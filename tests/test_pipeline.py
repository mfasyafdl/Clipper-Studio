"""Offline tests: no GPU, no LLM, no network. Needs ffmpeg on PATH for the render tests."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipper import captions, pipeline, review
from clipper.config import Settings, load_env_file
from clipper.transcribe import fill_missing_times, segments_to_srt

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def W(word, start=None, end=None):
    d = {"word": word}
    if start is not None:
        d["start"], d["end"] = start, end
    return d


def test_fill_missing_times_interpolates_gaps():
    out = fill_missing_times([W("aku", 0.0, 0.4), W("123"), W("ribu"), W("rupiah", 1.0, 1.4), W("  ")])
    assert [w["word"] for w in out] == ["aku", "123", "ribu", "rupiah"]
    assert out[1]["start"] == 0.4 and out[2]["end"] == 1.0
    assert all("start" in w and "end" in w for w in out)


def test_fill_missing_times_at_edges():
    out = fill_missing_times([W("x"), W("ya", 2.0, 2.5), W("y")])
    assert out[0]["start"] <= out[0]["end"] <= 2.0
    assert out[2]["start"] >= 2.5


def test_group_words_breaks_on_count_pause_and_punct():
    words = [W("satu", 0, .2), W("dua,", .2, .4), W("tiga", .4, .6), W("empat", .6, .8),
             W("lima", .8, 1.0), W("enam", 1.0, 1.2), W("tujuh", 3.0, 3.2)]
    groups = [[w["word"] for w in g] for g in captions.group_words(words, max_words=3)]
    assert groups == [["satu", "dua,"], ["tiga", "empat", "lima"], ["enam"], ["tujuh"]]


def test_words_in_range_shifts_to_clip_time():
    words = [W("a", 9.0, 9.5), W("b", 10.2, 10.6), W("c", 14.8, 15.4), W("d", 16, 17)]
    got = captions.words_in_range(words, 10.0, 15.0)
    assert [w["word"] for w in got] == ["b", "c"]
    assert got[0]["start"] == pytest.approx(0.2) and got[1]["end"] == pytest.approx(5.0)


def test_build_ass_has_hook_and_one_event_per_word():
    s = Settings()
    words = [W("halo", 0, .3), W("semua", .3, .7), W("{x}", .7, 1.0)]
    ass = captions.build_ass(words, "Ini rahasia", 5.0, s)
    assert "Style: Hook" in ass and "INI RAHASIA" in ass
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue: 0")]
    assert len(dialogue) == 3
    assert "{x}" not in ass and "(X)" in ass  # braces escaped so libass doesn't eat them


def test_srt_and_env_parsing(tmp_path):
    srt = segments_to_srt([{"start": 0, "end": 1.5, "text": " halo "}, {"start": 61.25, "end": 3600, "text": "b"}])
    assert "00:00:01,500" in srt and "00:01:01,250 --> 01:00:00,000" in srt
    env = tmp_path / ".env"
    env.write_text("# c\nA=1\nB = 'dua'\nC=\"tiga\"\nrusak\n", encoding="utf-8")
    assert load_env_file(env) == {"A": "1", "B": "dua", "C": "tiga"}


def test_review_page_escapes_script_breakout(tmp_path):
    clips = [{"id": "clip_01", "start": 0, "end": 5, "title": "</script><b>x", "final": "final/clip_01.mp4"}]
    page = review.write_review_page(tmp_path, clips, "Judul <i>").read_text(encoding="utf-8")
    assert "</script><b>" not in page and "Judul &lt;i&gt;" in page


def _make_video(path: Path, seconds: int, size: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_end_to_end_captions_review_and_apply(tmp_path, monkeypatch):
    s = Settings(video_encoder="libx264")
    job = tmp_path / "job"
    job.mkdir()
    _make_video(job / "source_test.mp4", 20, "640x360")
    words = [W(f"kata{i}", 2 + i * 0.5, 2.4 + i * 0.5) for i in range(20)]
    (job / "words.json").write_text(json.dumps({"words": words}), encoding="utf-8")
    for n in (1, 2):
        _make_video(job / f"short_0{n}.mp4", 5, "202x360")
    clips = [
        {"id": "clip_01", "start": 2.0, "end": 7.0, "score": 9, "title": "Satu", "hook": "Hook satu", "vertical": "short_01.mp4"},
        {"id": "clip_02", "start": 8.0, "end": 13.0, "score": 7, "title": "Dua", "hook": "Hook dua", "vertical": "short_02.mp4"},
    ]
    pipeline.render_captions(job, clips, words, s)
    pipeline.save_clips(job, clips)
    pipeline.refresh_review(job, clips)

    for c in clips:
        assert c["final"] and (job / c["final"]).exists(), c.get("error")
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-of", "csv=p=0", str(job / c["final"])],
                               capture_output=True, text=True).stdout.strip()
        assert probe == "1080,1920"
    assert (job / "review.html").exists()

    # simulate the audit: keep clip 1 with a trim + new hook, drop clip 2
    (job / "decisions.json").write_text(json.dumps({
        "clip_01": {"verdict": "approve", "trim_start": -1, "trim_end": 1, "note": "hook: Hook baru!"},
        "clip_02": {"verdict": "reject", "trim_start": 0, "trim_end": 0, "note": ""},
    }), encoding="utf-8")

    def fake_recrop(source, start, end, out_path, settings):
        _make_video(out_path, int(round(end - start)), "202x360")

    monkeypatch.setattr(pipeline.highlights, "recrop", fake_recrop)
    result = pipeline.apply_decisions(job, s)
    assert result == {"rerendered": ["clip_01"], "approved": ["clip_01_v2.mp4"], "notes": 0}
    c1 = pipeline.load_clips(job)[0]
    assert (c1["start"], c1["end"], c1["hook"]) == (1.0, 8.0, "Hook baru!")
    assert "HOOK BARU!" in (job / "final" / "clip_01_v2.ass").read_text(encoding="utf-8")
    # trims are consumed, so running apply twice doesn't move the clip again
    assert json.loads((job / "decisions.json").read_text())["clip_01"]["trim_start"] == 0


def test_review_server_saves_decisions(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    handler = lambda *a, **kw: review._Handler(*a, directory=str(tmp_path), **kw)  # noqa: E731
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/decisions"
        req = urllib.request.Request(url, data=b'{"clip_01":{"verdict":"approve"}}', method="POST")
        assert urllib.request.urlopen(req).status == 204
        assert json.loads((tmp_path / "decisions.json").read_text())["clip_01"]["verdict"] == "approve"
        bad = urllib.request.Request(url, data=b"not json", method="POST")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
    finally:
        srv.shutdown()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_retry_failed_recrops_missing_clips(tmp_path, monkeypatch):
    s = Settings(video_encoder="libx264")
    job = tmp_path / "job"
    job.mkdir()
    _make_video(job / "source_x.mp4", 12, "640x360")
    (job / "words.json").write_text(json.dumps({"words": [W("halo", 1, 1.5)]}), encoding="utf-8")
    pipeline.save_clips(job, [{"id": "clip_01", "start": 1.0, "end": 4.0, "hook": "h", "vertical": None,
                               "final": None, "error": "mp4v crash"}])
    monkeypatch.setattr(pipeline.highlights, "recrop",
                        lambda src, a, b, out, st: _make_video(out, int(b - a), "202x360"))
    assert pipeline.retry_failed(job, s) == ["clip_01"]
    assert (job / "final" / "clip_01.mp4").exists()
