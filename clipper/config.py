"""Settings loaded from the repo's .env file (and real environment variables)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor"
AYS_DIR = VENDOR_DIR / "AI-Youtube-Shorts-Generator"
MODELS_DIR = VENDOR_DIR / "models"


def _ays_python() -> Path:
    win = REPO_ROOT / ".venv-ays" / "Scripts" / "python.exe"
    nix = REPO_ROOT / ".venv-ays" / "bin" / "python"
    return win if win.exists() else nix


def load_env_file(path: Path) -> dict[str, str]:
    """Tiny .env parser: KEY=VALUE lines, # comments, optional quotes."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _get(env: dict[str, str], key: str, default: str) -> str:
    return os.environ.get(key) or env.get(key) or default


@dataclass
class Settings:
    language: str = "id"
    whisper_model: str = "large-v3-turbo"
    whisper_batch: int = 8
    whisper_compute: str = "float16"
    device: str = "cuda"
    hf_token: str = ""
    caption_font: str = "Arial Black"
    caption_size: int = 84
    caption_words: int = 3
    caption_uppercase: bool = True
    caption_highlight: str = "&H0000E5FF"  # ASS BGR: yellow
    hook_seconds: float = 3.0
    hook_size: int = 62
    video_encoder: str = "auto"  # auto / h264_nvenc / libx264
    engine: str = "gaming"  # gaming (signals + split layout + effects) / ays (old path)
    layout_preset: str = "windah"
    use_chat: bool = True
    chat_timeout_min: float = 30.0
    gemini_video: bool = True
    candidates: int = 40
    effects: str = "light"  # off / light (1 punch-in + 1 sfx at the payoff) / full
    title_suffix: str = " - MOMEN KOCAK WINDAH BASUDARA"
    jobs_dir: Path = field(default_factory=lambda: REPO_ROOT / "jobs")
    llm_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        env = load_env_file(REPO_ROOT / ".env")
        llm_keys = (
            "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL",
            "GEMINI_API_KEY", "GEMINI_MODEL",
        )
        return cls(
            language=_get(env, "LANGUAGE", "id"),
            whisper_model=_get(env, "WHISPER_MODEL", "large-v3-turbo"),
            whisper_batch=int(_get(env, "WHISPER_BATCH", "8")),
            whisper_compute=_get(env, "WHISPER_COMPUTE", "float16"),
            device=_get(env, "DEVICE", "cuda"),
            hf_token=_get(env, "HF_TOKEN", ""),
            caption_font=_get(env, "CAPTION_FONT", "Arial Black"),
            caption_size=int(_get(env, "CAPTION_SIZE", "84")),
            caption_words=int(_get(env, "CAPTION_WORDS", "3")),
            caption_uppercase=_get(env, "CAPTION_UPPERCASE", "1") not in ("0", "false", "no"),
            hook_seconds=float(_get(env, "HOOK_SECONDS", "3")),
            hook_size=int(_get(env, "HOOK_SIZE", "62")),
            video_encoder=_get(env, "VIDEO_ENCODER", "auto"),
            engine=_get(env, "ENGINE", "gaming"),
            layout_preset=_get(env, "LAYOUT_PRESET", "windah"),
            use_chat=_get(env, "USE_CHAT", "1") not in ("0", "false", "no"),
            chat_timeout_min=float(_get(env, "CHAT_TIMEOUT_MIN", "30")),
            gemini_video=_get(env, "GEMINI_VIDEO", "1") not in ("0", "false", "no"),
            candidates=int(_get(env, "CANDIDATES", "40")),
            effects=_get(env, "EFFECTS", "light").lower(),
            title_suffix=_get(env, "TITLE_SUFFIX", " - MOMEN KOCAK WINDAH BASUDARA"),
            jobs_dir=Path(_get(env, "JOBS_DIR", str(REPO_ROOT / "jobs"))),
            llm_env={k: v for k in llm_keys if (v := _get(env, k, ""))},
        )

    @property
    def ays_python(self) -> Path:
        return _ays_python()
