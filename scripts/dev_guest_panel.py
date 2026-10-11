"""Try the guest panel without hardware: `make guest-dev` (or `uv run python scripts/dev_guest_panel.py`).

Starts the Conductor on the SIMULATOR at http://127.0.0.1:8100 with:
- CONDUCTOR_MODE=sim and HUBSPACE_LIVE removed, so nothing can reach Hubspace (07 §1);
- CONDUCTOR_ENV=workshop-sim, so the guest panel drives the workshop target, as on VWBD's laptop;
- both shop modules registered on simulated 60-entry controllers (`sqhdk03-sim-60`, the real SQHDK03's frame length
  with 30 pucks) with each kit's own controller map, so scenes show as per-light gradients — bench-kit as "Kingston
  test" and shop-string-1 as "String light 1";
- auth off (CONDUCTOR_AUTH=off: this machine only) and a throwaway data dir, deleted on exit unless --keep.

Open http://127.0.0.1:8100/kiosk/?debug=1 for the panel with its debug view, or /ops/ and /ops/showcase/ for the
"Guest panel" buttons. Options: --port (8100), --host (127.0.0.1), --keep, --solid (leave String light 1 unmapped to
see the solid-colour fallback).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAME_LENGTH = 60  # sqhdk03-sim-60
HEADS = 30
# Environment that could point a dev run at real lights or real credentials: never inherited.
UNSAFE_ENV = (
    "HUBSPACE_LIVE",
    "HUBSPACE_LIVE_UNTIL",
    "HUBSPACE_DEVICE_ALLOWLIST",
    "HUBSPACE_ACCOUNT_ALLOWLIST",
    "CONDUCTOR_BASIC_AUTH",
    "CONDUCTOR_SECRET_KEY",
    "CONDUCTOR_ACCOUNT_CONNECT",
    "RAILWAY_ENVIRONMENT_NAME",
)


def mapping(device_id: str) -> dict:
    """The identity map recorded by Apply map (D58), bound to the kit's own simulated address."""
    return {"frame_length": FRAME_LENGTH, "indices": list(range(HEADS)), "proven_on": f"sim-{device_id}"}


def overlay(*, solid: bool = False) -> dict:
    """The ops overlay (`<data>/overrides/registry.yaml`) registering both shop modules on 60-entry sim controllers."""
    string = {
        "role": "spare",
        "account": "bench",
        "label": "String light 1",
        "model": "SQHDK03",
        "profile": "sqhdk03-sim-60",
        "expected_heads": HEADS,
        "orientation": "A",
    }
    if not solid:
        string["framebuffer_mapping"] = mapping("shop-string-1")
    return {
        "devices": {
            "bench-kit": {
                "label": "Kingston test",
                "profile": "sqhdk03-sim-60",
                "framebuffer_mapping": mapping("bench-kit"),
            },
            "shop-string-1": string,
        }
    }


def environment(data: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in UNSAFE_ENV}
    env.update(
        CONDUCTOR_MODE="sim",
        CONDUCTOR_ENV="workshop-sim",
        CONDUCTOR_AUTH="off",
        CONDUCTOR_DATA_DIR=str(data),
    )
    return env


def write_overlay(data: Path, *, solid: bool = False) -> Path:
    import yaml

    path = data / "overrides" / "registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(overlay(solid=solid), sort_keys=False))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway data dir")
    parser.add_argument("--solid", action="store_true", help="leave String light 1 unmapped (solid fallback)")
    args = parser.parse_args(argv)

    data = Path(tempfile.mkdtemp(prefix="guest-panel-"))
    write_overlay(data, solid=args.solid)
    env = environment(data)
    os.environ.clear()
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    base = f"http://{args.host}:{args.port}"
    print(f"Guest panel (simulator, nothing reaches Hubspace): {base}/kiosk/?debug=1")
    print(f"Ops: {base}/ops/   Studio: {base}/ops/showcase/   Data: {data}")
    try:
        import uvicorn

        uvicorn.run("conductor.app:app", host=args.host, port=args.port)
    finally:
        if args.keep:
            print(f"Kept {data}")
        else:
            shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
