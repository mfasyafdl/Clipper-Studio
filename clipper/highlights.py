"""Highlight picking + 9:16 reframe via AI-Youtube-Shorts-Generator (vendored).

We call its CLI in --mode local inside its own virtualenv so its
dependencies never fight with WhisperX's torch build.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import AYS_DIR, Settings


def _check_llm(settings: Settings) -> None:
    provider = settings.llm_env.get("LLM_PROVIDER", "gemini").lower()
    key = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
    if not settings.llm_env.get(key):
        raise RuntimeError(
            f"{key} belum diisi di .env (LLM_PROVIDER={provider}). "
            "Highlight dipilih oleh LLM, jadi kunci ini wajib."
        )


def run_ays(source: Path, job_dir: Path, num_clips: int, settings: Settings) -> list[dict]:
    out_json = job_dir / "ays_result.json"
    if not AYS_DIR.exists():
        raise RuntimeError(f"{AYS_DIR} tidak ada. Jalankan install.ps1 dulu.")
    if not settings.ays_python.exists():
        raise RuntimeError(f"{settings.ays_python} tidak ada. Jalankan install.ps1 dulu.")
    _check_llm(settings)

    env = os.environ.copy()
    env.update({"LLM_PROVIDER": "gemini", **settings.llm_env})
    env["LOCAL_OUTPUT_DIR"] = str(job_dir.resolve())
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        str(settings.ays_python), "main.py", str(source.resolve()),
        "--mode", "local", "--num-clips", str(num_clips),
        "--aspect-ratio", "9:16", "--output-json", str(out_json.resolve()),
    ]
    if settings.language:
        cmd += ["--language", settings.language]
    print("[highlights] menjalankan AI-Youtube-Shorts-Generator (mode local)", flush=True)
    subprocess.run(cmd, cwd=AYS_DIR, env=env, check=True)

    result = json.loads(out_json.read_text(encoding="utf-8"))
    shorts = []
    for i, s in enumerate(result.get("shorts", []), 1):
        shorts.append({
            "id": f"clip_{i:02d}",
            "start": float(s["start_time"]),
            "end": float(s["end_time"]),
            "score": s.get("score"),
            "title": s.get("title", ""),
            "hook": s.get("hook_sentence") or s.get("title", ""),
            "reason": s.get("virality_reason") or "",
            "vertical": Path(s["clip_url"]).name if s.get("clip_url") else None,
            "error": s.get("error"),
        })
    return shorts


def recrop(source: Path, start: float, end: float, out_path: Path, settings: Settings) -> None:
    helper = Path(__file__).with_name("ays_recrop.py")
    subprocess.run(
        [str(settings.ays_python), str(helper), str(AYS_DIR), str(source.resolve()),
         f"{start:.3f}", f"{end:.3f}", "9:16", str(out_path.resolve())],
        check=True,
    )
