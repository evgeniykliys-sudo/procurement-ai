"""Запись сцен ролика в кадры: Chrome CDP (127.0.0.1:9333) → video/frames/<id>/*.jpg + list.txt.

    PYTHONPATH=<путь к websocket-client> .venv/Scripts/python.exe video/record.py           # все сцены
    ... video/record.py --only s02_summary s04_sku                                          # выборочно
    ... video/record.py --desktop --only s09_claude_code                                    # захват экрана

Сначала нужен tts.py: длительность сцены и якоря действий («@слово») берутся из audio/<id>.json.

Время виртуальное: кадр k — это момент k/fps сцены; прокрутка и движение курсора вычисляются
для этого момента, поэтому ролик плавный при любой скорости снимков. Неподвижные кадры не
снимаются повторно — в list.txt (ffconcat) у кадра просто растёт длительность.

Браузер: своя вкладка в отдельном окне (Target.createTarget newWindow — фоновая вкладка не
рисуется), закрывается в конце. Другие вкладки не трогаются.
Сцены desktop без --desktop записываются карточкой-заглушкой; с --desktop — ffmpeg gdigrab
в frames/<id>/desktop.mp4 (build.py берёт его вместо заглушки).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import subprocess
import sys
import time
from pathlib import Path

VIDEO = Path(__file__).resolve().parent
ROOT = VIDEO.parent
sys.path.insert(0, str(VIDEO))

from facts import facts, render  # noqa: E402
from tts import AUDIO, ffmpeg, spoken, time_at_char  # noqa: E402

FRAMES = VIDEO / "frames"
LIVE = {"hash": 1.4, "click": 1.4, "eval": 0.8, "key": 0.8, "reveal": 0.7, "hl_on": 0.45, "hl_off": 0.45}
MOVE_S = 0.6  # движение курсора перед кликом

HELPER_JS = r"""
(function(){
if (window.__v) return;
function visible(e){ if(!e) return false; const r=e.getClientRects(); if(!r.length) return false;
  return !e.closest('[hidden]'); }
function q(sel){
  if (sel.indexOf('text=')===0){
    const t=sel.slice(5);
    const all=[...document.querySelectorAll('body *')].filter(e=>visible(e)&&(e.textContent||'').trim().indexOf(t)===0);
    return all.find(e=>!all.some(o=>o!==e&&e.contains(o))) || null;
  }
  const els=[...document.querySelectorAll(sel)].filter(visible);
  return els[0]||null;
}
function pick(sel, closest){ let e=q(sel); if(e&&closest){ e=e.closest(closest)||e; } return e; }
const cur=document.createElement('div');
cur.id='__cur';
cur.innerHTML='<svg width="34" height="40" viewBox="0 0 17 20"><path d="M1 1 L1 16 L5 12.5 L8 19 L10.5 18 L7.6 11.6 L13 11.6 Z" fill="#111" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg>';
cur.style.cssText='position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;transform:translate(1500px,700px);filter:drop-shadow(0 2px 3px rgba(0,0,0,.35));display:none';
document.documentElement.appendChild(cur);
const hls=new Map();
window.__v={
  q:q,
  cursorShow:function(on){ cur.style.display=on?'block':'none'; },
  cursorAt:function(x,y){ cur.style.transform='translate('+x+'px,'+y+'px)'; window.__cx=x; window.__cy=y; },
  center:function(sel,closest){ const e=pick(sel,closest); if(!e) return null; const r=e.getBoundingClientRect();
    return [r.left+Math.min(r.width/2, 160), r.top+r.height/2]; },
  scrollInfo:function(sel,to,offset,inSel){
    const box = inSel ? q(inSel) : null;
    const cur = box ? box.scrollTop : window.scrollY;
    const max = box ? box.scrollHeight-box.clientHeight : document.documentElement.scrollHeight-innerHeight;
    let target = to;
    if (sel){ const e=q(sel); if(!e) return null; const r=e.getBoundingClientRect();
      target = box ? r.top-box.getBoundingClientRect().top+box.scrollTop : r.top+window.scrollY; }
    target = Math.max(0, Math.min(max, target+(offset||0)));
    return [cur, target];
  },
  scrollTo:function(y,inSel){ const box=inSel?q(inSel):null; if(box) box.scrollTop=y; else window.scrollTo(0,y); },
  click:function(sel){ const e=q(sel); if(e){ e.click(); return true;} return false; },
  hl:function(sel,closest,on){
    const e=pick(sel,closest); if(!e) return false;
    if(on){ hls.set(sel, [e, e.style.outline, e.style.outlineOffset, e.style.boxShadow, e.style.transition]);
      e.style.transition='outline-color .35s, box-shadow .35s';
      e.style.outline='3px solid rgba(245,158,11,0)'; e.style.outlineOffset='4px';
      requestAnimationFrame(()=>{ e.style.outline='3px solid rgba(245,158,11,1)'; e.style.boxShadow='0 0 0 9999px rgba(15,23,42,0)'; });
    } else { const s=hls.get(sel); if(s){ const [el,o,oo,bs,tr]=s; el.style.outline='3px solid rgba(245,158,11,0)';
      setTimeout(()=>{ el.style.outline=o; el.style.outlineOffset=oo; el.style.boxShadow=bs; el.style.transition=tr; }, 400); hls.delete(sel);} }
    return true; },
  reveal:function(i){ const els=document.querySelectorAll('.rv'); if(els[i]) els[i].classList.add('on'); },
  revealAll:function(){ document.querySelectorAll('.rv').forEach(e=>e.classList.add('on')); },
  key:function(k){ document.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true})); }
};
})();
"""


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def file_url(p: Path, hash_: str = "") -> str:
    return p.resolve().as_uri() + (("#" + hash_) if hash_ else "")


def load_meta(sid: str) -> dict:
    p = AUDIO / f"{sid}.json"
    if not p.exists():
        raise SystemExit(f"нет {p.name} — сначала запусти video/tts.py")
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_at(at, meta: dict, dur: float) -> float:
    if isinstance(at, (int, float)):
        return float(at) * dur
    if isinstance(at, str) and at.startswith("@"):
        needle = spoken(at[1:]).lower()
        c = meta["spoken"].lower().find(needle)
        if c < 0:
            print(f"  ! якорь не найден: {at}")
            return 0.0
        return time_at_char(meta["words"], c)
    raise ValueError(f"at: {at!r}")


def scene_url(sc: dict, fx: dict, site_url: str) -> tuple[str, bool]:
    """URL страницы сцены и нужен ли курсор."""
    from pages import build_page
    t = sc["type"]
    if t == "browser":
        raw = sc["url"]
        path, _, h = raw.partition("#")
        if path.startswith("http"):
            return raw, sc.get("cursor", True)
        return file_url(ROOT / path, h), sc.get("cursor", True)
    html = render(sc["html"], fx) if sc.get("html") else None
    return file_url(build_page(sc, html, site_url)), False


def build_events(sc: dict, meta: dict, dur: float) -> list[dict]:
    ev = []
    for a in sc.get("actions", []):
        t = resolve_at(a.get("at", 0), meta, dur) + float(a.get("delay", 0))
        t = min(max(0.0, t), max(0.0, dur - 0.3))
        do = a["do"]
        if do in ("click", "cursor"):
            ev.append({"t": t, "kind": "move", "sel": a["selector"], "closest": a.get("closest"), "dur": MOVE_S})
            if do == "click":
                ev.append({"t": t + MOVE_S, "kind": "click", "sel": a["selector"]})
        elif do == "scroll":
            ev.append({"t": t, "kind": "scroll", "sel": a.get("selector"), "to": a.get("to", 0),
                       "offset": a.get("offset", 0), "in": a.get("in"), "dur": float(a.get("dur", 1.5))})
        elif do == "highlight":
            ev.append({"t": t, "kind": "hl_on", "sel": a["selector"], "closest": a.get("closest")})
            ev.append({"t": t + float(a.get("dur", 2.5)), "kind": "hl_off", "sel": a["selector"],
                       "closest": a.get("closest")})
        elif do == "hash":
            ev.append({"t": t, "kind": "hash", "value": a["value"]})
        elif do == "reveal":
            ev.append({"t": t, "kind": "reveal", "index": a["index"]})
        elif do == "eval":
            ev.append({"t": t, "kind": "eval", "js": a["js"]})
        elif do == "key":
            ev.append({"t": t, "kind": "key", "key": a["key"]})
        else:
            raise ValueError(f"{sc['id']}: неизвестное действие {do}")
    ev.sort(key=lambda e: e["t"])
    return ev


def js(s) -> str:
    return json.dumps(s, ensure_ascii=False)


def record_scene(tab, sc: dict, cfg: dict, fx: dict, site_url: str) -> int:
    meta = load_meta(sc["id"])
    dur = meta["duration"]
    fps = cfg["fps"]
    n_ticks = int(math.ceil(dur * fps))
    url, with_cursor = scene_url(sc, fx, site_url)
    out = FRAMES / sc["id"]
    if out.exists():
        for f in out.glob("*.jpg"):
            f.unlink()
    out.mkdir(parents=True, exist_ok=True)

    tab.navigate("about:blank")
    tab.navigate(url)
    time.sleep(1.3)  # графики ECharts дорисовываются
    tab.eval(HELPER_JS)
    tab.eval("__v.cursorAt(1500,700)")  # курсор появляется с первым движением
    events = build_events(sc, meta, dur)
    pending = list(events)
    active: list[dict] = []
    frames: list[list] = []  # [имя файла, число тиков]
    live_until, live_t0, live_wall = -1.0, 0.0, 0.0
    k = 0
    while k < n_ticks:
        tt = k / fps
        # 1) события, чьё время наступило
        while pending and pending[0]["t"] <= tt + 1e-9:
            e = pending.pop(0)
            kind = e["kind"]
            if kind == "move":
                c = tab.eval(f"__v.center({js(e['sel'])},{js(e.get('closest'))})")
                if c and with_cursor:
                    tab.eval("__v.cursorShow(true)")
                    x0 = tab.eval("window.__cx||1500")
                    y0 = tab.eval("window.__cy||700")
                    e.update(x0=x0, y0=y0, x1=c[0], y1=c[1], t0=tt)
                    active.append(e)
                continue
            if kind == "scroll":
                info = tab.eval(f"__v.scrollInfo({js(e.get('sel'))},{js(e.get('to', 0))},{js(e.get('offset', 0))},{js(e.get('in'))})")
                if info:
                    e.update(y0=info[0], y1=info[1], t0=tt)
                    active.append(e)
                else:
                    print(f"  ! не найдено для прокрутки: {e.get('sel')}")
                continue
            if kind == "click":
                if not tab.eval(f"__v.click({js(e['sel'])})"):
                    print(f"  ! не найдено для клика: {e['sel']}")
            elif kind == "hash":
                tab.eval(f"location.hash={js(e['value'])}")
            elif kind == "hl_on":
                if not tab.eval(f"__v.hl({js(e['sel'])},{js(e.get('closest'))},true)"):
                    print(f"  ! не найдено для подсветки: {e['sel']}")
            elif kind == "hl_off":
                tab.eval(f"__v.hl({js(e['sel'])},{js(e.get('closest'))},false)")
            elif kind == "reveal":
                tab.eval(f"__v.reveal({int(e['index'])})")
            elif kind == "eval":
                tab.eval(e["js"])
            elif kind == "key":
                tab.eval(f"__v.key({js(e['key'])})")
            live_until = max(live_until, tt + LIVE.get(kind, 0.8))
            live_t0, live_wall = tt, time.time()
        # 2) непрерывные: прокрутка и курсор — состояние на момент tt
        still = []
        for e in active:
            p = ease((tt - e["t0"]) / e["dur"]) if e["dur"] > 0 else 1.0
            if e["kind"] == "move":
                tab.eval(f"__v.cursorAt({e['x0'] + (e['x1'] - e['x0']) * p:.1f},{e['y0'] + (e['y1'] - e['y0']) * p:.1f})")
            else:
                tab.eval(f"__v.scrollTo({e['y0'] + (e['y1'] - e['y0']) * p:.1f},{js(e.get('in'))})")
            if tt - e["t0"] < e["dur"]:
                still.append(e)
        moving = bool(active)
        active = still
        # 3) кадр
        if k == 0 or moving or tt < live_until:
            name = f"{len(frames):05d}.jpg"
            tab.screenshot(str(out / name), quality=90)
            n = 1
            if not moving and tt < live_until:
                # анимации страницы идут в реальном времени: кадр покрывает прошедшее время
                elapsed = time.time() - live_wall
                n = max(1, int((live_t0 + elapsed - tt) * fps))
                if pending:
                    n = min(n, max(1, int(math.ceil((pending[0]["t"] - tt) * fps))))
            n = min(n, n_ticks - k)
            frames.append([name, n])
            k += n
        else:
            frames[-1][1] += 1
            k += 1
    lines = ["ffconcat version 1.0"]
    for name, n in frames:
        lines += [f"file '{name}'", f"duration {n / fps:.4f}"]
    lines.append(f"file '{frames[-1][0]}'")
    (out / "list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(frames)


def primary_screen() -> tuple[int, int]:
    try:
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def record_desktop(sc: dict, dur: float, region: str | None, countdown: int) -> Path:
    out = FRAMES / sc["id"]
    out.mkdir(parents=True, exist_ok=True)
    mp4 = out / "desktop.mp4"
    if region:
        x, y, w, h = (int(v) for v in region.split(","))
    else:
        (w, h), x, y = primary_screen(), 0, 0
    meta = load_meta(sc["id"])
    print(f"\n=== {sc['id']} · {dur:.1f} с · область {w}×{h} от ({x},{y})")
    print(f"В кадре: {sc.get('screen', '')}")
    print(f"Озвучка: {meta['text']}")
    for i in range(countdown, 0, -1):
        print(f"  запись через {i}…", flush=True)
        time.sleep(1)
    print("  ● идёт запись", flush=True)
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "gdigrab", "-framerate", "20",
           "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-draw_mouse", "1",
           "-i", "desktop", "-t", f"{dur:.2f}",
           "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(mp4)]
    subprocess.run(cmd, check=True)
    print(f"  готово → {mp4.relative_to(ROOT)}")
    return mp4


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Запись сцен ролика")
    ap.add_argument("--only", nargs="*", help="только эти id сцен")
    ap.add_argument("--desktop", action="store_true", help="записать сцены desktop захватом экрана (gdigrab)")
    ap.add_argument("--region", help="область захвата x,y,w,h (по умолчанию — основной монитор)")
    ap.add_argument("--countdown", type=int, default=5, help="отсчёт перед записью экрана, с")
    ap.add_argument("--port", type=int, default=9333, help="порт отладки Chrome")
    ap.add_argument("--site-url", default="https://evgeniykliys-sudo.github.io/procurement-ai/",
                    help="ссылка в дайджесте")
    args = ap.parse_args(argv)
    cfg = json.loads((VIDEO / "scenes.json").read_text(encoding="utf-8"))
    fx = facts()
    scenes = [s for s in cfg["scenes"] if not args.only or s["id"] in args.only]
    if args.desktop:
        for sc in scenes:
            if sc["type"] == "desktop":
                record_desktop(sc, load_meta(sc["id"])["duration"], args.region, args.countdown)
        return 0
    from cdp import Tab
    tab = Tab(port=args.port)
    try:
        tab.setup(cfg["width"], cfg["height"])
        for sc in scenes:
            t0 = time.time()
            n = record_scene(tab, sc, cfg, fx, args.site_url)
            print(f"{sc['id']:<20} {n:4d} кадров  {time.time() - t0:5.1f} с", flush=True)
    finally:
        tab.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
