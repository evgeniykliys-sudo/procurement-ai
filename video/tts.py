"""Озвучка сцен: edge-tts → video/audio/<id>.mp3 + <id>.json (слова с таймингами, реплики субтитров).

    .venv/Scripts/python.exe video/tts.py            # озвучить изменившиеся сцены, обновить script.md
    .venv/Scripts/python.exe video/tts.py --force    # переозвучить всё
    .venv/Scripts/python.exe video/tts.py --only s02_summary

Текст сцены — из scenes.json, подстановки {…} — из facts.py (свежий out/plan.json).
Субтитры показывают текст как написан; в озвучку он идёт через словарь произношения SAY.
Длительность сцены = длительность озвучки + tail (0,5 с) → audio/durations.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import edge_tts
import imageio_ffmpeg

VIDEO = Path(__file__).resolve().parent
sys.path.insert(0, str(VIDEO))
from facts import facts, render  # noqa: E402

AUDIO = VIDEO / "audio"
MAX_CUE = 86  # символов в реплике субтитра (≈ две строки)

# Как произносить то, что в субтитрах пишется латиницей. Порядок важен: длинное раньше.
SAY: list[tuple[str, str]] = [
    (r"Claude Code", "Клод Код"),
    (r"CLAUDE\.md", "клод эм-дэ"),
    (r"AGENTS\.md", "эйджентс эм-дэ"),
    (r"README", "ридми"),
    (r"GitHub Actions", "Гитхаб Экшнс"),
    (r"GitHub Pages", "Гитхаб Пейджес"),
    (r"GitHub", "Гитхаб"),
    (r"DeepSeek", "Дипсик"),
    (r"Python", "Пайтон"),
    (r"Run workflow", "Ран воркфлоу"),
    (r"Actions", "Экшнс"),
    (r"config\.toml", "конфиг-томл"),
    (r"plan\.json", "план-джейсон"),
    (r"plan\.py", "план-пай"),
    (r"need_at", "нид эт"),
    (r"\.autopilot", "автопилот"),
    (r"/autopilot", "автопилот"),
    (r"/grilling", "грилинг"),
    (r"/zakupki", "закупки"),
    (r"\bJSON\b", "джейсон"),
    (r"\bCSV\b", "си-эс-ви"),
    (r"\bERP\b", "и-ар-пи"),
    (r"\bSKU\b", "эс-кей-ю"),
    (r"\bKPI\b", "кей-пи-ай"),
    (r"\bExcel\b", "эксель"),
    (r"\bexplain\b", "эксплейн"),
    (r"\bdata\b", "дата"),
    (r"\bdemand\b", "диманд"),
    (r"\bsupply\b", "саплай"),
    (r"\bplan\b", "план"),
    (r"\bdeploy\b", "деплой"),
    (r"\bnotify\b", "нотифай"),
    (r"\bprocurement\b", "прокьюрмент"),
    (r"\bD0(\d)–D0(\d)\b", r"дэ-ноль-\1 — дэ-ноль-\2"),
    (r"\bD0(\d)\b", r"дэ-ноль-\1"),
]


def spoken(text: str) -> str:
    s = text
    for pat, rep in SAY:
        s = re.sub(pat, rep, s)
    s = re.sub(r"(\d) (\d{3})\b", r"\1\2", s)   # «42 796» → «42796», иначе читается как два числа
    s = s.replace("«", "").replace("»", "")
    return s


def cues_of(text: str) -> list[str]:
    """Текст → реплики субтитров: по предложениям, длинные — по запятым/тире, затем по словам."""
    out: list[str] = []
    for sent in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if len(sent) <= MAX_CUE:
            out.append(sent)
            continue
        parts = re.split(r"(?<=[,:;])\s+|\s+(?=— )", sent)
        cur = ""
        for p in parts:
            if cur and len(cur) + 1 + len(p) > MAX_CUE:
                out.append(cur)
                cur = p
            else:
                cur = f"{cur} {p}".strip()
        if cur:
            out.append(cur)
    final: list[str] = []
    for c in out:  # всё ещё длинные — по словам пополам
        while len(c) > MAX_CUE + 20:
            words = c.split()
            acc, i = 0, 0
            while i < len(words) and acc + len(words[i]) < len(c) / 2:
                acc += len(words[i]) + 1
                i += 1
            final.append(" ".join(words[:i]))
            c = " ".join(words[i:])
        final.append(c)
    return [c for c in final if c]


def ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def media_duration(path: Path) -> float:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise RuntimeError(f"не удалось узнать длительность {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


async def _synth(text: str, voice: str, rate: str, mp3: Path) -> list[dict]:
    words: list[dict] = []
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"t": ch["offset"] / 1e7, "d": ch["duration"] / 1e7, "text": ch["text"]})
    return words


def synth(text: str, voice: str, rate: str, mp3: Path, attempts: int = 8) -> list[dict]:
    last = None
    for k in range(attempts):
        try:
            return asyncio.run(_synth(text, voice, rate, mp3))
        except Exception as e:  # edge-tts иногда отвечает NoAudioReceived — повторяем
            last = e
            time.sleep(1.5 + k * 1.5)
    raise RuntimeError(f"edge-tts не ответил за {attempts} попыток: {type(last).__name__}")


def align(spoken_text: str, words: list[dict]) -> list[dict]:
    """Позиция каждого слова-границы в тексте озвучки (символ), чтобы привязать реплики и якоря."""
    pos, low = 0, spoken_text.lower()
    for w in words:
        i = low.find(w["text"].lower(), pos)
        if i >= 0:
            w["c"] = i
            pos = i + len(w["text"])
        else:
            w["c"] = pos
    return words


def time_at_char(words: list[dict], c: int) -> float:
    for w in words:
        if w["c"] >= c:
            return w["t"]
    return words[-1]["t"] + words[-1]["d"] if words else 0.0


def build_scene(sc: dict, cfg: dict, fx: dict, force: bool) -> dict:
    AUDIO.mkdir(parents=True, exist_ok=True)
    text = render(sc["text"], fx)
    cues = cues_of(text)
    say_parts = [spoken(c) for c in cues]
    say = " ".join(say_parts)
    key = hashlib.sha1(f"{cfg['voice']}|{cfg['rate']}|{say}".encode("utf-8")).hexdigest()
    mp3, meta_p = AUDIO / f"{sc['id']}.mp3", AUDIO / f"{sc['id']}.json"
    if not force and meta_p.exists() and mp3.exists():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        if meta.get("key") == key:
            return meta
    words = align(say, synth(say, cfg["voice"], cfg["rate"], mp3))
    dur = media_duration(mp3)
    # реплики: начало — первое слово реплики, конец — начало следующей (или конец речи)
    starts, c = [], 0
    for part in say_parts:
        starts.append(time_at_char(words, c))
        c += len(part) + 1
    speech_end = (words[-1]["t"] + words[-1]["d"]) if words else dur
    out_cues = []
    for i, cue in enumerate(cues):
        st = starts[i]
        en = starts[i + 1] if i + 1 < len(cues) else speech_end + 0.3
        out_cues.append({"start": round(st, 3), "end": round(max(en, st + 0.6), 3), "text": cue})
    meta = {"id": sc["id"], "key": key, "text": text, "spoken": say, "audio": round(dur, 3),
            "duration": round(dur + cfg.get("tail", 0.5), 3), "words": words, "cues": out_cues}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def fmt_t(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:04.1f}"


def write_script(cfg: dict, metas: dict, fx: dict) -> Path:
    lines = ["# Сценарий демо-ролика", "",
             f"Голос `{cfg['voice']}`, скорость `{cfg['rate']}`, {cfg['width']}×{cfg['height']}, {cfg['fps']} к/с. "
             "Файл генерируется `video/tts.py` из `video/scenes.json` (числа — из свежего `out/plan.json`); "
             "правь сцены там.", ""]
    total = 0.0
    rows = []
    for sc in cfg["scenes"]:
        m = metas.get(sc["id"])
        d = m["duration"] if m else 0.0
        rows.append((sc, m, total, d))
        total += d
    lines.append(f"**Итого: {fmt_t(total)}** ({total:.1f} с), сцен: {len(rows)}.")
    lines.append("")
    lines.append("| # | id | тип | начало | длит., с |")
    lines.append("|---|----|-----|--------|----------|")
    for n, (sc, m, st, d) in enumerate(rows, 1):
        lines.append(f"| {n} | `{sc['id']}` | {sc['type']} | {fmt_t(st)} | {d:.1f} |")
    lines.append("")
    for n, (sc, m, st, d) in enumerate(rows, 1):
        lines.append(f"## {n}. `{sc['id']}` — {sc['type']}, {d:.1f} с (с {fmt_t(st)})")
        lines.append("")
        lines.append(f"**В кадре:** {render(sc.get('screen', ''), fx)}")
        if sc["type"] == "desktop":
            lines.append("")
            lines.append("**Запись экрана** — `video/record.py --desktop --only " + sc["id"] + "`; без записи — карточка-заглушка.")
        lines.append("")
        lines.append("**Озвучка:**")
        lines.append("")
        lines.append("> " + (m["text"] if m else render(sc["text"], fx)))
        lines.append("")
    p = VIDEO / "script.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Озвучка сцен ролика (edge-tts)")
    ap.add_argument("--force", action="store_true", help="переозвучить все сцены")
    ap.add_argument("--only", nargs="*", help="только эти id сцен")
    args = ap.parse_args(argv)
    cfg = json.loads((VIDEO / "scenes.json").read_text(encoding="utf-8"))
    fx = facts()
    metas = {}
    for sc in cfg["scenes"]:
        if args.only and sc["id"] not in args.only:
            mp = AUDIO / f"{sc['id']}.json"
            if mp.exists():
                metas[sc["id"]] = json.loads(mp.read_text(encoding="utf-8"))
            continue
        m = build_scene(sc, cfg, fx, args.force)
        metas[sc["id"]] = m
        print(f"{sc['id']:<20} {m['duration']:6.1f} с")
    durations = {k: v["duration"] for k, v in metas.items()}
    (AUDIO / "durations.json").write_text(json.dumps(durations, indent=1), encoding="utf-8")
    p = write_script(cfg, metas, fx)
    print(f"итого {sum(durations.values()):.1f} с → {p.relative_to(VIDEO.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
