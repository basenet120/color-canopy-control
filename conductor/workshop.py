"""Workshop launcher: run the Conductor on one laptop in a fabrication shop to test a single module (Prototype 01).

    python -m conductor.workshop setup      first time: asks for the Hubspace email of the test module's account
    python -m conductor.workshop run        start the Conductor (real module, through the Hubspace cloud)
    python -m conductor.workshop sim        start on the simulator (practice — nothing touches real lights)
    python -m conductor.workshop allow      allow the module you registered on /ops/accounts (then restart)
    python -m conductor.workshop status     what's configured (never prints secrets)

Settings live in `workshop.env` next to the program (mode 0600): a locally generated password for the pages and a
locally generated encryption key for the Hubspace sign-in. Nothing here is shared with anyone else, and the runtime
never holds show accounts: it runs in `bench` mode with an account allowlist of exactly one email (07 §1–§3).
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "workshop.env"
DATA_DIR = ROOT / "workshop-data"
SIM_DATA_DIR = ROOT / "workshop-data-sim"
PORT = 8000
USER = "workshop"
DEFAULT_UNTIL = "2026-10-19"


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str], path: Path = ENV_FILE) -> None:
    lines = [
        "# Color Canopy workshop settings. Keep this file private: it holds the page password and the key that",
        "# encrypts the Hubspace sign-in. Created by `python -m conductor.workshop setup`.",
        *(f"{k}={v}" for k, v in values.items()),
    ]
    from conductor.tokens import atomic_write

    atomic_write(path, ("\n".join(lines) + "\n").encode(), mode=0o600)


def lan_address() -> str:
    """This laptop's address on the shop Wi-Fi (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "this-laptop"


def setup(email: str | None, until: str, *, out=sys.stdout) -> dict[str, str]:
    from cryptography.fernet import Fernet

    existing = read_env()
    if not email:
        email = input("Hubspace email of the account that owns the test module: ").strip()
    if "@" not in (email or ""):
        raise SystemExit("That doesn't look like an email address. Run setup again.")
    values = {
        "CONDUCTOR_MODE": "bench",
        "HUBSPACE_LIVE": "1",
        "HUBSPACE_ACCOUNT_ALLOWLIST": email.lower(),
        "HUBSPACE_DEVICE_ALLOWLIST": existing.get("HUBSPACE_DEVICE_ALLOWLIST", ""),
        "HUBSPACE_LIVE_UNTIL": until,
        "CONDUCTOR_ACCOUNT_CONNECT": "1",
        "CONDUCTOR_ENV": "workshop",
        "CONDUCTOR_DATA_DIR": str(DATA_DIR),
        # Keep the password and key across re-runs of setup, or the saved Hubspace sign-in can't be decrypted.
        "CONDUCTOR_BASIC_AUTH": existing.get("CONDUCTOR_BASIC_AUTH") or f"{USER}:{secrets.token_urlsafe(9)}",
        "CONDUCTOR_SECRET_KEY": existing.get("CONDUCTOR_SECRET_KEY") or Fernet.generate_key().decode(),
    }
    write_env(values)
    password = values["CONDUCTOR_BASIC_AUTH"].split(":", 1)[1]
    print(
        f"\nSaved {ENV_FILE.name}.\n"
        f"  Page sign-in:  user '{USER}'   password '{password}'\n"
        "  (It's also in workshop.env if you forget it. Share it only with your crew.)\n",
        file=out,
    )
    return values


def _apply(values: dict[str, str], *, sim: bool) -> None:
    for key in ("CONDUCTOR_AUTH", "RAILWAY_ENVIRONMENT_NAME", "CONDUCTOR_ENGINE", "CONDUCTOR_SIM_SPEED"):
        os.environ.pop(key, None)
    os.environ.update(values)
    if sim:
        for key in (
            "HUBSPACE_LIVE",
            "HUBSPACE_DEVICE_ALLOWLIST",
            "HUBSPACE_ACCOUNT_ALLOWLIST",
            "HUBSPACE_LIVE_UNTIL",
        ):
            os.environ.pop(key, None)
        os.environ.update(
            CONDUCTOR_MODE="sim", CONDUCTOR_DATA_DIR=str(SIM_DATA_DIR), CONDUCTOR_ENV="workshop-sim"
        )


def run(*, sim: bool, port: int = PORT, out=sys.stdout) -> None:
    values = read_env()
    if not values:
        values = setup(None, DEFAULT_UNTIL, out=out)
    _apply(values, sim=sim)
    address = lan_address()
    kit = values.get("HUBSPACE_DEVICE_ALLOWLIST") or ""
    mode = (
        "SIMULATOR (practice: nothing touches real lights)"
        if sim
        else "REAL MODULE through the Hubspace cloud"
    )
    print(
        f"\nColor Canopy workshop — {mode}\n"
        f"  On this laptop:      http://localhost:{port}/ops/module/\n"
        f"  On a phone (same Wi-Fi): http://{address}:{port}/ops/module/\n"
        f"  Sign in as '{USER}' with the password from setup (in workshop.env).\n",
        file=out,
    )
    if not sim and not kit:
        print(
            "  The module isn't allowed yet: open http://localhost:8000/ops/accounts/ , sign in to Hubspace, press\n"
            "  'Use as bench kit' on your module, then stop this (Ctrl+C) and run the 'allow' step.\n",
            file=out,
        )
    print("  Stop with Ctrl+C.\n", file=out)
    import uvicorn

    uvicorn.run("conductor.app:app", host="0.0.0.0", port=port, log_level="warning")


def registered_kit() -> str | None:
    from conductor.config import load_registry

    try:
        return load_registry(data=DATA_DIR).get("bench-kit").metadevice_id
    except Exception:  # noqa: BLE001 - a missing/broken overlay just means "not registered yet"
        return None


def allow(*, assume_yes: bool = False, out=sys.stdout) -> bool:
    values = read_env()
    if not values:
        raise SystemExit("Run setup first.")
    kit = registered_kit()
    if not kit:
        print(
            "No module is registered yet. Start the program, open /ops/accounts/, sign in to Hubspace and press\n"
            "'Use as bench kit' on your module. Then run this again.",
            file=out,
        )
        return False
    tail = kit[-6:]
    if not assume_yes:
        answer = input(
            f"Allow the program to change the module registered as the bench kit (id …{tail})? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing changed.", file=out)
            return False
    values["HUBSPACE_DEVICE_ALLOWLIST"] = kit
    write_env(values)
    print(f"Allowed the module …{tail}. Start the program again for it to take effect.", file=out)
    return True


def status(out=sys.stdout) -> None:
    values = read_env()
    if not values:
        print("Not set up yet: run setup.", file=out)
        return
    kit = values.get("HUBSPACE_DEVICE_ALLOWLIST") or ""
    print(
        f"Hubspace account allowed: {values.get('HUBSPACE_ACCOUNT_ALLOWLIST', '?')}\n"
        f"Module allowed:           {('…' + kit[-6:]) if kit else 'none yet (run allow after registering it)'}\n"
        f"Writes stop after:        {values.get('HUBSPACE_LIVE_UNTIL', '?')}\n"
        f"Registered bench kit:     {('…' + (registered_kit() or '')[-6:]) if registered_kit() else 'none yet'}",
        file=out,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="workshop", description="Run the Color Canopy test program in a workshop."
    )
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("setup", help="first-time settings")
    p.add_argument("--email")
    p.add_argument(
        "--until", default=DEFAULT_UNTIL, help="date after which the program stops writing (YYYY-MM-DD)"
    )
    sub.add_parser("run", help="start with the real module")
    sub.add_parser("sim", help="start on the simulator (practice)")
    a = sub.add_parser("allow", help="allow the registered module")
    a.add_argument("--yes", action="store_true")
    sub.add_parser("status", help="show the settings (no secrets)")
    args = parser.parse_args(argv)
    if args.cmd == "setup":
        setup(args.email, args.until)
    elif args.cmd == "sim":
        run(sim=True)
    elif args.cmd == "allow":
        allow(assume_yes=args.yes)
    elif args.cmd == "status":
        status()
    else:
        run(sim=False)


if __name__ == "__main__":
    main()
