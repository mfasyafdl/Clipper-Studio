"""Orchestrates: download -> transcribe -> highlights+reframe -> captions -> review."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import captions, highlights, review, transcribe
from .config import Settings


def _probe_fps(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        num, den = out.split("/") if "/" in out else (out, "1")
        return round(float(num) / float(den), 3) or 30.0
    except Exception:
        return 30.0


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:40] or "video"


def download(url: str, job_dir: Path, max_height: int = 1080) -> Path:
    """yt-dlp download into the job folder; local files are copied as-is."""
    local = Path(url).expanduser()
    if local.exists():
        dest = job_dir / f"source_{_slug(local.stem)}{local.suffix.lower()}"
        if not dest.exists():
            shutil.copy2(local, dest)
        return dest
    existing = sorted(job_dir.glob("source_*.mp4"))
    if existing:
        return existing[0]
    import yt_dlp

    opts = {
        "format": f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(job_dir / "source_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
    }
    print(f"[download] {url}", flush=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    path = job_dir / f"source_{info['id']}.mp4"
    if not path.exists():
        found = sorted(job_dir.glob(f"source_{info['id']}.*"))
        if not found:
            raise RuntimeError("yt-dlp selesai tapi file video tidak ditemukan")
        path = found[0]
    (job_dir / "source_info.json").write_text(
        json.dumps({k: info.get(k) for k in ("id", "title", "uploader", "webpage_url", "duration")},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path


def new_job_dir(url: str, settings: Settings) -> Path:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})", url)
    name = m.group(1) if m else _slug(Path(url).stem)
    job = settings.jobs_dir / f"{datetime.now():%Y%m%d}-{name}"
    job.mkdir(parents=True, exist_ok=True)
    return job


def load_words(job_dir: Path) -> list[dict]:
    return json.loads((job_dir / "words.json").read_text(encoding="utf-8"))["words"]


def source_of(job_dir: Path) -> Path:
    found = [p for p in sorted(job_dir.glob("source_*")) if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not found:
        raise RuntimeError(f"Tidak ada video sumber di {job_dir}")
    return found[0]


def render_captions(job_dir: Path, clips: list[dict], words: list[dict], settings: Settings) -> None:
    final_dir = job_dir / "final"
    final_dir.mkdir(exist_ok=True)
    for c in clips:
        if not c.get("vertical"):
            continue
        version = c.get("version", 1)
        name = c["id"] if version == 1 else f"{c['id']}_v{version}"
        print(f"[captions] {name}: {c.get('title', '')}", flush=True)
        try:
            info = captions.caption_clip(
                job_dir / c["vertical"], words, c["start"], c["end"], c.get("hook", ""),
                final_dir / f"{name}.mp4", settings,
            )
            c.update({"final": f"final/{info['final']}", "srt": f"final/{info['srt']}",
                      "text": info["text"], "encoder": info["encoder"], "error": None})
        except Exception as e:  # keep going, show the failure in the review page
            print(f"[captions] {name} gagal: {e}", flush=True)
            c.update({"final": None, "error": str(e)})


def save_clips(job_dir: Path, clips: list[dict]) -> None:
    (job_dir / "clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=1), encoding="utf-8")


def load_clips(job_dir: Path) -> list[dict]:
    return json.loads((job_dir / "clips.json").read_text(encoding="utf-8"))


def job_title(job_dir: Path) -> str:
    info = job_dir / "source_info.json"
    if info.exists():
        return json.loads(info.read_text(encoding="utf-8")).get("title") or job_dir.name
    return job_dir.name


def refresh_review(job_dir: Path, clips: list[dict]) -> None:
    source = source_of(job_dir)
    review.write_review_page(job_dir, clips, job_title(job_dir))
    review.write_otio(job_dir, source, clips, _probe_fps(source))


def run(url: str, num_clips: int, settings: Settings) -> Path:
    job_dir = new_job_dir(url, settings)
    print(f"[job] folder: {job_dir}", flush=True)
    source = download(url, job_dir)
    transcribe.transcribe(source, job_dir, settings)
    clips = highlights.run_ays(source, job_dir, num_clips, settings)
    render_captions(job_dir, clips, load_words(job_dir), settings)
    save_clips(job_dir, clips)
    refresh_review(job_dir, clips)
    print(f"\n[job] selesai. Audit dengan:  .\\run.ps1 review \"{job_dir}\"", flush=True)
    return job_dir


def apply_decisions(job_dir: Path, settings: Settings) -> dict:
    """Apply decisions.json: re-cut trimmed clips, swap hooks, collect approved."""
    decisions = json.loads((job_dir / "decisions.json").read_text(encoding="utf-8"))
    clips = load_clips(job_dir)
    source = source_of(job_dir)
    words = load_words(job_dir)
    changed: list[dict] = []
    notes: list[str] = []

    for c in clips:
        d = decisions.get(c["id"]) or {}
        if d.get("verdict") == "reject":
            continue
        ts, te = float(d.get("trim_start") or 0), float(d.get("trim_end") or 0)
        note = (d.get("note") or "").strip()
        new_hook = None
        m = re.match(r"(?is)^hook\s*:\s*(.+)$", note)
        if m:
            new_hook = m.group(1).strip()
        elif note:
            notes.append(f"- {c['id']} ({c.get('title', '')}): {note}")

        if ts or te or new_hook:
            c["start"] = max(0.0, c["start"] + ts)
            c["end"] = max(c["start"] + 1.0, c["end"] + te)
            c["version"] = c.get("version", 1) + 1
            if new_hook:
                c["hook"] = new_hook
            if ts or te:
                vertical = f"short_{c['id']}_v{c['version']}.mp4"
                print(f"[apply] potong ulang {c['id']} -> {c['start']:.1f}s..{c['end']:.1f}s", flush=True)
                highlights.recrop(source, c["start"], c["end"], job_dir / vertical, settings)
                c["vertical"] = vertical
            changed.append(c)
            # the trim is now baked in; reset it so a second apply doesn't double it
            d["trim_start"] = d["trim_end"] = 0
            if new_hook:
                d["note"] = ""

    if changed:
        render_captions(job_dir, changed, words, settings)
    save_clips(job_dir, clips)
    (job_dir / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=1), encoding="utf-8")

    approved_dir = job_dir / "approved"
    approved_dir.mkdir(exist_ok=True)
    approved = []
    for c in clips:
        if (decisions.get(c["id"]) or {}).get("verdict") == "approve" and c.get("final"):
            dst = approved_dir / Path(c["final"]).name
            shutil.copy2(job_dir / c["final"], dst)
            approved.append(dst.name)
    if notes:
        (job_dir / "notes_for_claude.md").write_text(
            "# Catatan review yang perlu dikerjakan manual\n\n" + "\n".join(notes) + "\n", encoding="utf-8")
    refresh_review(job_dir, clips)
    return {"rerendered": [c["id"] for c in changed], "approved": approved, "notes": len(notes)}
