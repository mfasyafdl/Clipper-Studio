"""Gaming engine: signals, layout math, edit plan, Gemini selection (mocked), full render."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipper import effects, layout, moments, pipeline, signals
from clipper.config import Settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _chat_line(t, author, text):
    return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(int(t * 1000)), "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": text}]}, "authorExternalChannelId": author}}}}]}})


def test_parse_live_chat_tolerates_partial_file(tmp_path):
    p = tmp_path / "x.live_chat.json"
    p.write_text(_chat_line(1.5, "a", "halo") + "\n" + _chat_line(2, "b", "WKWKWK") + "\n{\"replayChat", encoding="utf-8")
    msgs = signals.parse_live_chat(p)
    assert msgs == [(1.5, "a", "halo"), (2.0, "b", "WKWKWK")]


def test_chat_series_counts_unique_authors_and_laughs_with_lag():
    msgs = [(110 + i * 0.1, f"u{i % 20}", "wkwkwk") for i in range(60)] + [(50, "x", "halo")]
    s = signals.chat_series(msgs, 200)
    assert s["uniq"][100:108].max() == 20  # 20 unique authors, attributed 10 s earlier
    assert s["laugh"][100:108].max() == pytest.approx(1.0)
    assert s["uniq"][40] == 1


def test_pick_peaks_respects_gap_and_margin():
    score = np.zeros(600)
    score[[100, 120, 300, 595]] = [1.0, 0.9, 0.8, 1.0]
    assert signals.pick_peaks(score, 5) == [100, 300]


def test_cam_rect_median_and_fallback():
    preset = layout.PRESETS["windah"]
    faces = [(1700, 850, 100, 120), (1705, 852, 104, 120), (1698, 849, 98, 118), (300, 300, 400, 400)]
    r = layout.cam_rect_from_faces(faces, 1920, 1080, preset)
    assert r.found and r.w > r.h and r.x + r.w <= 1920 and r.y + r.h <= 1080
    assert abs(r.w / r.h - 1080 / 790) < 0.02
    fb = layout.cam_rect_from_faces(faces[:2], 1920, 1080, preset)
    assert not fb.found and fb.x == int(0.78 * 1920)


def test_validate_light_vs_full():
    plan = [{"t": 5, "type": "zoom"}, {"t": 6, "type": "zoom"}, {"t": 9, "type": "zoom"},
            {"t": 5, "type": "sfx", "name": "vineboom"}, {"t": 7, "type": "flash"}, {"t": 50, "type": "zoom"}]
    light = effects.validate(plan, 20, "light")
    assert [e["type"] for e in light] == ["zoom", "sfx"] and light[1]["name"] == "boom"
    full = effects.validate(plan, 20, "full")
    assert [e["t"] for e in full if e["type"] == "zoom"] == [5, 9]  # 6 too close, 50 out of range
    assert effects.validate(plan, 20, "off") == []


def test_finalize_bounds():
    c = moments.finalize({"start": 0, "payoff": 100, "end": 130}, 3600)
    assert c["end"] == 104 and c["end"] - c["start"] >= 15
    c = moments.finalize({"start": 10, "payoff": 90, "end": 91}, 3600)
    assert c["end"] - c["start"] <= 45


def test_quote_title_suffix():
    assert moments.quote_title("freddy: duluan bang🗿", " - MOMEN KOCAK WINDAH BASUDARA") == \
        "FREDDY: DULUAN BANG🗿 - MOMEN KOCAK WINDAH BASUDARA"


def _make_stream(path: Path, seconds: int):
    # quiet tone, then a loud burst at t=60..63 (the "scream")
    audio = (f"aevalsrc='if(between(t,60,63),0.9,0.02)*sin(2*PI*300*t)':s=16000:d={seconds}")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", audio, "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


class _FakeModels:
    def __init__(self):
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        if isinstance(contents, str):  # stage A
            return type("R", (), {"text": json.dumps({"items": [
                {"id": i, "keep": True, "category": "jumpscare", "punchline": "kaget", "score": 90} for i in range(5)]})})
        return type("R", (), {"text": json.dumps({  # stage B, relative to preview start (peak-45)
            "keep": True, "category": "jumpscare", "start_s": 30, "payoff_s": 45, "end_s": 46.5,
            "title": "hantu: duluan bang🗿", "hook": "windah gak sadar", "punchline": "kaget",
            "score": 88, "edit_plan": [{"t": 45, "type": "zoom"}, {"t": 45, "type": "sfx", "name": "boom"}]})})


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_gaming_end_to_end(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    _make_stream(job / "source_test.mp4", 120)
    words = [{"word": w, "start": 58 + i * 0.4, "end": 58.35 + i * 0.4}
             for i, w in enumerate("eh ada apa itu ANJIR".split())]
    (job / "words.json").write_text(json.dumps({"words": words}), encoding="utf-8")
    chat = job / "chat_test.live_chat.json"
    chat.write_text("\n".join([_chat_line(t, f"u{t % 7}", "hm") for t in range(0, 120, 3)] +
                              [_chat_line(70 + i * 0.05, f"v{i}", "WKWKWK anjir") for i in range(80)]), encoding="utf-8")

    s = Settings(video_encoder="libx264", use_chat=True, gemini_video=True, effects="light")
    s.llm_env = {"GEMINI_API_KEY": "AQ.test"}
    fake = _FakeModels()
    monkeypatch.setattr(moments, "_client", lambda st: type("C", (), {"models": fake})())
    monkeypatch.setattr(layout, "ensure_yunet", lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    clips = pipeline.gaming_moments(job, job / "source_test.mp4", 1, s)
    assert len(clips) == 1
    c = clips[0]
    assert 55 <= c["payoff"] <= 65, c  # the loud burst + chat spike, not a random spot
    assert 15 <= c["end"] - c["start"] <= 45
    assert c["title"].endswith("MOMEN KOCAK WINDAH BASUDARA")

    pipeline.render_captions(job, clips, words, s)
    assert c.get("final"), c.get("error")
    out = job / c["final"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert "video,1080,1920" in probe and "audio" in probe
    assert [e["type"] for e in c["applied_plan"]] == ["zoom", "sfx"]
    assert c["cam"]["found"] is False  # offline -> preset rectangle
