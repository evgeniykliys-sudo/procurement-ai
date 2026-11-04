"""Минимальный клиент Chrome DevTools Protocol для записи сцен.

Работает только со своей вкладкой: создаёт её в отдельном окне (Target.createTarget, newWindow —
фоновая вкладка не отрисовывается и снимок зависает) и закрывает в конце.
Чужие вкладки браузера не трогаются. Нужен пакет websocket-client (PYTHONPATH или venv).
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request

import websocket


class CDPError(RuntimeError):
    pass


class Tab:
    """Своя вкладка Chrome: открыть, управлять, закрыть."""

    def __init__(self, port: int = 9333, host: str = "127.0.0.1"):
        self.host, self.port = host, port
        ver = json.load(urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=5))
        self.browser = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=30,
                                                   suppress_origin=True)
        self._id = 0
        res = self._call(self.browser, "Target.createTarget", {"url": "about:blank", "newWindow": True, "background": False})
        self.target_id = res["targetId"]
        self.ws = websocket.create_connection(
            f"ws://{host}:{port}/devtools/page/{self.target_id}", timeout=60, suppress_origin=True)
        self.events: list[dict] = []

    # --- транспорт ---------------------------------------------------------------------------
    def _call(self, ws, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg and ws is getattr(self, "ws", None):
                self.events.append(msg)

    def call(self, method: str, params: dict | None = None) -> dict:
        return self._call(self.ws, method, params)

    # --- удобства ----------------------------------------------------------------------------
    def setup(self, width: int, height: int) -> None:
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
        self.call("Emulation.setScrollbarsHidden", {"hidden": True})

    def add_init_script(self, source: str) -> None:
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})

    def navigate(self, url: str, timeout: float = 20) -> None:
        self.events.clear()
        self.call("Page.navigate", {"url": url})
        t0 = time.time()
        while time.time() - t0 < timeout:
            if any(e.get("method") == "Page.loadEventFired" for e in self.events):
                return
            try:
                self.ws.settimeout(0.5)
                msg = json.loads(self.ws.recv())
                if "method" in msg:
                    self.events.append(msg)
            except websocket.WebSocketTimeoutException:
                pass
            finally:
                self.ws.settimeout(60)
        raise CDPError(f"страница не загрузилась за {timeout} с: {url}")

    def eval(self, expr: str):
        res = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in res:
            raise CDPError(f"JS: {res['exceptionDetails'].get('exception', {}).get('description', res['exceptionDetails'])}")
        return res.get("result", {}).get("value")

    def screenshot(self, path: str, quality: int = 88) -> None:
        res = self.call("Page.captureScreenshot", {"format": "jpeg", "quality": quality, "fromSurface": True})
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self._call(self.browser, "Target.closeTarget", {"targetId": self.target_id})
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
