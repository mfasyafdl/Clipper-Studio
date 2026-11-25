"""One ffmpeg pass per clip: jump cuts, split layout, edit recipe, captions,
bleeps, SFX, music, loudnorm. The filter graph goes to {name}.filter.txt so a
failed render can be inspected and re-run by hand."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import captions, effects, layout
from .config import Settings


_GRAPH_OPT: list[str] = []


def graph_file_option() -> str:
    """-filter_complex_script was removed in FFmpeg 9; '-/filter_complex FILE'
    exists since FFmpeg 7. Pick by version, once."""
    if not _GRAPH_OPT:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout
            m = re.search(r"version n?(\d+)\.", out)
            major = int(m.group(1)) if m else 7  # git builds ("N-12345") are new
        except OSError:
            major = 7
        _GRAPH_OPT.append("-/filter_complex" if major >= 7 else "-filter_complex_script")
    return _GRAPH_OPT[0]


def _beats(clip: dict) -> dict:
    """clip["beats"] (absolute seconds in the VOD) -> clip-relative beats for build_plan."""
    start, end = float(clip["start"]), float(clip["end"])
    raw = clip.get("beats") or {}
    rel = lambda t: None if t is None else float(t) - start  # noqa: E731
    payoff = clip.get("payoff")
    out = {
        "duration": end - start,
        "kind": clip.get("kind") or raw.get("kind") or "other",
        "peak": rel(payoff) if payoff is not None else (end - start) * 0.7,
        "ending": raw.get("ending"),
        "outbursts": [(rel(t), r) for t, r in raw.get("outbursts", []) if start <= float(t) <= end],
        "cards": [(rel(t), d, txt) for t, d, txt in raw.get("cards", []) if start <= float(t) < end],
    }
    for key, src in (("reaction_end", "reaction_end"), ("trigger", "trigger"), ("break_s", "break_character")):
        if raw.get(src) is not None and start <= float(raw[src]) <= end:
            out[key] = rel(raw[src])
    return out


def _between(spans: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)


def build_filter(cam: layout.CamRect, preset: str, plan: effects.Plan, ass_name: str | None,
                 sfx_inputs: list[tuple[int, float]], music_input: int | None) -> tuple[str, str, str]:
    """Returns (filter_complex, video_label, audio_label). Input 0 is the source
    already cut to the clip; sfx_inputs are (input index, output time)."""
    W, H = layout.CANVAS_W, layout.CANVAS_H
    out_D = plan.duration
    total = out_D + plan.tail
    f: list[str] = []

    # --- jump cuts: squeeze dead air
    segs = plan.segments or [(0.0, out_D)]
    if len(segs) > 1:
        n = len(segs)
        f.append(f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)))
        f.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
        pairs = ""
        for i, (a, b) in enumerate(segs):
            f.append(f"[vs{i}]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[vt{i}]")
            f.append(f"[as{i}]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[at{i}]")
            pairs += f"[vt{i}][at{i}]"
        f.append(f"{pairs}concat=n={n}:v=1:a=1[vsrc][asrc]")
    else:
        f.append("[0:v]setpts=PTS-STARTPTS[vsrc]")
        f.append("[0:a]asetpts=PTS-STARTPTS[asrc]")

    # --- layout: facecam panel (with zoom/shake) over 4:3 gameplay
    f.append("[vsrc]split=2[c][g]")
    f.append(f"[c]{cam.filter()},{effects.cam_chain(plan, W, layout.CAM_H, cam.face_x, cam.face_y)},setsar=1[cam]")
    f.append(f"[g]{layout.game_crop(preset)},scale={W}:{layout.GAME_H}:flags=lanczos,setsar=1[game]")
    f.append(f"[cam]pad={W}:{H}:0:{layout.CAM_Y}:black[bg]")
    f.append(f"[bg][game]overlay=0:{layout.GAME_Y}[v0]")
    last, k = "v0", 0

    def push(expr: str) -> None:
        nonlocal last, k
        k += 1
        f.append(f"[{last}]{expr}[v{k}]")
        last = f"v{k}"

    for expr in effects.color_chain(plan):
        push(expr)

    # --- red flash: 3 frames of 40 % red (drawbox alpha is far too strong on YUV)
    for i, t in enumerate(plan.flashes):
        f.append(f"color=c=red:s={W}x{H}:r=30:d=0.1,format=rgba,colorchannelmixer=aa=0.4,"
                 f"fade=t=out:st=0:d=0.1:alpha=1,setpts=PTS+{t:.3f}/TB[fl{i}]")
        k += 1
        f.append(f"[{last}][fl{i}]overlay=eof_action=pass:format=auto[v{k}]")
        last = f"v{k}"

    # --- ending tail: freeze the last frame (captions/cards are drawn after, so they keep animating)
    if plan.tail > 0:
        push(f"tpad=stop_mode=clone:stop_duration={plan.tail:.3f}")
        if plan.ending == "freeze_fade":
            push(f"fade=t=out:st={max(0.0, total - 1.0):.3f}:d=1.0")
    if ass_name:
        push(f"ass={ass_name}")
    f.append(f"[{last}]fps=30,format=yuv420p[vout]")

    # --- audio
    f.append("[asrc]aresample=48000,aformat=channel_layouts=stereo[a0]")
    alast = "a0"
    mix: list[str] = []
    if plan.bleeps:
        on = _between(plan.bleeps)
        f.append(f"[{alast}]volume=0:enable='{on}'[a1]")
        alast = "a1"
        f.append(f"sine=f=1000:sample_rate=48000:d={out_D:.3f},aformat=channel_layouts=stereo,"
                 f"volume='0.25*({on})':eval=frame[beep]")
        mix.append("[beep]")
    if plan.tail > 0:
        f.append(f"[{alast}]apad=pad_dur={plan.tail:.3f}[a2]")
        alast = "a2"
    for idx, t in sfx_inputs:
        ms = int(max(0.0, t) * 1000)
        f.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms},volume=0.9[s{idx}]")
        mix.append(f"[s{idx}]")
    if music_input is not None and plan.music:
        _, t_in, t_out = plan.music
        dur = max(0.5, min(total, t_out) - t_in)
        ms = int(t_in * 1000)
        f.append(f"[{music_input}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},"
                 f"asetpts=PTS-STARTPTS,afade=t=in:d=0.8,afade=t=out:st={max(0.0, dur - 0.04):.3f}:d=0.04,"
                 f"volume=0.3,adelay={ms}|{ms}[mus]")
        mix.append("[mus]")
    if mix:
        f.append(f"[{alast}]{''.join(mix)}amix=inputs={len(mix) + 1}:normalize=0:duration=first[am]")
        alast = "am"
    f.append(f"[{alast}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
             f"afade=t=out:st={max(0.0, total - 0.05):.3f}:d=0.05[aout]")
    return ";\n".join(f), "[vout]", "[aout]"


def render_gaming(job_dir: Path, source: Path, clip: dict, words: list[dict], settings: Settings,
                  name: str, encoder: str = "auto") -> dict:
    final_dir = job_dir / "final"
    final_dir.mkdir(exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    duration = end - start

    if not clip.get("cam"):
        rect = layout.detect_cam(source, start, end, settings.layout_preset)
        clip["cam"] = {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h, "found": rect.found,
                       "face_x": rect.face_x, "face_y": rect.face_y}
    cam = layout.CamRect(**clip["cam"])

    beats = _beats(clip)
    music = None
    if settings.effects == "full" and beats["kind"] == "fake_cry":
        music = effects.find_music("sad")
        if music is None:
            print(f"[render] {name}: tidak ada musik di folder music/, momen sedih dirender tanpa musik",
                  flush=True)
    clip_words = captions.words_in_range(words, start, end)
    captions.write_srt(clip_words, final_dir / f"{name}.srt")  # SRT keeps source timing for editors
    plan = effects.build_plan(beats, clip_words, settings.effects, settings.censor, music)
    clip["applied_plan"] = plan.describe()
    total = plan.duration + plan.tail

    hook = clip.get("hook") or clip.get("title", "").replace(settings.title_suffix.strip().upper(), "").strip(" -")
    ass_path = final_dir / f"{name}.ass"
    ass_path.write_text(captions.build_ass(clip_words, hook, total, settings, layout="split", cards=plan.cards),
                        encoding="utf-8")

    inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source.resolve())]
    sfx_inputs: list[tuple[int, float]] = []
    if plan.sfx:
        paths = effects.ensure_sfx()
        for sfx_name, t in plan.sfx:
            sfx_inputs.append((1 + len(sfx_inputs), t))
            inputs += ["-i", str(paths[sfx_name].resolve())]
    music_input = None
    if plan.music:
        music_input = 1 + len(sfx_inputs)
        inputs += ["-stream_loop", "-1", "-i", str(plan.music[0].resolve())]

    fc, vout, aout = build_filter(cam, settings.layout_preset, plan, ass_path.name, sfx_inputs, music_input)
    script = final_dir / f"{name}.filter.txt"
    script.write_text(fc, encoding="utf-8")
    out_path = final_dir / f"{name}.mp4"

    candidates = ["h264_nvenc", "libx264"] if encoder == "auto" else [encoder]
    if encoder == "auto":
        candidates = [c for c in candidates if c not in captions._FAILED_ENCODERS] or ["libx264"]
    err = ""
    for enc in candidates:
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs, graph_file_option(), script.name,
               "-map", vout, "-map", aout, "-t", f"{total:.3f}",
               *captions._encoder_args(enc), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
               str(out_path.resolve())]
        proc = subprocess.run(cmd, cwd=final_dir, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"final": f"final/{name}.mp4", "srt": f"final/{name}.srt", "encoder": enc,
                    "text": " ".join(w["word"] for w in clip_words), "duration": round(total, 2)}
        err = proc.stderr.strip()[-1200:]
        if encoder == "auto" and enc != "libx264":
            print(f"[render] {enc} gagal, pakai libx264", flush=True)
            captions._FAILED_ENCODERS.add(enc)
    raise RuntimeError(f"ffmpeg gagal merender {name}: {err}")
