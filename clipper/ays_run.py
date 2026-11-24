"""Run AI-Youtube-Shorts-Generator's CLI with our runtime patches (see ays_patches.py).

    python ays_run.py <ays_dir> <main.py args...>
"""
import os
import runpy
import sys


def main() -> None:
    ays_dir = sys.argv[1]
    sys.path.insert(0, ays_dir)
    sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))
    import ays_patches

    ays_patches.apply()
    sys.argv = [os.path.join(ays_dir, "main.py"), *sys.argv[2:]]
    runpy.run_path(os.path.join(ays_dir, "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
