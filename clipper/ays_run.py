"""Run AI-Youtube-Shorts-Generator's CLI with one small runtime patch.

Upstream rejects Gemini keys that don't start with "AIza". Google now also
issues Gemini API keys in a newer "AQ." format, which google-genai accepts.
We relax only that prefix check here instead of forking the repo.

    python ays_run.py <ays_dir> <main.py args...>
"""
import runpy
import sys


def main() -> None:
    ays_dir = sys.argv[1]
    sys.path.insert(0, ays_dir)
    from shorts_generator import config

    original = config._require_key

    def _require_key(value, name, *args, **kwargs):
        if name == "GEMINI_API_KEY":
            kwargs.pop("expected_prefix", None)
            if len(args) >= 2:
                args = args[:1]
        return original(value, name, *args, **kwargs)

    config._require_key = _require_key
    sys.argv = [f"{ays_dir}/main.py", *sys.argv[2:]]
    runpy.run_path(f"{ays_dir}/main.py", run_name="__main__")


if __name__ == "__main__":
    main()
