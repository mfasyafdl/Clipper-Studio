"""Animated word-by-word captions + hook title, burned in with ffmpeg.

The clip coming out of AI-Youtube-Shorts-Generator is already cut and
reframed to 9:16. This step scales it to 1080x1920, writes an .ass subtitle
file (current word highlighted with a small pop) plus a hook banner for the
first few seconds, and burns both in.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .config import Settings

PLAY_W, PLAY_H = 1080, 1920
_FAILED_ENCODERS: set[str] = set()  # don't retry a broken NVENC for every clip
_BREAK_PUNCT = re.compile(r"[.,!?;:…]$")


MAX_WORD = 1.2


def words_in_range(words: list[dict], start: float, end: float) -> list[dict]:
    """Words that overlap [start, end], with times shifted to clip-relative."""
    out = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        # WhisperX sometimes stretches the last word before a pause over the
        # whole pause (audit: one caption on screen for 17 s); no word lasts > 1.2 s
        w_end = min(w["end"], w["start"] + MAX_WORD)
        out.append({
            "word": str(w["word"]).strip(),
            "start": max(0.0, w["start"] - start),
            "end": min(end - start, w_end - start),
        })
    return out


def group_words(words: list[dict], max_words: int = 3, max_gap: float = 0.6) -> list[list[dict]]:
    """Split into short caption lines: max N words, break on pauses/punctuation."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap):
            groups.append(cur)
            cur = []
        cur.append(w)
        if _BREAK_PUNCT.search(w["word"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


RAGE_COLOR = "&H002A2AFF"  # #FF2A2A in ASS BGR


def build_ass(words: list[dict], hook: str, duration: float, s: Settings, layout: str = "full",
              cards: list[tuple[float, float, str]] | None = None) -> str:
    """layout="full": captions low-middle, hook banner for the first seconds.
    layout="split": hook pinned the whole clip in the black header (0-210),
    captions on the facecam panel above the seam (chest height), inside the
    TikTok safe zone (right-side button column kept clear)."""
    if layout == "split":
        cap_style = (f"Style: Caption,{s.caption_font},{s.caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
                     f"&H64000000,-1,0,0,0,100,100,0,0,1,7,3,2,120,180,1080,1")
        hook_style = (f"Style: Hook,{s.caption_font},{s.hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
                      f"&H00000000,-1,0,0,0,100,100,0,0,1,4,0,5,60,60,0,1")
    else:
        cap_style = (f"Style: Caption,{s.caption_font},{s.caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
                     f"&H64000000,-1,0,0,0,100,100,0,0,1,7,3,2,80,80,560,1")
        hook_style = (f"Style: Hook,{s.caption_font},{s.hook_size},&H00000000,&H00000000,&H0000E5FF,"
                      f"&H0000E5FF,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,230,1")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{cap_style}
{hook_style}
Style: Card,Georgia,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,-1,0,0,100,100,0,0,1,4,2,8,100,180,1060,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    if hook and layout == "split":
        hook_text = _esc(hook.upper() if s.caption_uppercase else hook)
        events.append(
            f"Dialogue: 1,{_ts(0)},{_ts(duration)},Hook,,0,0,0,,{{\\pos(540,105)\\fscx85\\fscy85\\t(0,140,\\fscx100\\fscy100)}}{hook_text}"
        )
    elif hook and s.hook_seconds > 0:
        hook_text = _esc(hook.upper() if s.caption_uppercase else hook)
        end = min(duration, s.hook_seconds)
        events.append(
            f"Dialogue: 1,{_ts(0)},{_ts(end)},Hook,,0,0,0,,{{\\fad(120,200)\\fscx85\\fscy85\\t(0,140,\\fscx100\\fscy100)}}{hook_text}"
        )

    for t, dur, text in cards or []:
        events.append(f"Dialogue: 2,{_ts(t)},{_ts(min(duration, t + dur))},Card,,0,0,0,,{{\\fad(300,300)}}{_esc(text)}")

    for group in group_words(words, max_words=s.caption_words):
        tokens = []
        for w in group:
            raw = w.get("display") or w["word"]
            tok = _esc(raw.upper() if s.caption_uppercase else raw)
            if w.get("rage"):  # angry / swear word: red, big, pop
                tok = (f"{{\\c{RAGE_COLOR}\\fscx150\\fscy150\\t(0,120,\\fscx130\\fscy130)}}{tok}"
                       f"{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
            tokens.append(tok)
        for i, w in enumerate(group):
            start = w["start"]
            end = group[i + 1]["start"] if i + 1 < len(group) else w["end"]
            if end - start < 0.05:
                end = start + 0.05
            parts = []
            for j, tok in enumerate(tokens):
                if j == i and not group[j].get("rage"):
                    parts.append(
                        f"{{\\c{s.caption_highlight}\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}}{tok}{{\\c&H00FFFFFF&}}"
                    )
                else:
                    parts.append(tok)
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def _encoder_args(name: str) -> list[str]:
    if name == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


def burn(clip: Path, ass_path: Path, out_path: Path, encoder: str = "auto") -> str:
    """Scale to 1080x1920 and burn the .ass file. Returns the encoder used.

    ffmpeg runs inside the clip's folder with a relative .ass name, which
    sidesteps the painful escaping of Windows drive letters in filter args.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg tidak ditemukan di PATH. Jalankan install.ps1 lagi.")
    workdir = ass_path.parent
    vf = (
        f"scale={PLAY_W}:{PLAY_H}:force_original_aspect_ratio=increase,"
        f"crop={PLAY_W}:{PLAY_H},setsar=1,ass={ass_path.name}"
    )
    candidates = ["h264_nvenc", "libx264"] if encoder == "auto" else [encoder]
    if encoder == "auto":
        candidates = [c for c in candidates if c not in _FAILED_ENCODERS] or ["libx264"]
    last_err = ""
    for enc in candidates:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(clip.resolve()),
            "-vf", vf, *_encoder_args(enc), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(out_path.resolve()),
        ]
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
        if proc.returncode == 0:
            return enc
        last_err = proc.stderr.strip()[-800:]
        if enc not in _FAILED_ENCODERS:
            print(f"[captions] encoder {enc} gagal ({last_err.splitlines()[-1] if last_err else '?'}), pakai berikutnya", flush=True)
        _FAILED_ENCODERS.add(enc)
    raise RuntimeError(f"ffmpeg gagal membakar subtitle: {last_err}")


def write_srt(words: list[dict], path: Path, max_words: int = 6) -> None:
    """Plain SRT of the clip, handy as an editable subtitle track in Resolve."""
    from .transcribe import _fmt_srt

    lines = []
    for idx, g in enumerate(group_words(words, max_words=max_words), 1):
        text = " ".join(w["word"] for w in g)
        lines += [str(idx), f"{_fmt_srt(g[0]['start'])} --> {_fmt_srt(g[-1]['end'])}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def caption_clip(
    clip: Path, all_words: list[dict], start: float, end: float, hook: str, out_path: Path, s: Settings
) -> dict:
    words = words_in_range(all_words, start, end)
    stem = out_path.stem
    ass_path = out_path.with_name(stem + ".ass")
    ass_path.write_text(build_ass(words, hook, end - start, s), encoding="utf-8")
    write_srt(words, out_path.with_name(stem + ".srt"))
    encoder = burn(clip, ass_path, out_path, s.video_encoder)
    return {"final": out_path.name, "ass": ass_path.name, "srt": stem + ".srt", "encoder": encoder,
            "text": " ".join(w["word"] for w in words)}
