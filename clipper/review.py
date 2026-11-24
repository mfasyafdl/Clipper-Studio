"""Audit tools: a local review page and a timeline for DaVinci Resolve.

* review.html  every clip with player, score, hook, transcript, and
               approve/reject + trim + notes. "Simpan" writes decisions.json
               straight into the job folder (through the tiny local server),
               so Claude can read your verdict and re-render.
* timeline.otio  the clips as segments of the original 16:9 video, for
               File > Import > Timeline in DaVinci Resolve (free works).
"""
from __future__ import annotations

import html
import json
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = """<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"><title>Review klip: __TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark;--bg:#111;--card:#1c1c1e;--line:#2c2c2e;--ok:#30d158;--no:#ff453a;--mut:#98989d}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,Segoe UI,sans-serif;background:var(--bg);color:#eee}
header{position:sticky;top:0;z-index:2;background:#000c;backdrop-filter:blur(8px);padding:12px 20px;display:flex;gap:16px;align-items:center;border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;flex:1}#status{color:var(--mut);font-size:13px}
button{font:inherit;border:1px solid var(--line);background:#2c2c2e;color:#eee;border-radius:8px;padding:6px 12px;cursor:pointer}
button.primary{background:#0a84ff;border-color:#0a84ff}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px;padding:20px}
.card{background:var(--card);border:2px solid var(--line);border-radius:14px;padding:12px;display:flex;flex-direction:column;gap:8px}
.card.approve{border-color:var(--ok)}.card.reject{border-color:var(--no);opacity:.6}
video{width:100%;aspect-ratio:9/16;background:#000;border-radius:10px}
.meta{display:flex;justify-content:space-between;color:var(--mut);font-size:13px}
.title{font-weight:650}.hook{color:#ffd60a}.text{color:var(--mut);font-size:13px;max-height:6em;overflow:auto}
.row{display:flex;gap:8px;align-items:center}.row label{font-size:13px;color:var(--mut)}
.row input[type=number]{width:70px;background:#000;color:#eee;border:1px solid var(--line);border-radius:6px;padding:4px}
textarea{width:100%;min-height:52px;background:#000;color:#eee;border:1px solid var(--line);border-radius:8px;padding:6px;font:inherit}
.verdict button.on.approve{background:var(--ok);color:#000}.verdict button.on.reject{background:var(--no)}
.err{color:var(--no)}
</style></head><body>
<header><h1>Review klip: __TITLE__</h1><span id="status"></span>
<button class="primary" onclick="save()">Simpan keputusan</button></header>
<main id="grid"></main>
<script>
const CLIPS = __CLIPS__;
const KEY = "review:" + __JOB__;
let D = Object.assign({}, __DECISIONS__, JSON.parse(localStorage.getItem(KEY) || "{}"));
function fmt(t){t=Math.max(0,t);return Math.floor(t/60)+":"+String((t%60).toFixed(1)).padStart(4,"0")}
function render(){
  const g = document.getElementById("grid"); g.innerHTML = "";
  for (const c of CLIPS){
    const d = D[c.id] || (D[c.id] = {verdict:"", trim_start:0, trim_end:0, note:""});
    const el = document.createElement("div"); el.className = "card " + d.verdict;
    el.innerHTML = `
      ${c.final ? `<video controls preload="metadata" src="${c.final}"></video>` : `<div class="err">Render gagal: ${c.error||"?"}</div>`}
      <div class="meta"><span>${c.id} · skor ${c.score ?? "?"}</span><span>${fmt(c.start)} → ${fmt(c.end)} (${(c.end-c.start).toFixed(0)} dtk)</span></div>
      <div class="title"></div><div class="hook"></div><div class="text"></div>
      <div class="row verdict"><button class="approve ${d.verdict=="approve"?"on":""}" data-v="approve">✓ Pakai</button>
        <button class="reject ${d.verdict=="reject"?"on":""}" data-v="reject">✕ Buang</button></div>
      <div class="row"><label>Geser awal (dtk)</label><input type="number" step="0.5" data-k="trim_start" value="${d.trim_start}">
        <label>Geser akhir (dtk)</label><input type="number" step="0.5" data-k="trim_end" value="${d.trim_end}"></div>
      <textarea data-k="note" placeholder="Catatan untuk Claude, mis. 'ganti hook jadi ...'"></textarea>`;
    el.querySelector(".title").textContent = c.title || "";
    el.querySelector(".hook").textContent = c.hook ? "Hook: " + c.hook : "";
    el.querySelector(".text").textContent = c.text || "";
    el.querySelector("textarea").value = d.note || "";
    el.querySelectorAll("[data-v]").forEach(b => b.onclick = () => { d.verdict = d.verdict == b.dataset.v ? "" : b.dataset.v; persist(); render(); });
    el.querySelectorAll("[data-k]").forEach(i => i.oninput = () => { d[i.dataset.k] = i.type == "number" ? parseFloat(i.value || 0) : i.value; persist(); });
    g.appendChild(el);
  }
  const n = CLIPS.length, a = CLIPS.filter(c => (D[c.id]||{}).verdict == "approve").length, r = CLIPS.filter(c => (D[c.id]||{}).verdict == "reject").length;
  document.getElementById("status").textContent = `${a} dipakai · ${r} dibuang · ${n-a-r} belum`;
}
function persist(){ localStorage.setItem(KEY, JSON.stringify(D)); }
async function save(){
  const body = JSON.stringify(D, null, 1);
  try {
    const res = await fetch("decisions", {method:"POST", headers:{"Content-Type":"application/json"}, body});
    if (!res.ok) throw new Error(res.status);
    document.getElementById("status").textContent = "Tersimpan ke decisions.json ✓";
  } catch (e) {
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([body], {type:"application/json"}));
    a.download = "decisions.json"; a.click();
    document.getElementById("status").textContent = "Server mati: decisions.json diunduh. Pindahkan ke folder job.";
  }
}
render();
</script></body></html>
"""


def write_review_page(job_dir: Path, clips: list[dict], title: str) -> Path:
    decisions_path = job_dir / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.exists() else {}

    def js(value) -> str:  # JSON that is safe inside a <script> block
        return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")

    page = (
        PAGE.replace("__TITLE__", html.escape(title))
        .replace("__CLIPS__", js(clips))
        .replace("__JOB__", js(job_dir.name))
        .replace("__DECISIONS__", js(decisions))
    )
    out = job_dir / "review.html"
    out.write_text(page, encoding="utf-8")
    return out


def write_otio(job_dir: Path, source: Path, clips: list[dict], fps: float, only_approved: dict | None = None) -> Path | None:
    """Timeline of the source segments, for import into DaVinci Resolve."""
    try:
        import opentimelineio as otio
    except ImportError:
        print("[review] opentimelineio belum terpasang, timeline.otio dilewati", flush=True)
        return None

    tl = otio.schema.Timeline(name=f"clipper-{job_dir.name}")
    track = otio.schema.Track(name="Highlights", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(track)
    ref = otio.schema.ExternalReference(target_url=source.resolve().as_uri())
    for c in clips:
        if only_approved is not None and only_approved.get(c["id"], {}).get("verdict") != "approve":
            continue
        rng = otio.opentime.TimeRange(
            start_time=otio.opentime.from_seconds(c["start"], fps),
            duration=otio.opentime.from_seconds(c["end"] - c["start"], fps),
        )
        clip = otio.schema.Clip(name=f"{c['id']} {c.get('title', '')}".strip(), media_reference=ref, source_range=rng)
        clip.markers.append(otio.schema.Marker(
            name=(c.get("hook") or "")[:80],
            marked_range=otio.opentime.TimeRange(otio.opentime.from_seconds(c["start"], fps), otio.opentime.from_seconds(0, fps)),
        ))
        track.append(clip)
        track.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(
            duration=otio.opentime.from_seconds(1.0, fps))))
    out = job_dir / "timeline.otio"
    otio.adapters.write_to_file(tl, str(out))
    return out


class _Handler(SimpleHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/decisions") or self.path == "/decisions":
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(min(length, 2_000_000))
            try:
                json.loads(data)
            except ValueError:
                self.send_error(400, "bukan JSON")
                return
            (Path(self.directory) / "decisions.json").write_bytes(data)
            self.send_response(204)
            self.end_headers()
            return
        self.send_error(404)

    def log_message(self, *args):
        pass


def serve(job_dir: Path, port: int = 8765, open_browser: bool = True) -> None:
    handler = lambda *a, **kw: _Handler(*a, directory=str(job_dir), **kw)  # noqa: E731
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/review.html"
    print(f"[review] buka {url}  (Ctrl+C untuk berhenti)", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
