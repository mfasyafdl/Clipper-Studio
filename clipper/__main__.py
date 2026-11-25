"""Command line: python -m clipper <run|review|apply|captions> ..."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipper", description="Clipper Studio: video panjang -> klip vertikal siap posting")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="proses satu video (URL YouTube/Twitch atau file lokal)")
    r.add_argument("url")
    r.add_argument("--clips", type=int, default=5, help="jumlah klip (default 5)")
    r.add_argument("--lang", default=None, help="kode bahasa, mis. id atau en (default dari .env)")

    v = sub.add_parser("review", help="buka halaman audit untuk satu job")
    v.add_argument("job")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--no-browser", action="store_true")

    a = sub.add_parser("apply", help="terapkan decisions.json (trim, ganti hook, kumpulkan yang dipakai)")
    a.add_argument("job")

    m = sub.add_parser("rerun", help="pilih ulang momen + render ulang job lama (tanpa unduh/transkrip ulang)")
    m.add_argument("job")
    m.add_argument("--clips", type=int, default=5)

    k = sub.add_parser("remake", help="tonton ulang klip tertentu dengan Gemini lalu render ulang pakai resep edit")
    k.add_argument("job")
    k.add_argument("ids", nargs="*", help="mis. clip_03 clip_05 (kosong = semua)")
    k.add_argument("--same", action="store_true", help="tanpa Gemini: pakai tanda momen tersimpan, render ulang saja")

    f = sub.add_parser("retry", help="ulangi klip yang gagal di tahap crop/caption")
    f.add_argument("job")

    c = sub.add_parser("captions", help="render ulang subtitle semua klip (setelah ganti gaya di .env)")
    c.add_argument("job")

    args = p.parse_args(argv)
    from .config import Settings

    settings = Settings.load()
    if getattr(args, "lang", None):
        settings.language = args.lang

    if args.cmd == "run":
        from .pipeline import run

        run(args.url, args.clips, settings)
    elif args.cmd == "review":
        from .review import serve

        serve(Path(args.job), port=args.port, open_browser=not args.no_browser)
    elif args.cmd == "apply":
        from .pipeline import apply_decisions

        result = apply_decisions(Path(args.job), settings)
        print(f"[apply] dirender ulang: {result['rerendered'] or '-'}")
        print(f"[apply] klip dipakai ({len(result['approved'])}): folder approved/")
        if result["notes"]:
            print("[apply] ada catatan yang butuh edit manual: notes_for_claude.md")
    elif args.cmd == "rerun":
        from .pipeline import rerun_moments

        job = rerun_moments(Path(args.job), args.clips, settings)
        print(f"\n[job] selesai. Audit dengan:  .\\run.ps1 review \"{job}\"")
    elif args.cmd == "remake":
        from .pipeline import remake_clips

        done = remake_clips(Path(args.job), args.ids, settings, same=args.same)
        print(f"[remake] selesai: {done or '-'}. Audit dengan:  .\\run.ps1 review \"{args.job}\"")
    elif args.cmd == "retry":
        from .pipeline import retry_failed

        done = retry_failed(Path(args.job), settings)
        print(f"[retry] berhasil: {done or '-'}")
    elif args.cmd == "captions":
        from .pipeline import load_clips, load_words, refresh_review, render_captions, save_clips

        job = Path(args.job)
        clips = load_clips(job)
        render_captions(job, clips, load_words(job), settings)
        save_clips(job, clips)
        refresh_review(job, clips)
    return 0


if __name__ == "__main__":
    sys.exit(main())
