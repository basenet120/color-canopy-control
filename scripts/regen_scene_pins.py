"""Regenerate tests/fixtures_scene_frames.json from config/ (`make scene-pins`, Design Lab PLAN §3.7).

The fixture holds each scene's 30-light frame (Paris's handoff rule) and the approved values the tests pin
(`_pins`: each design's layout and its words, the module-level colours, the solid, the brightness and the stepped
timing). The Design Lab's export regenerates it in its patch; after a hand edit of config/{scenes,modes,recipes}.yaml
(or Paris's D63 review merged as YAML) run this, then review the fixture's diff before committing it.

    uv run --offline python scripts/regen_scene_pins.py           # write the fixture
    uv run --offline python scripts/regen_scene_pins.py --check   # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conductor.design_lab.draft import load_trio
from conductor.design_lab.export import fixture_for
from conductor.geometry import load_canopy_config

FIXTURE = ROOT / "tests" / "fixtures_scene_frames.json"


def fixture_text(config_dir: Path = ROOT / "config") -> str:
    """The fixture as the config in `config_dir` gives it (byte for byte what the export writes)."""
    config_dir = Path(config_dir)
    return fixture_for(load_trio(config_dir), load_canopy_config(config_dir / "canopy.yaml"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate tests/fixtures_scene_frames.json from config/.")
    parser.add_argument("--config", type=Path, default=ROOT / "config", help="the config directory to read")
    parser.add_argument("--out", type=Path, default=FIXTURE, help="the fixture to write")
    parser.add_argument(
        "--check", action="store_true", help="don't write: exit 1 if the fixture is out of date"
    )
    args = parser.parse_args(argv)
    text = fixture_text(args.config)
    current = args.out.read_text(encoding="utf-8") if args.out.exists() else None
    if args.check:
        if current != text:
            print(f"{args.out} is out of date: run make scene-pins and review its diff", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    if current == text:
        print(f"{args.out} is already up to date")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}; review its diff before committing it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
