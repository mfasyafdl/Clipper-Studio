"""Moment candidates from what the audience and the streamer DO, not only what is said.

Signals, one value per second of the VOD:
  * chat   unique chat authors per 8 s window (YouTube live-chat replay),
           shifted back ~10 s because chat reacts after the moment.
           Share of laugh / shock messages labels the moment.
  * audio  loudness (RMS dB) z-score plus a "quiet -> explosion" burst shape,
           which is what a jumpscare or a rage scream looks like.
  * heat   YouTube "Most Replayed" heatmap when the VOD has one.
  * words  reaction words in the WhisperX transcript (anjir, wkwk, aaa, ...).

Fused into one score, smoothed, and the top peaks (>= 60 s apart) become the
candidates that Gemini judges later. Weights are starting heuristics.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

LAUGH_RE = re.compile(r"(w+k+w+k*|k+a+k+a+|h+a+h+a+|ngakak|wkwk|lol|lmao|xixi|😂|🤣|😹)", re.I)
SHOCK_RE = re.compile(r"(anjir|anjay|anjrit|njir|buset|astaga|astagfirullah|kaget|waduh|bjir|gila|😱|😨|💀|😭)", re.I)
REACTION_WORDS = re.compile(
    r"^(anjir|anjay|njir|bjir|buset|astaga|astagfirullah|waduh|woi|woy|aa+|ha+|wkwk\w*|hah|eh|anjing|bangsat|gila|mampus|tolong|lari)$",
    re.I,
)

CHAT_LAG_S = 10
CHAT_WINDOW_S = 8
WEIGHTS = {"chat": 0.35, "audio": 0.30, "burst": 0.15, "heat": 0.10, "words": 0.10}


# ---------------------------------------------------------------- chat
def parse_live_chat(path: Path) -> list[tuple[float, str, str]]:
    """(seconds, author_id, text) per message from yt-dlp's live_chat.json (JSON lines).

    Tolerates a partially written file: the last line may be cut off.
    """
    out: list[tuple[float, str, str]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            replay = obj.get("replayChatItemAction") or {}
            try:
                t = int(replay.get("videoOffsetTimeMsec", -1)) / 1000.0
            except (TypeError, ValueError):
                continue
            if t < 0:
                continue
            for action in replay.get("actions", []):
                item = (action.get("addChatItemAction") or {}).get("item") or {}
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not r:
                    continue
                parts = []
                for run in (r.get("message") or {}).get("runs", []):
                    if "text" in run:
                        parts.append(run["text"])
                    elif "emoji" in run:
                        e = run["emoji"]
                        parts.append(e.get("emojiId", "") if len(e.get("emojiId", "")) <= 2 else
                                     " ".join(e.get("shortcuts", [])[:1]))
                author = r.get("authorExternalChannelId") or (r.get("authorName") or {}).get("simpleText", "")
                out.append((t, author, " ".join(parts)))
    return out


def chat_series(messages: list[tuple[float, str, str]], duration: int) -> dict[str, np.ndarray]:
    n = max(1, duration)
    authors_per_sec: list[set] = [set() for _ in range(n)]
    laugh = np.zeros(n)
    shock = np.zeros(n)
    count = np.zeros(n)
    for t, author, text in messages:
        i = int(t) - CHAT_LAG_S  # attribute the reaction to the moment that caused it
        if not 0 <= i < n:
            continue
        authors_per_sec[i].add(author)
        count[i] += 1
        if LAUGH_RE.search(text):
            laugh[i] += 1
        if SHOCK_RE.search(text):
            shock[i] += 1
    # unique authors in a trailing window (spam-resistant)
    uniq = np.zeros(n)
    for i in range(n):
        lo = max(0, i - CHAT_WINDOW_S + 1)
        uniq[i] = len(set().union(*authors_per_sec[lo:i + 1]))
    win = np.ones(CHAT_WINDOW_S)
    cnt_w = np.convolve(count, win, "full")[:n]
    laugh_ratio = np.divide(np.convolve(laugh, win, "full")[:n], cnt_w, out=np.zeros(n), where=cnt_w > 0)
    shock_ratio = np.divide(np.convolve(shock, win, "full")[:n], cnt_w, out=np.zeros(n), where=cnt_w > 0)
    return {"uniq": uniq, "laugh": laugh_ratio, "shock": shock_ratio}


# ---------------------------------------------------------------- audio
def loudness_db(source: Path, rate: int = 8000, hop_s: float = 0.5) -> np.ndarray:
    """RMS loudness in dB per hop, from a mono 8 kHz decode (fast, CPU)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        capture_output=True, check=True,
    )
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop = int(rate * hop_s)
    frames = len(pcm) // hop
    if frames == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(pcm[: frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-10)
    return 20 * np.log10(rms)


def per_second(values: np.ndarray, hop_s: float, duration: int, how=np.max) -> np.ndarray:
    per = int(round(1 / hop_s))
    out = np.full(duration, values.min() if len(values) else 0.0)
    for i in range(min(duration, len(values) // per)):
        out[i] = how(values[i * per:(i + 1) * per])
    return out


def burst_shape(db: np.ndarray) -> np.ndarray:
    """max loudness in next 2 s minus mean of previous 5 s (quiet -> explosion)."""
    n = len(db)
    out = np.zeros(n)
    for i in range(n):
        before = db[max(0, i - 5):i]
        after = db[i:min(n, i + 2)]
        if len(before) and len(after):
            out[i] = after.max() - before.mean()
    return out


# ---------------------------------------------------------------- fusion
def robust_z(x: np.ndarray, window: int = 600) -> np.ndarray:
    """z-score against a rolling median/MAD (±window/2 s), so slow drifts don't count."""
    n = len(x)
    if n == 0:
        return x
    z = np.zeros(n)
    step = 30  # recompute the baseline every 30 s, cheap enough for 3-hour VODs
    for s in range(0, n, step):
        lo, hi = max(0, s - window // 2), min(n, s + window // 2)
        seg = x[lo:hi]
        med = np.median(seg)
        mad = np.median(np.abs(seg - med)) * 1.4826 + 1e-6
        z[s:s + step] = (x[s:s + step] - med) / mad
    return z


def norm01(z: np.ndarray, cap: float = 4.0) -> np.ndarray:
    return np.clip(z, 0, cap) / cap


def word_series(words: list[dict], duration: int) -> np.ndarray:
    out = np.zeros(max(1, duration))
    for w in words:
        token = re.sub(r"[^\w]", "", str(w.get("word", "")).lower())
        if token and REACTION_WORDS.match(token):
            i = int(w["start"])
            if 0 <= i < len(out):
                out[i] += 1
    return np.convolve(out, np.ones(5), "same")


def heat_series(heatmap: list[dict] | None, duration: int) -> np.ndarray:
    out = np.zeros(max(1, duration))
    for h in heatmap or []:
        a, b = int(h.get("start_time", 0)), int(h.get("end_time", 0))
        out[max(0, a):min(duration, b)] = float(h.get("value", 0))
    return out


def fuse(parts: dict[str, np.ndarray], duration: int) -> np.ndarray:
    weights = {k: w for k, w in WEIGHTS.items() if k in parts and np.any(parts[k])}
    total = sum(weights.values()) or 1.0
    score = np.zeros(duration)
    for k, w in weights.items():
        score += (w / total) * parts[k][:duration]
    return np.convolve(score, np.ones(5) / 5, "same")


def pick_peaks(score: np.ndarray, k: int, min_gap: int = 60, margin: int = 30) -> list[int]:
    order = np.argsort(score)[::-1]
    picked: list[int] = []
    for i in order:
        if score[i] <= 0:
            break
        if i < margin or i > len(score) - 10:
            continue
        if all(abs(int(i) - p) >= min_gap for p in picked):
            picked.append(int(i))
            if len(picked) >= k:
                break
    return sorted(picked)


def find_candidates(
    source: Path, words: list[dict], duration: float, k: int,
    chat_path: Path | None = None, heatmap: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    d = int(duration)
    print("[signals] mengukur loudness audio", flush=True)
    db = per_second(loudness_db(source), 0.5, d)
    burst = burst_shape(db)
    parts = {
        "audio": norm01(robust_z(db)),
        "burst": norm01((burst - 6) / 2),  # >= ~8-10 dB jump starts to count
        "words": norm01(word_series(words, d), cap=3),
    }
    chat = None
    if chat_path and chat_path.exists():
        msgs = parse_live_chat(chat_path)
        print(f"[signals] {len(msgs)} pesan chat", flush=True)
        if len(msgs) > 50:
            chat = chat_series(msgs, d)
            chat["z"] = robust_z(chat["uniq"])
            parts["chat"] = norm01(chat["z"])
    if heatmap:
        parts["heat"] = heat_series(heatmap, d)
    score = fuse(parts, d)
    peaks = pick_peaks(score, k)

    cands = []
    for p in peaks:
        lo, hi = max(0, p - 3), min(d, p + 4)
        info = {
            "peak": float(p),
            "score": round(float(score[p]), 3),
            "loud_db": round(float(db[lo:hi].max()), 1),
            "burst_db": round(float(burst[lo:hi].max()), 1),
        }
        if chat is not None:
            info["chat_z"] = round(float(chat["z"][lo:hi].max()), 1)
            info["laugh"] = round(float(chat["laugh"][lo:hi].max()), 2)
            info["shock"] = round(float(chat["shock"][lo:hi].max()), 2)
        cands.append(info)
    debug = {"signals": sorted(parts), "duration": d}
    return cands, debug


def describe(c: dict) -> str:
    bits = [f"skor sinyal {c['score']}", f"loudness {c['loud_db']} dB", f"lonjakan {c['burst_db']} dB"]
    if "chat_z" in c:
        bits.append(f"chat z={c['chat_z']} (tawa {int(c['laugh'] * 100)}%, kaget {int(c['shock'] * 100)}%)")
    return ", ".join(bits)
