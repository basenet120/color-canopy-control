"""Run the Design Lab on the simulator: `make lab-dev` (or `uv run --offline python scripts/dev_design_lab.py`).

Starts the Conductor on the SIMULATOR at http://127.0.0.1:8200 with:
- CONDUCTOR_MODE=sim and every HUBSPACE_*, RAILWAY_* and secret variable removed, so nothing can reach Hubspace or a
  show account (07 §1);
- CONDUCTOR_ENV removed, as on the show box;
- auth off (CONDUCTOR_AUTH=off), so it binds loopback only: any other host is refused;
- a throwaway data dir (deleted on exit unless --keep), or --data <dir> (kept); never one under ~/canopy-show or
  ~/Library/Application Support/ColorCanopy.

Port 8000 is refused: it belongs to the show Conductor (this Mac mini is the show box, and the enrolled iPad points
at it). Open http://127.0.0.1:8200/ops/showcase/#lab. Options: --port (8200), --host (127.0.0.1), --data, --keep.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
DEFAULT_PORT = 8200
SHOW_PORT = 8000
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
UNSAFE_PREFIXES = ("HUBSPACE_", "RAILWAY_")
SECRET_NAME = re.compile(r"SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL", re.IGNORECASE)
DROPPED = ("CONDUCTOR_ENV", "CONDUCTOR_ENGINE")  # the engine target and the engine itself, as on the show box
SHOW_PORT_REFUSED = "port 8000 belongs to the show Conductor; use another port (the default is 8200)"
HOST_REFUSED = "auth is off, so this server is for this machine only: use --host 127.0.0.1"
DATA_REFUSED = "{data} is the show box's data; give the Lab a throwaway --data dir"


def show_box_dirs(home: Path | None = None) -> tuple[Path, ...]:
    home = Path.home() if home is None else home
    return (home / "canopy-show", home / "Library" / "Application Support" / "ColorCanopy")


def unsafe(name: str) -> bool:
    return (
        name in UNSAFE_ENV
        or name.startswith(UNSAFE_PREFIXES)
        or bool(SECRET_NAME.search(name))
        or name in DROPPED
    )


def environment(data: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in (os.environ if base is None else base).items() if not unsafe(k)}
    env.update(CONDUCTOR_MODE="sim", CONDUCTOR_AUTH="off", CONDUCTOR_DATA_DIR=str(data))
    return env


def refusal(*, port: int, host: str, data: Path | None, home: Path | None = None) -> str | None:
    """Why this run would be unsafe on the show box, or None."""
    if port == SHOW_PORT:
        return SHOW_PORT_REFUSED
    if host not in LOOPBACK:
        return HOST_REFUSED
    if data is not None:
        # macOS volumes are case-insensitive: compare case-folded paths
        resolved = Path(str(data.expanduser().resolve()).casefold())
        for show in show_box_dirs(home):
            shown = Path(str(show.resolve()).casefold())
            if resolved == shown or resolved.is_relative_to(shown):
                return DATA_REFUSED.format(data=data)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", type=Path, default=None, help="data dir to use (kept on exit)")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway data dir")
    args = parser.parse_args(argv)
    if why := refusal(port=args.port, host=args.host, data=args.data):
        parser.error(why)

    if args.data is not None:
        data = args.data.expanduser().resolve()
        data.mkdir(parents=True, exist_ok=True)
    else:
        data = Path(tempfile.mkdtemp(prefix="design-lab-"))
    env = environment(data)
    os.environ.clear()
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    base = f"http://{args.host}:{args.port}"
    print(f"Design Lab (simulator, nothing reaches Hubspace): {base}/ops/showcase/#lab")
    print(f"Ops: {base}/ops/   Data: {data}")
    try:
        import uvicorn

        uvicorn.run("conductor.app:app", host=args.host, port=args.port)
    finally:
        if args.keep or args.data is not None:
            print(f"Kept {data}")
        else:
            shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
