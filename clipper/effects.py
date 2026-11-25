"""The "funny layer": an edit plan rendered with ffmpeg.

An edit plan is a list of events in clip-relative seconds:
    {"t": 12.4, "type": "zoom"}                    punch-in on the facecam
    {"t": 12.4, "type": "shake"}                   camera shake (0.35 s)
    {"t": 12.4, "type": "flash"}                   white flash (0.15 s)
    {"t": 12.4, "type": "sfx", "name": "boom"}     sound effect

Gemini proposes it (it watched the clip); validate() enforces the dosage that
the research points to: effects land ON the punchline, not as constant noise.

Sound effects are synthesised with ffmpeg on first use, so the repo ships
no third-party audio with unclear licenses. Drop your own files with the
same names into sfx/ (e.g. sfx/boom.wav) to override them.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import REPO_ROOT

SFX_DIR = REPO_ROOT / "sfx"
SFX_NAMES = ("boom", "whoosh", "ding", "scratch", "pop", "bass")

# ffmpeg lavfi recipes (all generated, no licensing questions)
_SFX_RECIPES = {
    # deep "vine-boom"-like hit: falling sine + sub, quick decay
    "boom": "sine=f=55:d=1.2,volume=2.5[a];sine=f=110:d=1.2,volume=1.2[b];[a][b]amix=2,"
            "afade=t=out:st=0.05:d=1.1,acompressor,volume=1.6",
    "whoosh": "anoisesrc=d=0.6:c=pink:a=0.6,bandpass=f=1800:w=1400,"
              "afade=t=in:d=0.25,afade=t=out:st=0.3:d=0.3",
    "ding": "sine=f=1760:d=0.7,afade=t=out:st=0.02:d=0.65,volume=0.6",
    "scratch": "anoisesrc=d=0.45:c=white:a=0.5,bandpass=f=900:w=600,tremolo=f=18:d=0.9,"
               "afade=t=out:st=0.2:d=0.25",
    "pop": "sine=f=600:d=0.12,afade=t=out:st=0.0:d=0.12,volume=0.8",
    "bass": "sine=f=40:d=0.9,afade=t=out:st=0.05:d=0.8,volume=3",
}

MAX = {"zoom": 3, "shake": 2, "flash": 2, "sfx": 3}
# "light" follows what the top Windah clips (14M, 8.1M views) actually do:
# one light punch-in on the facecam at the payoff, nothing else repeated.
LIGHT_MAX = {"zoom": 1, "shake": 1, "flash": 0, "sfx": 1}
MIN_GAP = {"zoom": 2.2, "shake": 2.0, "flash": 3.0, "sfx": 1.5}
ZOOM_AMOUNT = 0.14
SHAKE_PX = 10


def ensure_sfx() -> dict[str, Path]:
    SFX_DIR.mkdir(exist_ok=True)
    paths = {}
    for name in SFX_NAMES:
        own = next((p for ext in (".wav", ".mp3", ".ogg") if (p := SFX_DIR / f"{name}{ext}").exists()), None)
        if own:
            paths[name] = own
            continue
        out = SFX_DIR / f"{name}.wav"
        recipe = _SFX_RECIPES[name]
        if ";" in recipe or "[" in recipe:
            cmd = ["ffmpeg", "-y", "-v", "error", "-filter_complex", recipe, "-ar", "48000", "-ac", "2", str(out)]
        else:
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", recipe, "-ar", "48000", "-ac", "2", str(out)]
        subprocess.run(cmd, check=True)
        paths[name] = out
    return paths


def validate(plan: list[dict], duration: float, level: str = "light") -> list[dict]:
    """Drop malformed events, enforce spacing and per-type caps for the level."""
    if level == "off":
        return []
    caps = LIGHT_MAX if level == "light" else MAX
    clean: list[dict] = []
    for ev in sorted((e for e in plan if isinstance(e, dict)), key=lambda e: float(e.get("t", -1))):
        kind = str(ev.get("type", "")).lower()
        try:
            t = float(ev.get("t"))
        except (TypeError, ValueError):
            continue
        if kind not in caps or not 0.2 <= t <= duration - 0.3:
            continue
        same = [e for e in clean if e["type"] == kind]
        if len(same) >= caps[kind] or any(abs(e["t"] - t) < MIN_GAP[kind] for e in same):
            continue
        item = {"t": round(t, 2), "type": kind}
        if kind == "sfx":
            name = str(ev.get("name", "boom")).lower()
            item["name"] = name if name in SFX_NAMES else "boom"
        clean.append(item)
    return clean


def default_plan(payoff_s: float, duration: float, category: str = "") -> list[dict]:
    """Used when Gemini gave no plan: one punchline beat at the payoff."""
    t = min(max(0.5, payoff_s), duration - 0.5)
    plan = [{"t": t, "type": "zoom"}, {"t": t, "type": "sfx", "name": "boom"}]
    if category in ("jumpscare", "rage"):
        plan.append({"t": t, "type": "shake"})
    return plan


def _bump(times: list[float], width: float = 0.34) -> str:
    """Sum of smooth bumps exp(-((t-T)/w)^6): ~flat-topped pulse around each T."""
    return "+".join(f"exp(-pow((t-{T:.3f})/{width},6))" for T in times) or "0"


def cam_chain(plan: list[dict], w: int = 1080, h: int = 790) -> str:
    """Punch-in + shake on the facecam panel.

    Uses scale with eval=frame and a FIXED-size crop: a crop with time-varying
    w/h does not animate (verified by the research), x/y expressions do.
    A 4% base zoom gives the shake room so black edges never show.
    """
    zooms = [e["t"] for e in plan if e["type"] == "zoom"]
    shakes = [e["t"] for e in plan if e["type"] == "shake"]
    base = 0.04 if shakes else 0.0
    if not zooms and not shakes:
        return f"scale={w}:{h}:flags=lanczos"
    zexpr = f"(1+{base}+{ZOOM_AMOUNT}*({_bump(zooms)}))" if zooms else f"(1+{base})"
    sx = f"{SHAKE_PX}*sin(2*PI*17*t)*({_bump(shakes, 0.2)})" if shakes else "0"
    sy = f"{SHAKE_PX}*cos(2*PI*13*t)*({_bump(shakes, 0.2)})" if shakes else "0"
    return (
        f"scale=w='trunc({w}*{zexpr}/2)*2':h='trunc({h}*{zexpr}/2)*2':eval=frame:flags=bicubic,"
        f"crop={w}:{h}:x='(iw-{w})/2+{sx}':y='(ih-{h})*0.42+{sy}'"
    )


def flash_filter(plan: list[dict]) -> str | None:
    flashes = [e["t"] for e in plan if e["type"] == "flash"]
    if not flashes:
        return None
    expr = "+".join(f"0.7*gte(t,{T:.3f})*max(0,1-(t-{T:.3f})/0.15)" for T in flashes)
    return f"eq=brightness='{expr}':eval=frame"


def sfx_inputs(plan: list[dict], sfx: dict[str, Path]) -> list[tuple[Path, float]]:
    return [(sfx[e["name"]], e["t"]) for e in plan if e["type"] == "sfx" and e.get("name") in sfx]
