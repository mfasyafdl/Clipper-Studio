"""One ffmpeg pass per clip: split layout + edit plan + captions + SFX + loudnorm."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import captions, effects, layout
from .config import Settings


def build_filter(cam: layout.CamRect, preset: str, plan: list[dict], n_sfx: int, sfx_delays: list[float],
                 ass_name: str | None) -> tuple[str, str, str]:
    """Returns (filter_complex, video_label, audio_label)."""
    v = [
        "[0:v]split=2[c][g]",
        f"[c]{cam.filter()},scale={layout.CANVAS_W}:{layout.CAM_H}:flags=lanczos,setsar=1,"
        f"{effects.cam_chain(plan, layout.CANVAS_W, layout.CAM_H)}[cam]",
        f"[g]{layout.game_crop(preset)},scale={layout.CANVAS_W}:{layout.GAME_H}:flags=lanczos,setsar=1[game]",
        f"[cam]pad={layout.CANVAS_W}:{layout.CANVAS_H}:0:{layout.CAM_Y}:black[bg]",
        f"[bg][game]overlay=0:{layout.GAME_Y}[v0]",
    ]
    last = "v0"
    flash = effects.flash_filter(plan)
    if flash:
        v.append(f"[{last}]{flash}[v1]")
        last = "v1"
    if ass_name:
        v.append(f"[{last}]ass={ass_name}[v2]")
        last = "v2"
    v.append(f"[{last}]format=yuv420p[vout]")

    a = []
    mix = ["[0:a]"]
    for i, delay in enumerate(sfx_delays, start=1):
        ms = int(delay * 1000)
        a.append(f"[{i}:a]adelay={ms}|{ms},volume=0.9[s{i}]")
        mix.append(f"[s{i}]")
    if n_sfx:
        a.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[am]")
        src = "[am]"
    else:
        src = "[0:a]"
    a.append(f"{src}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    return ";".join(v + a), "[vout]", "[aout]"


def render_gaming(job_dir: Path, source: Path, clip: dict, words: list[dict], settings: Settings,
                  name: str, encoder: str = "auto") -> dict:
    final_dir = job_dir / "final"
    final_dir.mkdir(exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    duration = end - start

    if not clip.get("cam"):
        rect = layout.detect_cam(source, start, end, settings.layout_preset)
        clip["cam"] = {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h, "found": rect.found}
    cam = layout.CamRect(**clip["cam"])

    plan = effects.validate(clip.get("edit_plan") or [], duration, settings.effects)
    if settings.effects != "off" and not any(e["type"] == "zoom" for e in plan) and clip.get("payoff") is not None:
        plan = effects.validate(plan + effects.default_plan(float(clip["payoff"]) - start, duration,
                                                            clip.get("category", "")), duration, settings.effects)
    clip["applied_plan"] = plan
    sfx_paths = effects.ensure_sfx() if any(e["type"] == "sfx" for e in plan) else {}
    sfx = effects.sfx_inputs(plan, sfx_paths)

    clip_words = captions.words_in_range(words, start, end)
    ass_path = final_dir / f"{name}.ass"
    ass_path.write_text(captions.build_ass(clip_words, clip.get("hook") or clip.get("title", "").replace(settings.title_suffix.strip().upper(), "").strip(" -"),
                                          duration,
                                          settings, layout="split"), encoding="utf-8")
    captions.write_srt(clip_words, final_dir / f"{name}.srt")

    fc, vout, aout = build_filter(cam, settings.layout_preset, plan, len(sfx), [t for _, t in sfx], ass_path.name)
    out_path = final_dir / f"{name}.mp4"
    inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source.resolve())]
    for path, _ in sfx:
        inputs += ["-i", str(path.resolve())]

    candidates = ["h264_nvenc", "libx264"] if encoder == "auto" else [encoder]
    if encoder == "auto":
        candidates = [c for c in candidates if c not in captions._FAILED_ENCODERS] or ["libx264"]
    err = ""
    for enc in candidates:
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", vout, "-map", aout,
               *captions._encoder_args(enc), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
               str(out_path.resolve())]
        proc = subprocess.run(cmd, cwd=final_dir, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"final": f"final/{name}.mp4", "srt": f"final/{name}.srt", "encoder": enc,
                    "text": " ".join(w["word"] for w in clip_words)}
        err = proc.stderr.strip()[-1200:]
        if encoder == "auto" and enc != "libx264":
            print(f"[render] {enc} gagal, pakai libx264", flush=True)
            captions._FAILED_ENCODERS.add(enc)
    raise RuntimeError(f"ffmpeg gagal merender {name}: {err}")
