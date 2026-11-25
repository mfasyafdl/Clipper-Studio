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


def _w(text, t0, step=0.4):
    return [{"word": w, "start": t0 + i * step, "end": t0 + i * step + 0.35} for i, w in enumerate(text.split())]


def test_rage_plan_holds_zoom_bleeps_and_squeezes_dead_air():
    words = _w("ayo jalan dulu", 1.0) + _w("eh kok mati BANGSAT anjing woi", 9.0)  # 3.0..9.0 = dead air
    beats = {"duration": 14.0, "kind": "rage", "peak": 10.6, "reaction_end": 11.8,
             "outbursts": [(10.6, 1), (11.4, 2)], "ending": "hard_cut"}
    plan = effects.build_plan({**beats, "duration": 24.0}, [dict(w) for w in words], "full", "heavy", None)
    assert len(plan.segments) == 2 and 15.0 <= plan.duration < 24.0  # gap squeezed, clip stays >= 15 s
    plan = effects.build_plan(beats, words, "full", "heavy", None)
    assert len(plan.segments) == 1  # 14 s clip: nothing left to squeeze
    main = plan.punches[0]
    assert main.amount >= 0.3 and main.hold_until - main.t >= 0.8  # held, not a 0.3 s bump
    assert len(plan.punches) == 1  # the rank-2 outburst is too close (< 2.2 s)
    assert plan.flashes and plan.shakes and ("boom", plan.flashes[0] - 0.05) in plan.sfx
    assert len(plan.bleeps) == 1  # bangsat bleeped, anjing only red (CENSOR=heavy)
    by = {w["word"]: w for w in words}
    assert by["BANGSAT"]["display"] == "BANGS*T" and by["anjing"].get("rage") and "display" not in by["anjing"]
    assert plan.ending == "hard_cut" and plan.tail == 0
    z = effects.zoom_expr(plan)
    assert "cos(PI*" in z and "pow(1-" in z


def test_fake_cry_plan_desaturates_and_snaps_back_on_break(tmp_path):
    words = _w("huhu aku sedih banget kalah terus", 2.0, 0.8) + _w("wkwk becanda", 9.5)
    music = tmp_path / "sad.mp3"
    beats = {"duration": 12.0, "kind": "fake_cry", "peak": 5.0, "trigger": 2.0, "break_s": 9.5,
             "cards": [(3.0, 2.0, "dia kehilangan segalanya...")]}
    plan = effects.build_plan(beats, words, "full", "heavy", music)
    assert plan.desat == (2.0, 9.5) and plan.music == (music, 1.4, 9.5)
    assert plan.punches[0].ease == "out_back" and ("scratch", 9.45) in plan.sfx
    assert plan.ending == "break_character_cut" and plan.cards
    assert "hue=s=" in effects.color_chain(plan)[0]
    light = effects.build_plan(beats, _w("huhu", 2.0), "light", "heavy", music)
    assert light.kind == "other" and light.music is None
    off = effects.build_plan(beats, _w("huhu", 2.0), "off", "heavy", music)
    assert not off.punches and not off.push


def test_fake_cry_without_break_freezes_and_fades():
    plan = effects.build_plan({"duration": 10.0, "kind": "fake_cry", "peak": 6.0, "trigger": 3.0},
                              _w("huhu sedih", 3.0), "full", "heavy", None)
    assert plan.ending == "freeze_fade" and plan.tail == 2.0 and plan.music is None


def test_snap_end_and_mask():
    words = _w("kok bisa gitu sih anjir", 10.0)
    assert effects.snap_end(words, 10.5, 30) == pytest.approx(words[-1]["end"])
    assert effects.mask("ANJING") == "ANJ*NG"
    assert moments.clean_text("WINDAH: ANJING LAH 😡") == "WINDAH: ANJ*NG LAH 😡"


def test_finalize_bounds():
    c = moments.finalize({"start": 0, "payoff": 100, "end": 130}, 3600)
    assert c["end"] == 115 and 15 <= c["end"] - c["start"] <= 45
    c = moments.finalize({"start": 10, "payoff": 90, "end": 91}, 3600)
    assert c["end"] - c["start"] <= 45
    # rage: the end follows the rant, not the first swear word
    words = _w("anjing apaan sih ini game sampah banget woi", 100.0)
    c = moments.finalize({"start": 88, "payoff": 100, "end": 101, "kind": "rage",
                          "beats": {"reaction_end": 103.0}}, 3600, words)
    assert c["end"] >= words[-1]["end"]


def test_parse_stage_b_beats():
    r = moments.parse_stage_b({"keep": True, "moment_type": "rage", "category": "funny_fail", "start_s": 30,
                               "payoff_s": 45, "end_s": 50, "reaction_end_s": 49.5,
                               "outbursts": [{"t": 45, "rank": 1}, {"t": "x"}], "ending": "freeze_sting",
                               "cards": [{"t": 1, "text": ""}], "break_character_s": None}, 100.0, {})
    assert r["kind"] == "rage" and r["category"] == "rage"
    assert r["beats"]["reaction_end"] == 149.5 and r["beats"]["outbursts"] == [(145.0, 1)]
    assert r["beats"]["break_character"] is None and r["beats"]["cards"] == []
    assert r["beats"]["ending"] == "freeze_sting"


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
    def __init__(self, kind="rage"):
        self.calls = 0
        self.kind = kind

    def generate_content(self, model, contents, config):
        self.calls += 1
        if isinstance(contents, str):  # stage A
            return type("R", (), {"text": json.dumps({"items": [
                {"id": i, "keep": True, "category": "jumpscare", "punchline": "kaget", "score": 90} for i in range(5)]})})
        return type("R", (), {"text": json.dumps({  # stage B, relative to preview start (peak-45)
            "keep": True, "moment_type": self.kind, "category": "rage", "start_s": 30, "trigger_s": 33,
            "payoff_s": 45, "end_s": 46.5, "reaction_end_s": 47.5, "break_character_s": 46.0,
            "outbursts": [{"t": 45, "rank": 1}, {"t": 41, "rank": 2}], "ending": "hard_cut",
            "cards": [{"t": 35, "dur": 2, "text": "dia kehilangan segalanya..."}],
            "title": "windah: bangsat game ini😡", "hook": "windah gak sadar", "punchline": "ngamuk",
            "score": 88})})


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return out


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.parametrize("kind", ["rage", "fake_cry"])
def test_gaming_end_to_end(tmp_path, monkeypatch, kind):
    job = tmp_path / "job"
    job.mkdir()
    _make_stream(job / "source_test.mp4", 120)
    # talk, 4 s of dead air (squeezed), then the rant around the burst at 60 s
    words = _w("ayo kita coba lagi", 46.0) + _w("eh ada apa itu BANGSAT anjing woi", 57.5, 0.5) + _w("udah lah", 61.5)
    (job / "words.json").write_text(json.dumps({"words": words}), encoding="utf-8")
    chat = job / "chat_test.live_chat.json"
    chat.write_text("\n".join([_chat_line(t, f"u{t % 7}", "hm") for t in range(0, 120, 3)] +
                              [_chat_line(70 + i * 0.05, f"v{i}", "WKWKWK anjir") for i in range(80)]), encoding="utf-8")
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=3", str(music_dir / "sad_piano.mp3")],
                   check=True)
    monkeypatch.setattr(effects, "MUSIC_DIR", music_dir)
    monkeypatch.setattr(effects, "SFX_DIR", tmp_path / "sfx")

    s = Settings(video_encoder="libx264", use_chat=True, gemini_video=True, effects="full", censor="heavy")
    s.llm_env = {"GEMINI_API_KEY": "AQ.test"}
    fake = _FakeModels(kind)
    monkeypatch.setattr(moments, "_client", lambda st: type("C", (), {"models": fake})())
    monkeypatch.setattr(layout, "ensure_yunet", lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    clips = pipeline.gaming_moments(job, job / "source_test.mp4", 1, s)
    assert len(clips) == 1
    c = clips[0]
    assert 55 <= c["payoff"] <= 65, c  # the loud burst + chat spike, not a random spot
    assert 15 <= c["end"] - c["start"] <= 45
    assert c["title"].endswith("MOMEN KOCAK WINDAH BASUDARA") and "BANGSAT" not in c["title"]
    assert c["kind"] == kind and c["beats"]["outbursts"]
    assert c["end"] >= words[-1]["end"]  # not cut in the middle of "udah lah"

    pipeline.render_captions(job, clips, words, s)
    assert c.get("final"), c.get("error")
    probe = _probe(job / c["final"])
    assert "video,1080,1920" in probe and "audio" in probe
    plan = " | ".join(c["applied_plan"])
    assert "zoom" in plan and "bleep" in plan, plan
    if kind == "fake_cry":
        assert "hitam-putih" in plan and "musik sad_piano.mp3" in plan and "sfx scratch" in plan
    else:
        assert "flash merah" in plan and "shake" in plan
    assert c["cam"]["found"] is False  # offline -> preset rectangle
    ass = (job / "final" / f"{c['id']}.ass").read_text(encoding="utf-8")
    assert "BANGS*T" in ass and "BANGSAT" not in ass
