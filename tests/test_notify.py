"""Дайджест в Telegram: HTTP подменён, значение токена нигде не печатается."""
import logging

import pytest
import requests

from procurement import notify

TOKEN = "123456789:AAH-secret-token-value_XYZ"
CHAT = "987654321"
SITE = "https://example.github.io/procurement-ai/"


def _item(sku, qty, status="order_now", supplier="SUP-0851EB", order_by="2026-08-13"):
    return {"sku_id": sku, "status": status, "order_qty": qty, "supplier_id": supplier, "order_by": order_by,
            "category": "space_heaters", "risk": []}


PLAN = {
    "as_of": "2026-08-13",
    "kpi": {"order_now_positions": 189, "order_now_qty": 43066, "order_now_suppliers": 18,
            "order_now_no_supplier": 2, "deficit_unavoidable": 205, "deficit_if_late": 16, "excess": 84,
            "review_price": 115, "dead": 331, "in_transit_future_qty": 17127, "late_lines": 5},
    "items": [_item("SKU-000001", 50), _item("SKU-000002", 6000), _item("SKU-000003", 1900),
              _item("SKU-000004", 700), _item("SKU-000005", 300), _item("SKU-000006", 200),
              _item("SKU-000007", 9999, status="order_later"), _item("SKU-<b>8", 10)],
}
DIFF = {"as_of": "2026-08-13", "prev_as_of": "2026-07-01",
        "new_orders": [{"sku_id": "SKU-000002", "qty": 6000}], "dropped_orders": [],
        "changed": [{"sku_id": "SKU-000003", "prev_qty": 900, "qty": 1900, "pct": 111.1}],
        "new_risks": [{"sku_id": "SKU-3C1D79", "risk": "deficit_if_late"}, {"sku_id": "SKU-3C1D79", "risk": "late_po"},
                      {"sku_id": "SKU-ABCDEF", "risk": "excess"}]}


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"ok": status < 400}
        self.ok = status < 400

    def json(self):
        return self._payload


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # чужой .env не подхватывается
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SITE_URL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def posts(env):
    log = []

    def install(response):
        def fake_post(url, **kw):
            log.append({"url": url, **kw})
            if isinstance(response, Exception):
                raise response
            return response
        env.setattr(requests, "post", fake_post)
        return log
    return install


def test_without_env_skips_and_logs(posts, caplog):
    log = posts(FakeResponse())
    with caplog.at_level(logging.INFO):
        assert notify.send_digest(PLAN, DIFF, SITE) is False
    assert log == []
    assert "TELEGRAM_BOT_TOKEN" in caplog.text


def test_sends_html_digest_with_kpi_top5_risks_and_link(posts, env):
    env.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    env.setenv("TELEGRAM_CHAT_ID", CHAT)
    log = posts(FakeResponse())
    assert notify.send_digest(PLAN, DIFF, SITE) is True
    (req,) = log
    assert req["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = req["json"]
    assert body["chat_id"] == CHAT and body["parse_mode"] == "HTML"
    text = body["text"]
    assert len(text) <= 4096
    assert "13.08.2026" in text
    for number in ("189", "43 066", "205", "84", "115", "331", "17 127"):
        assert number in text
    # топ-5 к заказу по количеству; позже и шестой — не в топе
    for sku in ("SKU-000002", "SKU-000003", "SKU-000004", "SKU-000005", "SKU-000006"):
        assert sku in text
    assert "SKU-000001" not in text and "SKU-000007" not in text
    assert text.index("SKU-000002") < text.index("SKU-000003") < text.index("SKU-000004")
    assert "SKU-3C1D79" in text and "SKU-ABCDEF" in text
    assert f'href="{SITE}"' in text
    assert "<b>8" not in text  # текст данных экранирован


def test_site_url_falls_back_to_env(posts, env):
    env.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    env.setenv("TELEGRAM_CHAT_ID", CHAT)
    env.setenv("SITE_URL", SITE)
    log = posts(FakeResponse())
    assert notify.send_digest(PLAN, DIFF, "") is True
    assert f'href="{SITE}"' in log[0]["json"]["text"]


@pytest.mark.parametrize("response", [
    requests.ConnectionError(f"HTTPSConnectionPool url: /bot{TOKEN}/sendMessage"),
    requests.Timeout(f"read timeout /bot{TOKEN}/sendMessage"),
    FakeResponse(401, {"ok": False, "description": f"Unauthorized bot{TOKEN}"}),
])
def test_failure_returns_false_and_never_prints_token(posts, env, caplog, capsys, response):
    env.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    env.setenv("TELEGRAM_CHAT_ID", CHAT)
    posts(response)
    with caplog.at_level(logging.DEBUG):
        assert notify.send_digest(PLAN, DIFF, SITE) is False
    out = capsys.readouterr()
    assert caplog.text.strip()  # причина записана
    assert TOKEN not in caplog.text + out.out + out.err
    assert "AAH-secret" not in caplog.text + out.out + out.err
