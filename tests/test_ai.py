"""ИИ-аналитик: вход для LLM, запрос к DeepSeek (HTTP подменён), кеш, вычистка выдуманных SKU."""
import json

import pytest
import requests

from procurement import ai
from procurement.config import Config

KEY = "sk-test-0123456789abcdef"


def _item(i, status="order_now", risk=("deficit_unavoidable",), qty=100):
    return {
        "sku_id": f"SKU-{i:06X}", "category": "space_heaters", "supplier_id": "SUP-000001", "abc": "A",
        "status": status, "order_qty": qty, "order_by": "2026-08-13", "stockout_date": "2026-09-01",
        "cover_days": 19, "stock_now": 10, "in_transit_qty": 0, "next_eta": None, "lead_days": 123,
        "lead_source": "sku", "risk": list(risk), "flags": ["скоро кончится"], "markup_12m": 40.0,
        "sales_12m": 500, "forecast_12m": 600.0,
        "explain": {"forecast_horizon": 72.3, "safety": 51.2, "stock_at_arrival": 0.0, "transit": 0,
                    "gross": 123.5, "uplift": 1.0, "lot": 50, "lot_source": "партия", "final": qty,
                    "stock": 10, "transit_lead": 0, "forecast_lead": 132.3, "at": "2026-08-13",
                    "arrival": "2026-12-14", "horizon_days": 153, "z": 1.64, "sigma_monthly": 21.4,
                    "days_short": 90, "lost_lead": 40.0, "excess_qty": 0.0, "cap": 600},
    }


def _plan(n=800):
    items = [_item(i, qty=100 + i) for i in range(n)]
    return {"as_of": "2026-08-13", "kpi": {"order_now_positions": n, "order_now_qty": 43066},
            "items": items, "calendar": [{"month": f"2026-{m:02d}", "positions": 3, "qty": 50} for m in range(1, 7)],
            "stale_po": [], "reliability": [], "supplier_orders": []}


def _diff(n=200):
    return {"as_of": "2026-08-13", "prev_as_of": "2026-07-01",
            "new_orders": [{"sku_id": f"SKU-{i:06X}", "qty": 10, "category": "c", "supplier_id": "S"} for i in range(n)],
            "dropped_orders": [{"sku_id": f"SKU-{i:06X}", "prev_qty": 5, "status": "ok"} for i in range(n)],
            "changed": [{"sku_id": f"SKU-{i:06X}", "prev_qty": 5, "qty": 9, "pct": 80.0} for i in range(n)],
            "new_risks": [{"sku_id": f"SKU-{i:06X}", "risk": "excess"} for i in range(n)]}


BACKTEST = {"by_category": [{"category": "space_heaters", "wape_model": 0.56, "wape_naive": 0.98,
                             "bias_model": 0.1, "bias_naive": -0.2}],
            "total": {"wape_model": 0.44, "wape_naive": 0.83}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Чистое окружение: без ключа в переменных и без .env в текущей папке."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return monkeypatch


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


def _llm(content):
    return FakeResponse({"choices": [{"message": {"content": content}}]})


@pytest.fixture
def calls(env):
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


def _summary():
    return ai.build_summary(_plan(40), _diff(5), BACKTEST)


def test_prompt_file_sets_role_rules_and_json_format():
    text = ai.PROMPT_PATH.read_text(encoding="utf-8")
    for part in ("аналитик", "только числа из входа", '"markdown"', '"sku_notes"', "Главное",
                 "Что изменилось с прошлого прогона", "Риски", "Что сделать сегодня", "JSON"):
        assert part in text


def test_summary_is_compact_even_for_large_plan():
    s = ai.build_summary(_plan(800), _diff(200), BACKTEST)
    text = json.dumps(s, ensure_ascii=False)
    assert len(text) / 3 <= 8000  # ≈ 3 символа на токен — с запасом
    assert len(s["top_risks"]) == 15
    assert all("explain" in r and r["explain"]["final"] == r["order_qty"] for r in s["top_risks"])
    assert s["kpi"]["order_now_qty"] == 43066


def test_summary_speaks_dashboard_language():
    """Записка не должна расходиться с дашбордом: русские подписи, проценты, WAPE по SKU, объединённый заказ."""
    plan = _plan(3)
    merged = plan["items"][0]
    merged["explain"].update(gross=1870.0, lot=100, lot_source="шаг", final=3800, merged_until_review=1900)
    merged["order_qty"] = 3800
    plan["reliability"] = [{"supplier_id": "SUP-000001", "fill_ratio": 0.7289, "uplift": 1.2, "orders_n": 8},
                           {"supplier_id": "SUP-000002", "fill_ratio": 0.9, "uplift": 1.0, "orders_n": 3}]
    bt = {"by_category": [{"category": "grain_mills", "wape_model": 0.4615, "wape_naive": 0.4251,
                           "wape_sku_model": 0.5749, "wape_sku_naive": 0.5251, "bias_model": -0.3661,
                           "bias_naive": -0.3677}],
          "total": {"wape_model": 0.408, "wape_naive": 0.6737, "wape_sku_model": 0.656, "wape_sku_naive": 0.8604,
                    "bias_model": -0.071, "bias_naive": -0.035, "actual": 1.0}}
    diff = _diff(1)
    diff["new_orders"][0]["category"] = "space_heaters"
    diff["new_risks"][0]["risk"] = "deficit_unavoidable"
    s = ai.build_summary(plan, diff, bt)
    top = next(r for r in s["top_risks"] if r["sku_id"] == merged["sku_id"])
    assert top["explain"]["merged_until_review"] == 1900 and top["explain"]["final"] == top["order_qty"] == 3800
    assert top["explain"]["final_before_merge"] == 1900  # округлённая потребность — готовым числом
    assert "cap" not in top["explain"]  # потолок — только там, где сработал
    assert top["category"] == "Обогреватели" and top["status"] == "Заказать сейчас"
    assert top["risk"] == ["Дефицит неизбежен"]
    assert s["diff"]["new_orders"][0]["category"] == "Обогреватели"
    assert s["diff"]["new_risks"][0]["risk"] == "Дефицит неизбежен"
    assert s["backtest"]["total"] == {"wape_sku_model": 66, "wape_sku_naive": 86, "wape_model": 41,
                                      "wape_naive": 67, "bias_model": -7, "bias_naive": -4}
    assert s["backtest"]["by_category"][0]["category"] == "Зернодробилки"
    assert s["backtest"]["by_category"][0]["wape_sku_model"] == 57
    assert s["low_reliability"] == [{"supplier_id": "SUP-000001", "fill_pct": 73, "orders_n": 8}]
    text = ai.PROMPT_PATH.read_text(encoding="utf-8")
    for part in ("merged_until_review", "wape_sku_model", "fill_pct", "не пиши в тексте названия полей"):
        assert part in text


def test_no_key_returns_reason_without_request(calls, tmp_path):
    log = calls(_llm("{}"))
    note = ai.analyst_note(_summary(), Config(), tmp_path / "cache")
    assert note["ok"] is False and note["markdown"] == "" and note["sku_notes"] == {}
    assert "DEEPSEEK_API_KEY" in note["error"]
    assert log == []


def test_key_from_dotenv_file_is_used(calls, tmp_path):
    (tmp_path / ".env").write_text(f"DEEPSEEK_API_KEY={KEY}\nTELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    log = calls(_llm(json.dumps({"markdown": "## Главное\nВсё хорошо.", "sku_notes": {}})))
    note = ai.analyst_note(_summary(), Config(), tmp_path / "cache")
    assert note["ok"] is True
    assert log[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_success_request_shape_and_invented_skus_removed(calls, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    summary = _summary()
    real = summary["top_risks"][0]["sku_id"]
    content = json.dumps({
        "markdown": f"## Главное\n- Срочно {real}: заказать {summary['top_risks'][0]['order_qty']} шт.\n"
                    "- Срочно SKU-FAKE99: заказать 5 шт.\n## Риски\nМало.",
        "sku_notes": {real: "Остаток кончится до прихода.", "SKU-FAKE99": "Выдумано."},
    }, ensure_ascii=False)
    log = calls(_llm(content))
    cfg = Config(llm_timeout_s=42, llm_model="deepseek-chat", llm_base_url="https://api.example.test/")
    note = ai.analyst_note(summary, cfg, tmp_path / "cache")
    assert note["ok"] is True and note["error"] is None and note["model"] == "deepseek-chat"
    assert note["sku_notes"] == {real: "Остаток кончится до прихода."}
    assert real in note["markdown"] and "SKU-FAKE99" not in note["markdown"]
    assert "## Риски" in note["markdown"]
    (req,) = log
    assert req["url"] == "https://api.example.test/chat/completions"
    assert req["timeout"] == 42
    assert req["headers"]["Authorization"] == f"Bearer {KEY}"
    body = req["json"]
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "Главное" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == json.loads(json.dumps(summary, ensure_ascii=False))


def test_answer_is_cached_by_input_hash(calls, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    log = calls(_llm(json.dumps({"markdown": "## Главное\nТекст.", "sku_notes": {}})))
    cache = tmp_path / "cache"
    first = ai.analyst_note(_summary(), Config(), cache)
    second = ai.analyst_note(_summary(), Config(), cache)
    assert first == second and first["ok"] is True
    assert len(log) == 1
    other = ai.build_summary(_plan(41), _diff(5), BACKTEST)
    ai.analyst_note(other, Config(), cache)
    assert len(log) == 2


@pytest.mark.parametrize("response, words", [
    (requests.Timeout(f"timed out bot{KEY}"), ["не ответил", "60"]),
    (requests.ConnectionError(f"Max retries Bearer {KEY}"), ["связ"]),
    (FakeResponse({"error": {"message": "Authentication Fails"}}, status=401), ["401"]),
    (_llm("это не JSON"), ["JSON"]),
    (_llm(json.dumps({"sku_notes": {}})), ["записк"]),
    (_llm([{"type": "text", "text": "{}"}]), ["формате"]),  # content — список частей: не падать на .strip()
])
def test_failures_return_reason_in_russian_and_never_the_key(calls, tmp_path, monkeypatch, capsys, caplog,
                                                            response, words):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    calls(response)
    note = ai.analyst_note(_summary(), Config(), tmp_path / "cache")
    assert note["ok"] is False and note["markdown"] == "" and note["sku_notes"] == {}
    for w in words:
        assert w in note["error"]
    out = capsys.readouterr()
    assert KEY not in note["error"] + out.out + out.err + caplog.text
    assert not list((tmp_path / "cache").glob("*.json"))  # ошибки не кешируются


def test_note_mentioning_unknown_sku_is_dropped():
    """Пояснение к SKU из входа, в тексте которого выдуманный SKU, в карточку не попадает."""
    _, notes = ai._clean("## Главное\nok", {"SKU-AAA111": "как у SKU-FAKE99 — заказать 500",
                                            "SKU-BBB222": "в норме"}, {"SKU-AAA111", "SKU-BBB222"})
    assert notes == {"SKU-BBB222": "в норме"}


def test_bridge_sent_to_llm_only_when_it_sets_the_need():
    """`bridge` (разрыв до партии в пути, D14) — во входе ИИ только там, где он определил потребность:
    0 при окне −593.9 модель описала бы как дефицит."""
    plan = _plan(2)
    plan["items"][0]["explain"].update(gross_window=-593.9, bridge=0.0, gross=-593.9)
    plan["items"][1]["explain"].update(gross_window=-20.0, bridge=97.0, gross=97.0)
    s = ai.build_summary(plan, _diff(1), BACKTEST)
    top = {r["sku_id"]: r["explain"] for r in s["top_risks"]}
    a, b = top[plan["items"][0]["sku_id"]], top[plan["items"][1]["sku_id"]]
    assert "bridge" not in a and "gross_window" not in a
    assert b["bridge"] == 97.0 and b["gross_window"] == -20.0


def test_invented_numbers_dates_and_suppliers_are_dropped():
    """Строка с числом, датой или поставщиком не из входа — досчитано или выдумано — вычищается;
    числа входа в любом написании («34 945», «0,5», «66%», 372,5 → «373»), даты ДД.ММ.ГГГГ и
    небольшие целые (дни, «2 позиции») остаются."""
    src = {"kpi": {"order_now_qty": 34945, "share": 0.5, "pct": 66}, "as_of": "2026-08-13",
           "top_risks": [{"sku_id": "SKU-AAA111", "supplier_id": "SUP-111", "explain": {"safety": 372.5}}]}
    md = "\n".join([
        "## Главное",
        "- К заказу 34 945 шт., доля 0,5, ошибка 66%; данные на 13.08.2026, 2 позиции срочно.",
        "- По SKU-AAA111 страховой 373 шт., поставщик SUP-111.",
        "- Итого с прошлым — 35 000 шт.",          # досчитано
        "- Кончится 15.09.2026.",                    # даты нет во входе
        "- Связаться с SUP-999.",                    # поставщика нет во входе
    ])
    allowed = {"SKU-AAA111", "SUP-111"}
    out, kept = ai._clean(md, {"SKU-AAA111": "страховой 1 234 шт."}, allowed, source=src)
    assert out.splitlines() == md.splitlines()[:3]
    assert kept == {}
    _, kept = ai._clean(md, {"SKU-AAA111": "страховой 373 шт.", "SKU-BBB": "x"}, allowed, source=src)
    assert kept == {"SKU-AAA111": "страховой 373 шт."}


@pytest.mark.parametrize("text, bad", [
    ("рост в 1.2 раза, 66.1 шт. в месяц", []),      # дроби из входа — не даты
    ("срок 2026-08-13", []),
    ("срок 2026-12-31", ["2026-12-31"]),          # ISO-дата сверяется целиком
    ("падение -37%", []),
    ("снижение на 37%", []),                      # знак словом
    ("рост -45%", ["-45"]),
    ("-1.2", ["1.2"]),                            # знак у дроби, похожей на дату
    ("срок 13.08", []),
    ("срок 14.08", ["14.08"]),
    ("диапазон 10-20", []),                       # дефис диапазона — не минус
    ("1.3 раза", ["1.3"]),
])
def test_unknown_numbers_counterexamples(text, bad):
    from procurement.ai import _numbers_of, _unknown_numbers
    nums, dates = set(), set()
    _numbers_of({"a": 1.2, "b": 66.1, "c": -37.0, "eta": "2026-08-13"}, nums, dates)
    assert _unknown_numbers(text, nums, dates) == bad


def test_note_emptied_by_check_is_not_ok_and_not_cached(calls, tmp_path, monkeypatch):
    """Все строки записки вычищены (выдуманный код/число): записки нет — ok False, в кеш не пишется."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    log = calls(_llm(json.dumps({"markdown": "Закупить SKU-NOTREAL 987654 шт.", "sku_notes": {}})))
    cache = tmp_path / "cache"
    note = ai.analyst_note(_summary(), Config(), cache)
    assert note["ok"] is False and "не осталось" in note["error"]
    assert not cache.exists() or not any(cache.iterdir())
    ai.analyst_note(_summary(), Config(), cache)
    assert len(log) == 2  # не из кеша
