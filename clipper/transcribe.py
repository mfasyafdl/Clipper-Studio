"""Full-video transcription with WhisperX (word-level timestamps).

Writes two files into the job folder:
  * words.json            every word with start/end, used for animated captions
  * <source-stem>.srt     segment-level SRT in the exact cache format that
                          AI-Youtube-Shorts-Generator reads, so it skips its own
                          (slower, word-less) faster-whisper pass.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path

from .config import Settings


def _fmt_srt(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fill_missing_times(words: list[dict]) -> list[dict]:
    """WhisperX leaves some tokens (digits, symbols) without timestamps.

    Give each such word a slice of the gap between its timed neighbours so the
    caption renderer never sees a word without start/end.
    """
    out = [dict(w) for w in words if str(w.get("word", "")).strip()]
    n = len(out)
    i = 0
    while i < n:
        if "start" in out[i] and "end" in out[i]:
            i += 1
            continue
        j = i
        while j < n and not ("start" in out[j] and "end" in out[j]):
            j += 1
        span = 0.3 * (j - i)
        if i == 0:  # untimed words at the very start: tuck them in before the first timed word
            right = out[j]["start"] if j < n else span
            left = max(0.0, right - span)
        else:
            left = out[i - 1]["end"]
            right = out[j]["start"] if j < n else left + span
        if right <= left:
            right = left + span
        step = (right - left) / (j - i)
        for k in range(i, j):
            out[k]["start"] = round(left + step * (k - i), 3)
            out[k]["end"] = round(left + step * (k - i + 1), 3)
        i = j
    return out


def segments_to_srt(segments: list[dict]) -> str:
    lines = []
    for idx, seg in enumerate(segments, 1):
        text = str(seg.get("text", "")).strip().replace("\n", " ")
        lines += [str(idx), f"{_fmt_srt(seg['start'])} --> {_fmt_srt(seg['end'])}", text, ""]
    return "\n".join(lines)


def transcribe(source: Path, job_dir: Path, settings: Settings) -> Path:
    words_path = job_dir / "words.json"
    srt_path = job_dir / (source.stem + ".srt")
    if words_path.exists() and srt_path.exists() and words_path.stat().st_mtime >= source.stat().st_mtime:
        print(f"[transcribe] memakai hasil sebelumnya: {words_path}", flush=True)
        return words_path

    import whisperx  # heavy import, only when needed

    device = settings.device
    compute = settings.whisper_compute if device == "cuda" else "int8"
    print(f"[transcribe] whisperX model={settings.whisper_model} device={device} bahasa={settings.language}", flush=True)
    audio = whisperx.load_audio(str(source))
    model = whisperx.load_model(
        settings.whisper_model, device, compute_type=compute, language=settings.language or None
    )
    result = model.transcribe(audio, batch_size=settings.whisper_batch)
    language = result.get("language") or settings.language
    del model
    gc.collect()
    _empty_cuda_cache()

    print(f"[transcribe] menyelaraskan kata (bahasa={language})", flush=True)
    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(result["segments"], align_model, metadata, audio, device, return_char_alignments=False)
    del align_model
    gc.collect()
    _empty_cuda_cache()

    segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s.get("text", "")}
        for s in aligned["segments"]
        if "start" in s and "end" in s
    ]
    words = fill_missing_times([w for s in aligned["segments"] for w in s.get("words", [])])

    words_path.write_text(
        json.dumps({"language": language, "segments": segments, "words": words}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    srt_path.write_text(segments_to_srt(segments), encoding="utf-8")
    print(f"[transcribe] {len(words)} kata, {len(segments)} segmen", flush=True)
    return words_path


def _empty_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
