"""HTML-страницы для сцен code / title / digest / desktop-заглушки → video/pages/<id>.html.

Файлы репозитория читаются в момент генерации (record.py вызывает build_page перед записью сцены),
поэтому в кадре всегда текущая версия кода.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_for_filename

VIDEO = Path(__file__).resolve().parent
ROOT = VIDEO.parent
PAGES = VIDEO / "pages"

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1920px;min-height:1080px;background:#0f1722;color:#e6edf3;
  font-family:'Segoe UI',system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.rv{opacity:0;transform:translateY(14px);transition:opacity .45s ease,transform .45s ease}
.rv.on{opacity:1;transform:none}
"""

TITLE_CSS = """
body{display:flex;flex-direction:column;justify-content:center;padding:0 160px 120px;height:1080px;
  background:radial-gradient(1200px 700px at 80% 10%,#1d3557 0%,#0f1722 60%)}
.eyebrow{color:#7fb0ff;font-size:24px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;margin-bottom:22px}
h1{font-size:76px;line-height:1.08;font-weight:700;margin-bottom:22px}
h2{font-size:58px;line-height:1.12;font-weight:700;margin-bottom:40px;max-width:1500px}
.lead{font-size:34px;color:#b8c4d3;margin-bottom:60px}
.cols3{display:grid;grid-template-columns:repeat(3,1fr);gap:28px}
.box{background:#16212f;border:1px solid #2a3a50;border-radius:16px;padding:30px 32px}
.box b{display:block;font-size:34px;margin-bottom:10px;color:#fff}
.box span{font-size:25px;color:#aab6c5;line-height:1.35}
.flow{display:flex;align-items:stretch;gap:18px;margin-bottom:44px}
.flow .node{flex:1;background:#16212f;border:1px solid #2a3a50;border-radius:16px;padding:26px 24px}
.flow .node b{display:block;font-family:Consolas,'Cascadia Mono',monospace;font-size:36px;color:#7fb0ff;margin-bottom:10px}
.flow.small .node b{font-family:'Segoe UI',sans-serif;font-size:31px;color:#fff}
.flow .node span{font-size:24px;color:#b8c4d3;line-height:1.35}
.flow .arr{display:flex;align-items:center;font-size:46px;color:#4f6a8f}
.fan{display:flex;align-items:center;gap:30px}
.fan .json{font-family:Consolas,monospace;font-size:34px;background:#1f3b63;border-radius:12px;padding:20px 28px;color:#fff}
.fan .outs{display:flex;flex-wrap:wrap;gap:14px}
.fan .outs span{font-size:25px;background:#16212f;border:1px solid #2a3a50;border-radius:10px;padding:12px 18px;color:#cfd8e3}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.dcard{background:#16212f;border:1px solid #2a3a50;border-radius:16px;padding:26px 28px}
.dcard i{font-style:normal;font-family:Consolas,monospace;color:#f5b84b;font-size:26px;display:block;margin-bottom:8px}
.dcard b{display:block;font-size:31px;margin-bottom:10px}
.dcard span{font-size:23px;color:#b8c4d3;line-height:1.38}
.dcard.big{grid-column:1/-1;background:#1b2d22;border-color:#2f5a3c}
.dcard.big b{font-size:44px;color:#8fe3a6}
.list{list-style:none;margin-bottom:50px}
.list li{font-size:34px;color:#dbe3ec;padding:12px 0 12px 44px;position:relative}
.list li:before{content:'';position:absolute;left:0;top:28px;width:16px;height:16px;border-radius:4px;background:#7fb0ff}
.links{display:flex;gap:30px}
.links span{font-family:Consolas,monospace;font-size:28px;background:#16212f;border:1px solid #2a3a50;border-radius:10px;padding:14px 22px;color:#9cc3ff}
.ph{border:3px dashed #3b5170;border-radius:24px;padding:80px;text-align:center}
.ph .eyebrow{margin-bottom:30px}
.ph h2{margin:0 auto 30px;font-size:52px}
.ph p{font-size:28px;color:#9fb0c4;max-width:1300px;margin:0 auto;line-height:1.4}
"""

CODE_CSS = """
body{padding:44px 90px 140px}
.head{display:flex;align-items:baseline;gap:24px;margin-bottom:22px}
.head .path{font-family:Consolas,'Cascadia Mono',monospace;font-size:30px;color:#9cc3ff;font-weight:600}
.head .sub{font-size:22px;color:#8b98a8}
.panel{background:#0d1117;border:1px solid #263244;border-radius:14px;padding:22px 0;overflow:hidden}
.fname{font-family:Consolas,monospace;font-size:19px;color:#8b98a8;padding:4px 28px 14px;border-bottom:1px solid #1f2937;margin-bottom:10px}
.highlight{background:transparent!important}
.highlight pre{font-family:Consolas,'Cascadia Mono',monospace;font-size:21px;line-height:1.5;padding:0 28px;white-space:pre}
.wrap .highlight pre{white-space:pre-wrap;word-break:break-word}
.highlight .hll{background:#2b3a1f!important;display:block;margin:0 -28px;padding:0 28px;box-shadow:inset 4px 0 0 #9be564}
.linenos{color:#4b5768!important}
td.linenos .normal{color:#4b5768;padding-right:18px;font-family:Consolas,monospace;font-size:21px;line-height:1.5}
table.highlighttable{border-collapse:collapse}
.gap{height:26px}
"""

DIGEST_CSS = """
body{background:#0e1621;padding:60px 0 200px;display:flex;flex-direction:column;align-items:center}
.chat{width:1000px}
.who{display:flex;align-items:center;gap:18px;margin-bottom:28px}
.ava{width:64px;height:64px;border-radius:50%;background:#3a6df0;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700}
.who b{font-size:30px}.who span{display:block;font-size:22px;color:#7f91a4}
.bubble{background:#182533;border-radius:18px 18px 18px 6px;padding:26px 32px;font-size:26px;line-height:1.5;color:#e9eef3;white-space:pre-wrap}
.bubble a{color:#6ab3f3}
.note{margin-top:22px;font-size:20px;color:#6d7f92}
"""


def _doc(body: str, css: str, title: str = "") -> str:
    return (f"<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{BASE_CSS}{css}</style></head><body>{body}</body></html>")


def _lexer(path: Path):
    try:
        return get_lexer_for_filename(path.name)
    except Exception:
        return TextLexer()


def _start_line(lines: list[str], start) -> int:
    if isinstance(start, int):
        return max(1, start)
    for i, s in enumerate(lines, 1):
        if start in s:
            return i
    return 1


def _snippet(path: Path, start, count: int, hl: list[str]) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    s0 = _start_line(lines, start)
    chunk = lines[s0 - 1:s0 - 1 + count]
    hl_lines = [i for i, s in enumerate(chunk, 1) if any(p in s for p in hl)]
    fmt = HtmlFormatter(style="github-dark", linenos="table", linenostart=s0, hl_lines=hl_lines)
    return highlight("\n".join(chunk) + "\n", _lexer(path), fmt), fmt.get_style_defs(".highlight")


def code_page(sc: dict) -> str:
    files = []
    if sc.get("prepend_file"):
        files.append((sc["prepend_file"], 1, 20, []))
    files.append((sc["file"], sc.get("start", 1), sc.get("lines", 60), sc.get("hl", [])))
    parts, style = [], ""
    for rel, start, count, hl in files:
        snip, style = _snippet(ROOT / rel, start, count, hl)
        parts.append(f"<div class='panel'><div class='fname'>{html.escape(rel)}</div>{snip}</div>")
    body = (f"<div class='head'><span class='path'>{html.escape(sc.get('title', sc['file']))}</span></div>"
            + "<div class='gap'></div>".join(parts))
    cls = " class='wrap'" if sc.get("wrap") else ""
    return _doc(f"<div{cls}>{body}</div>", CODE_CSS + style, sc.get("title", ""))


def title_page(sc: dict, html_body: str) -> str:
    return _doc(html_body, TITLE_CSS, sc["id"])


def placeholder_page(sc: dict) -> str:
    body = ("<div class='ph'><div class='eyebrow'>запись экрана · сцена " + html.escape(sc["id"]) + "</div>"
            f"<h2>{html.escape(sc.get('placeholder', 'Здесь будет запись Claude Code'))}</h2>"
            f"<p>{html.escape(sc.get('screen', ''))}</p></div>")
    return _doc(body, TITLE_CSS, sc["id"])


def digest_page(sc: dict, site_url: str) -> str:
    sys.path.insert(0, str(ROOT))
    from procurement.notify import build_digest  # текущая версия модуля
    plan = json.loads((ROOT / "out" / "plan.json").read_text(encoding="utf-8"))
    diff = json.loads((ROOT / "out" / "diff.json").read_text(encoding="utf-8"))
    text = build_digest(plan, diff, site_url)
    # Telegram HTML (b, i, a, code) — показываем как есть, переносы строк — pre-wrap
    body = ("<div class='chat'><div class='who'><div class='ava'>П</div><div><b>План закупок</b>"
            "<span>бот · дайджест после прогона</span></div></div>"
            f"<div class='bubble'>{text}</div>"
            "<div class='note'>Текст — procurement.notify.build_digest по текущему out/plan.json</div></div>")
    return _doc(body, DIGEST_CSS, sc["id"])


def build_page(sc: dict, rendered_html: str | None = None, site_url: str = "") -> Path:
    PAGES.mkdir(parents=True, exist_ok=True)
    t = sc["type"]
    if t == "code":
        doc = code_page(sc)
    elif t == "title":
        doc = title_page(sc, rendered_html or "")
    elif t == "digest":
        doc = digest_page(sc, site_url)
    elif t == "desktop":
        doc = placeholder_page(sc)
    else:
        raise ValueError(f"нет страницы для типа {t}")
    out = PAGES / f"{sc['id']}.html"
    out.write_text(doc, encoding="utf-8")
    return out

