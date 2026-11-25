"""The edit layer: per-moment recipes turned into ffmpeg expressions.

Gemini describes the moment (type, trigger, peak, reaction end, break of
character, outbursts, ending). build_plan() maps that onto numbers from the
research ("Editing clip Windah marah dan sedih"), compresses dead air, and
limits how many effects land per clip.

Recipes (EFFECTS=full, the default):
  rage      dead air before the trigger squeezed (jump cut), slow push-in during the build-up, ONE crash zoom (ease-out,
            held, eased release) on the biggest outburst with a decaying
            shake, a 3-frame red flash and one SFX; smaller outbursts get a
            small punch. Swear words red and big in the captions, heavy
            ones bleeped. Hard cut 0.4 s after the rant actually ends.
  fake_cry  played straight: colour fades to black-and-white over 1.2 s,
            vignette, slow Ken Burns push, sad music (music/ folder) from
            just before the sob. When he breaks character: colour snaps back,
            music stops dead, crash zoom + record scratch. Hold ~1.5 s, cut.
  other     push-in + one punch + one SFX at the payoff.
EFFECTS=light uses "other" for everything, EFFECTS=off disables effects.

SFX are synthesised by ffmpeg on first use (no third-party audio). Put
your own files with the same names in sfx/ to override them.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT

SFX_DIR = REPO_ROOT / "sfx"
MUSIC_DIR = REPO_ROOT / "music"
SFX_NAMES = ("boom", "whoosh", "ding", "scratch", "pop", "bass")

_SFX_RECIPES = {
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

HEAVY_SWEARS = {"kontol", "memek", "ngentot", "entot", "bangsat", "jancok", "jancuk", "pepek"}
MEDIUM_SWEARS = {"anjing", "anjeng", "babi", "goblok", "tolol", "asu", "kampret", "bajingan", "tai"}
RAGE_WORDS = HEAVY_SWEARS | MEDIUM_SWEARS | {"anjir", "anjay", "njir", "bjir", "woi", "woy", "astaga", "sialan"}

GAP_MAX = 2.5   # silence longer than this inside a clip is compressed ...
GAP_KEEP = 0.6  # ... keeping this much on each side
MIN_OUT = 15.0  # never squeeze a clip below this


def _norm(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


def mask(word: str) -> str:
    """ANJING -> ANJ*NG: readable, but censored in titles/captions."""
    w = word.strip()
    vowels = [i for i, ch in enumerate(w) if ch.lower() in "aiueo"]
    i = vowels[1] if len(vowels) > 1 else (vowels[0] if vowels else len(w) // 2)
    return w[:i] + "*" + w[i + 1:]


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
        if "[" in recipe:
            cmd = ["ffmpeg", "-y", "-v", "error", "-filter_complex", recipe, "-ar", "48000", "-ac", "2", str(out)]
        else:
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", recipe, "-ar", "48000", "-ac", "2", str(out)]
        subprocess.run(cmd, check=True)
        paths[name] = out
    return paths


def find_music(mood: str = "sad") -> Path | None:
    if not MUSIC_DIR.exists():
        return None
    files = sorted(p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".ogg", ".m4a", ".flac"))
    preferred = [p for p in files if mood in p.stem.lower()]
    return (preferred or files or [None])[0]


# ------------------------------------------------------------------ plan
@dataclass
class Punch:
    t: float          # moment of full zoom
    amount: float     # 0.3 = 1.3x
    rise: float = 0.12
    hold_until: float = 0.0
    release: float = 0.4
    ease: str = "out_cubic"


@dataclass
class Plan:
    kind: str = "other"
    duration: float = 0.0                  # output length before the ending tail
    segments: list[tuple[float, float]] = field(default_factory=list)  # kept spans, clip-relative
    push: tuple[float, float, float] | None = None   # (t0, t1, amount)
    punches: list[Punch] = field(default_factory=list)
    shakes: list[tuple[float, float, float]] = field(default_factory=list)  # (t, amp_px, dur)
    flashes: list[float] = field(default_factory=list)
    sfx: list[tuple[str, float]] = field(default_factory=list)
    desat: tuple[float, float] | None = None   # (t0, t_break)
    music: tuple[Path, float, float] | None = None  # (file, t_in, t_out)
    bleeps: list[tuple[float, float]] = field(default_factory=list)
    cards: list[tuple[float, float, str]] = field(default_factory=list)
    ending: str = "hard_cut"
    tail: float = 0.0                      # freeze length appended at the end

    def describe(self) -> list[str]:
        out = []
        if self.push:
            out.append(f"push-in {self.push[0]:.1f}-{self.push[1]:.1f}s")
        out += [f"zoom {p.amount + 1:.2f}x @ {p.t:.1f}s" for p in self.punches]
        out += [f"shake @ {t:.1f}s" for t, _, _ in self.shakes]
        out += [f"flash merah @ {t:.1f}s" for t in self.flashes]
        out += [f"sfx {n} @ {t:.1f}s" for n, t in self.sfx]
        if self.desat:
            out.append(f"hitam-putih {self.desat[0]:.1f}-{self.desat[1]:.1f}s")
        if self.music:
            out.append(f"musik {self.music[0].name} {self.music[1]:.1f}-{self.music[2]:.1f}s")
        out += [f"bleep @ {a:.1f}s" for a, _ in self.bleeps]
        if len(self.segments) > 1:
            out.append(f"jump cut {len(self.segments) - 1}x")
        out.append(f"ending {self.ending}")
        return out


def compress_gaps(words: list[dict], duration: float, protect=(), min_out: float = MIN_OUT) -> list[tuple[float, float]]:
    """Kept spans after squeezing silences > GAP_MAX between words (clip-relative).
    Stops cutting once the clip would get shorter than min_out."""
    cuts = []
    removed = 0.0
    prev_end = 0.0
    for w in list(words) + [{"start": duration, "end": duration}]:
        gap_a, gap_b = prev_end, w["start"]
        for pa, pb in protect:  # the build-up before a peak is never squeezed
            if pa < gap_b and pb > gap_a:
                gap_b = min(gap_b, pa)
        cut = gap_b - gap_a - 2 * GAP_KEEP
        if gap_b - gap_a > GAP_MAX and duration - removed - cut >= min_out:
            cuts.append((gap_a + GAP_KEEP, gap_b - GAP_KEEP))
            removed += cut
        prev_end = max(prev_end, w["end"])
    spans, cur = [], 0.0
    for a, b in cuts:
        spans.append((cur, a))
        cur = b
    spans.append((cur, duration))
    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a > 0.05]


def remap(t: float, segments: list[tuple[float, float]]) -> float:
    """Clip-relative time -> output time after jump cuts."""
    out = 0.0
    for a, b in segments:
        if t < a:
            return out
        if t <= b:
            return out + (t - a)
        out += b - a
    return out


def snap_end(words: list[dict], t: float, cap: float) -> float:
    """Never end inside a word or a running phrase: extend to where talking stops."""
    end = t
    for w in words:
        if w["start"] <= end + 0.25 and w["end"] > end:
            end = w["end"]
    return min(end, cap)


def build_plan(beats: dict, words: list[dict], level: str, censor: str, music: Path | None) -> Plan:
    """beats (clip-relative seconds): kind, peak, reaction_end, trigger, break_s,
    outbursts [(t, rank)], ending, cards [(t, dur, text)], duration.
    words are clip-relative and are edited in place (rage/display marks, remapped times)."""
    D = float(beats["duration"])
    kind = beats.get("kind", "other") if level == "full" else "other"
    peak = min(max(0.3, float(beats.get("peak", D * 0.7))), D - 0.2)
    plan = Plan(kind=kind)

    bleeps = []
    for w in words:
        n = _norm(w["word"])
        if n in RAGE_WORDS:
            w["rage"] = True
        heavy = n in HEAVY_SWEARS or (censor == "all" and n in MEDIUM_SWEARS)
        if censor != "off" and heavy:
            bleeps.append((max(0.0, w["start"] - 0.03), w["end"] + 0.03))
            w["display"] = mask(w["word"])

    # Only talk-driven moments are squeezed; "other" can be a silent horror
    # build-up where the quiet IS the setup. Never cut from the trigger on.
    trig = beats.get("trigger")
    guard = float(trig) - 1.0 if trig is not None else peak - (2 if kind == "fake_cry" else 4)
    protect = [(max(0.0, min(guard, peak - 2)), D)]
    segs = compress_gaps(words, D, protect, min(MIN_OUT, D)) if kind in ("rage", "fake_cry") else [(0.0, D)]
    plan.segments = segs
    R = lambda t: remap(t, segs)  # noqa: E731
    out_D = R(D)
    plan.duration = out_D
    plan.bleeps = [(R(a), R(b)) for a, b in bleeps]
    for w in words:
        w["start"], w["end"] = R(w["start"]), R(w["end"])
    peak_o = R(peak)

    if level == "off":
        return plan

    if kind == "rage":
        plan.push = (0.0, max(0.5, peak_o - 0.1), 0.08)
        react = R(float(beats.get("reaction_end", peak + 1.2)))
        hold = min(out_D - 0.1, max(peak_o + 0.8, react - 0.2))
        plan.punches.append(Punch(peak_o, 0.32, 0.12, hold, 0.4, "out_cubic"))
        plan.shakes.append((peak_o, 24.0, 0.5))
        plan.flashes.append(peak_o)
        plan.sfx.append(("boom", max(0.0, peak_o - 0.05)))
        for t, rank in beats.get("outbursts", []):
            to = R(float(t))
            if int(rank) > 1 and len(plan.punches) < 3 and 0.3 < to < out_D - 0.3 \
                    and all(abs(to - p.t) >= 2.2 for p in plan.punches):
                plan.punches.append(Punch(to, 0.12, 0.12, to + 0.6, 0.35, "out_cubic"))
        plan.ending = beats.get("ending") if beats.get("ending") in ("hard_cut", "freeze_sting") else "hard_cut"
    elif kind == "fake_cry":
        t0 = R(float(beats.get("trigger", max(0.0, peak - 6))))
        brk = beats.get("break_s")
        b = R(float(brk)) if brk is not None else out_D
        b = min(out_D, max(b, t0 + 2.0))
        plan.desat = (t0, b)
        plan.push = (t0, min(b, t0 + 8.0), 0.18)
        if brk is not None and b < out_D - 0.3:
            plan.punches.append(Punch(b + 0.15, 0.3, 0.15, min(out_D - 0.1, b + 1.2), 0.4, "out_back"))
            plan.sfx.append(("scratch", max(0.0, b - 0.05)))
            plan.ending = "break_character_cut"
            t_out = b
        else:
            plan.ending = "freeze_fade"
            plan.tail = 2.0
            t_out = out_D + plan.tail
        if music:
            plan.music = (music, max(0.0, t0 - 0.6), t_out)
        plan.cards = [(R(float(t)), float(d), str(txt)) for t, d, txt in beats.get("cards", [])[:2]]
    else:
        plan.push = (0.0, max(0.5, peak_o - 0.1), 0.06)
        plan.punches.append(Punch(peak_o, 0.22, 0.15, min(out_D - 0.1, peak_o + 1.0), 0.4, "out_cubic"))
        plan.sfx.append(("boom", max(0.0, peak_o - 0.05)))
        if beats.get("ending") == "freeze_sting":
            plan.ending = "freeze_sting"

    if plan.ending == "freeze_sting":
        plan.tail = 0.7
        plan.sfx.append(("boom", out_D))
    return plan


# ------------------------------------------------------------------ ffmpeg expressions
def _clip(x: str) -> str:
    return f"clip({x},0,1)"


def _ease_in(p: str, ease: str) -> str:
    if ease == "out_back":  # overshoots ~10 % then settles
        return f"(1+2.70158*pow({p}-1,3)+1.70158*pow({p}-1,2))"
    return f"(1-pow(1-{p},3))"  # ease-out cubic: fast, then lands softly


def zoom_expr(plan: Plan) -> str:
    """Total zoom factor Z(t) = (1 + push(t)) * (1 + sum of punches)."""
    parts = []
    if plan.push:
        t0, t1, a = plan.push
        p = _clip(f"(t-{t0:.3f})/{max(0.1, t1 - t0):.3f}")
        parts.append(f"(1+{a}*({p}*{p}*(3-2*{p})))")  # smoothstep Ken Burns
    terms = []
    for pu in plan.punches:
        pin = _clip(f"(t-{pu.t - pu.rise:.3f})/{pu.rise:.3f}")
        pout = _clip(f"(t-{pu.hold_until:.3f})/{pu.release:.3f}")
        terms.append(f"{pu.amount}*{_ease_in(pin, pu.ease)}*(0.5+0.5*cos(PI*{pout}))")
    if terms:
        parts.append(f"(1+{'+'.join(terms)})")
    return "*".join(parts) or "1"


def cam_chain(plan: Plan, w: int = 1080, h: int = 790, fx: float = 0.5, fy: float = 0.42) -> str:
    """Zoom + shake on the facecam panel, anchored at (fx, fy) of the panel.

    * scale with eval=frame + FIXED-size crop (crop w/h expressions don't animate)
    * x/y recompute the scaled size from t: inside crop, iw/ih keep their
      initial values, so '(iw-w)/2' would zoom into the top-left corner
    * rendered at 2x and scaled down with flags=area: slow push-ins judder at 1x
    """
    if not plan.push and not plan.punches and not plan.shakes:
        return f"scale={w}:{h}:flags=lanczos"
    W, H = w * 2, h * 2
    Z = zoom_expr(plan)
    SW = f"(trunc({W}*{Z}/2)*2)"
    SH = f"(trunc({H}*{Z}/2)*2)"
    shake_x = shake_y = "0"
    if plan.shakes:
        sx, sy = [], []
        for t, amp, dur in plan.shakes:
            env = f"exp(-6*(t-{t:.3f}))*between(t,{t:.3f},{t + dur:.3f})"
            sx.append(f"{2 * amp}*sin(2*PI*15*t)*{env}")
            sy.append(f"{2 * amp}*cos(2*PI*13*t)*{env}")
        shake_x, shake_y = "+".join(sx), "+".join(sy)
    x = f"clip({fx}*({SW}-{W})+{shake_x},0,{SW}-{W})"
    y = f"clip({fy}*({SH}-{H})+{shake_y},0,{SH}-{H})"
    return (f"scale={W}:{H}:flags=bicubic,"
            f"scale=w='{SW}':h='{SH}':eval=frame:flags=bicubic,"
            f"crop={W}:{H}:x='{x}':y='{y}',scale={w}:{h}:flags=area")


def color_chain(plan: Plan) -> list[str]:
    """Full-canvas colour effects (after the panels are stacked)."""
    out = []
    if plan.desat:
        t0, b = plan.desat
        out.append(f"hue=s='1-{_clip(f'(t-{t0:.3f})/1.2')}*(1-gte(t,{b:.3f}))'")
        out.append(f"vignette=angle=PI/4:enable='between(t,{t0 + 0.6:.3f},{b:.3f})'")
    return out
