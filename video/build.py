"""Склейка ролика: кадры сцен + озвучка + субтитры → video/out/demo.mp4 (H.264 + AAC, 1920×1080).

    .venv/Scripts/python.exe video/build.py
    .venv/Scripts/python.exe video/build.py --only s02_summary   # пересобрать сегмент сцены и ролик

Каждая сцена кодируется отдельно (video/build/<id>.mp4): видео — frames/<id>/list.txt или
frames/<id>/desktop.mp4 (если сцену записали с --desktop), звук — audio/<id>.mp3, субтитры
прожигаются фильтром ass из audio/<id>.json. Затем сегменты склеиваются без перекодирования.
Рядом кладётся out/demo.srt — те же субтитры отдельным файлом.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

VIDEO = Path(__file__).resolve().parent
sys.path.insert(0, str(VIDEO))
from tts import AUDIO, ffmpeg, media_duration  # noqa: E402

FRAMES = VIDEO / "frames"
BUILD = VIDEO / "build"
OUT = VIDEO / "out"
FADE = 0.18

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Segoe UI,40,&H00FFFFFF,&H00FFFFFF,&H18101418,&H00000000,0,0,0,0,100,100,0,0,3,12,0,2,240,240,46,204

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(s: float) -> str:
    cs = int(round(s * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def srt_time(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def write_ass(meta: dict, path: Path) -> None:
    lines = [ASS_HEAD]
    for c in meta["cues"]:
        end = min(c["end"], meta["duration"] - 0.05)
        text = c["text"].replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(end)},Sub,,0,0,0,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"ffmpeg завершился с кодом {r.returncode}")


def build_segment(sc: dict, cfg: dict) -> Path:
    sid = sc["id"]
    meta = json.loads((AUDIO / f"{sid}.json").read_text(encoding="utf-8"))
    dur, fps = meta["duration"], cfg["fps"]
    BUILD.mkdir(parents=True, exist_ok=True)
    write_ass(meta, BUILD / f"{sid}.ass")
    desk = FRAMES / sid / "desktop.mp4"
    lst = FRAMES / sid / "list.txt"
    if desk.exists():
        vin = ["-i", str(desk)]
    elif lst.exists():
        vin = ["-f", "concat", "-safe", "0", "-i", str(lst)]
    else:
        raise SystemExit(f"{sid}: нет кадров — запусти video/record.py --only {sid}")
    out = BUILD / f"{sid}.mp4"
    vf = (f"[0:v]fps={fps},scale={cfg['width']}:{cfg['height']}:flags=lanczos:in_range=auto:out_range=tv,setsar=1,"
          f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
          f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE:.3f}:d={FADE},"
          f"ass={sid}.ass,format=yuv420p[v];"
          f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={dur:.3f},"
          f"afade=t=out:st={dur - 0.15:.3f}:d=0.15[a]")
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *vin, "-i", str(AUDIO / f"{sid}.mp3"),
           "-filter_complex", vf, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-color_range", "tv", "-r", str(fps), "-g", str(fps * 4),
           "-c:a", "aac", "-b:a", "160k", "-t", f"{dur:.3f}", out.name]
    run(cmd, cwd=BUILD)  # cwd — чтобы путь к .ass был без двоеточия диска
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Склейка ролика")
    ap.add_argument("--only", nargs="*", help="перекодировать только эти сцены (остальные — из video/build)")
    args = ap.parse_args(argv)
    cfg = json.loads((VIDEO / "scenes.json").read_text(encoding="utf-8"))
    segs, srt, t, n = [], [], 0.0, 1
    for sc in cfg["scenes"]:
        seg = BUILD / f"{sc['id']}.mp4"
        if not args.only or sc["id"] in args.only or not seg.exists():
            seg = build_segment(sc, cfg)
            print(f"{sc['id']:<20} готов", flush=True)
        segs.append(seg)
        meta = json.loads((AUDIO / f"{sc['id']}.json").read_text(encoding="utf-8"))
        for c in meta["cues"]:
            srt.append(f"{n}\n{srt_time(t + c['start'])} --> {srt_time(t + min(c['end'], meta['duration']))}\n{c['text']}\n")
            n += 1
        t += meta["duration"]
    OUT.mkdir(parents=True, exist_ok=True)
    (BUILD / "concat.txt").write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
    out = OUT / "demo.mp4"
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
         "-c", "copy", "-movflags", "+faststart", str(out)], cwd=BUILD)
    (OUT / "demo.srt").write_text("\n".join(srt), encoding="utf-8")
    real = media_duration(out)
    print(f"→ {out.relative_to(VIDEO.parent)} · {int(real // 60)}:{real % 60:04.1f} ({real:.1f} с), "
          f"{out.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
