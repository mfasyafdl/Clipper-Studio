"""Runtime patches for the vendored AI-Youtube-Shorts-Generator (no fork).

Imported inside .venv-ays by ays_run.py and ays_recrop.py, after the vendored
repo is on sys.path. Each patch fixes a problem we hit on a real run:

1. Gemini key prefix: upstream only accepts keys starting with "AIza"; Google
   also issues "AQ." keys that google-genai accepts.
2. Long-video chunks: upstream sends each 20-minute chunk with ABSOLUTE
   timestamps, then clamps the model's answer to the chunk length and adds the
   offset again. Every chunk after the first comes back empty or wrong. We
   rebase chunk segments to chunk-relative time so the offset math is right.
3. Vertical crop writer: cv2.VideoWriter('mp4v') crashed on Windows. We keep
   the same face tracking but pipe raw frames into ffmpeg/libx264.
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional, Tuple


def _patch_key_prefix() -> None:
    from shorts_generator import config

    original = config._require_key

    def _require_key(value, name, *args, **kwargs):
        if name == "GEMINI_API_KEY":
            kwargs.pop("expected_prefix", None)
            if len(args) >= 2:
                args = args[:1]
        return original(value, name, *args, **kwargs)

    config._require_key = _require_key


def _patch_chunk_times() -> None:
    from shorts_generator import highlights

    original = highlights.chunk_transcript

    def chunk_transcript(transcript):
        chunks = original(transcript)
        for chunk in chunks:
            off = float(chunk.get("_offset", 0))
            chunk["segments"] = [
                {**s, "start": float(s["start"]) - off, "end": float(s["end"]) - off}
                for s in chunk["segments"]
            ]
            last_end = max((s["end"] for s in chunk["segments"]), default=0.0)
            chunk["duration"] = max(float(chunk.get("duration", 0)), last_end)
        return chunks

    highlights.chunk_transcript = chunk_transcript

    original_call = highlights.call_highlight_api

    def call_highlight_api(*args, **kwargs):
        # one bad chunk shouldn't kill a 2-hour job
        if kwargs.get("is_chunk"):
            try:
                return original_call(*args, **kwargs)
            except RuntimeError as e:
                print(f"[highlights] chunk dilewati: {e}", flush=True)
                return {"highlights": []}
        return original_call(*args, **kwargs)

    highlights.call_highlight_api = call_highlight_api


def _reframe_vertical_ffmpeg(in_path: str, out_path: str, aspect_ratio: str) -> str:
    """Same Haar-cascade face tracking as upstream, frames piped to ffmpeg."""
    import cv2  # type: ignore
    from shorts_generator.local import clipper as up

    in_path = os.path.abspath(os.path.expanduser(in_path))
    out_path = os.path.abspath(os.path.expanduser(out_path))
    target_ratio = up._ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {in_path}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if target_ratio < src_w / src_h:
        crop_h = src_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(crop_w / target_ratio)
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{crop_w}x{crop_h}", "-r", f"{fps:.6f}", "-i", "pipe:0",
        "-i", in_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    last: Optional[Tuple[int, int]] = None
    smoothing = 0.15
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) > 0:
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                cx, cy = x + w // 2, y + h // 2
                last = (cx, cy) if last is None else (
                    int(last[0] + (cx - last[0]) * smoothing), int(last[1] + (cy - last[1]) * smoothing))
            if last is None:
                last = (src_w // 2, src_h // 2)
            x0 = max(0, min(src_w - crop_w, last[0] - crop_w // 2))
            y0 = max(0, min(src_h - crop_h, last[1] - crop_h // 2))
            proc.stdin.write(frame[y0:y0 + crop_h, x0:x0 + crop_w].tobytes())
    finally:
        cap.release()
        if proc.stdin:
            proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg crop gagal: {err[-500:]}")
    return out_path


def apply() -> None:
    _patch_key_prefix()
    _patch_chunk_times()
    from shorts_generator.local import clipper

    clipper._reframe_vertical = _reframe_vertical_ffmpeg
