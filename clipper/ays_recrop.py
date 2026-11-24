"""Re-cut + reframe one clip with AI-Youtube-Shorts-Generator's own cropper.

Runs inside .venv-ays (it imports the vendored repo), called by apply_review
when you move a clip's start/end during the audit.

    python ays_recrop.py <ays_dir> <source> <start> <end> <aspect> <out.mp4>
"""
import sys


def main() -> int:
    ays_dir, source, start, end, aspect, out = sys.argv[1:7]
    sys.path.insert(0, ays_dir)
    from shorts_generator.local.clipper import crop_clip_local

    crop_clip_local(source, float(start), float(end), aspect, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
