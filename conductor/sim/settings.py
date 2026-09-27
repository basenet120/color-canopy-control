"""The simulator's latency and fault model (07 §5, §7).

Rules being implemented:
- The acceptance conditions are **pinned** in `config/sim/acceptance.yaml` and `SimSettings.from_acceptance()`
  maps them exactly — PUT ack 150–800 ms lognormal, device apply +0.3–1.5 s, start skew ≤ 1.5 s, 5% of PUTs →
  429, 1% silent drops, one 43-minute window of 500s per soak. Nothing here may tune them to make a test pass.
- `readback_semantics` models the open question in 07 §5: `"cloud"` = REST readback shows what the cloud stored
  even if the device never applied it (the pessimistic default until Gate B says otherwise); `"device"` = readback
  shows what the device actually applied.
- A frame whose length differs from the device's framebuffer is refused with HTTP 400 by default
  (`wrong_length_policy="reject"`); `"accept"` models a cloud that stores it anyway (bench step 6 settles it).
- `per_device_min_interval_s`, when set, makes the simulated cloud answer 429 to a write that arrives sooner than
  that after the previous write to the same device, and counts it as a budget violation (≤ 1 write/s/device).

All randomness is drawn from a `random.Random` the caller seeds, so a run is reproducible.

Lognormal ack latency: median = √(min·max) and σ = ln(max/min)/4, so min and max sit at ±2σ (~95% of draws fall
inside naturally); every draw is then clamped to [min, max].
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import yaml

from conductor.config import CONFIG_DIR, ConfigError

ACCEPTANCE_FILE = CONFIG_DIR / "sim" / "acceptance.yaml"

ReadbackSemantics = Literal["cloud", "device"]
WrongLengthPolicy = Literal["reject", "accept"]
READBACK_SEMANTICS = ("cloud", "device")
WRONG_LENGTH_POLICIES = ("reject", "accept")


@dataclass(frozen=True)
class SimSettings:
    """Latency and fault parameters for one SimDriver. Immutable; use `replace()` for variants."""

    label: str = "custom"
    ack_ms_min: float = 150.0
    ack_ms_max: float = 800.0
    ack_distribution: Literal["lognormal", "uniform"] = "lognormal"
    device_apply_extra_s_min: float = 0.3
    device_apply_extra_s_max: float = 1.5
    start_skew_max_s: float = (
        1.5  # phase offset of on-device animation after apply (a seam; see drivers/sim.py)
    )
    throttle_429_rate: float = 0.0
    silent_drop_rate: float = 0.0  # PUT answered 200 but never stored
    read_row_drop_rate: float = 0.0  # silent degradation on GET: one random row missing from a 200
    # Scheduled 500 windows as (start, duration) seconds after the driver is created.
    http_500_windows: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    http_500_window_s: float | None = None  # pinned window length for a soak to schedule (acceptance: 43 min)
    internet_outage_s: float | None = None  # pinned outage length for a soak to schedule (acceptance: 30 min)
    outage_fail_s: float = (
        3.0  # how long a request takes to fail while the internet is down (sock_connect, 07 §4)
    )
    readback_semantics: ReadbackSemantics = "cloud"
    wrong_length_policy: WrongLengthPolicy = "reject"
    per_device_min_interval_s: float | None = None

    def __post_init__(self) -> None:
        problems = []
        if not 0 < self.ack_ms_min <= self.ack_ms_max:
            problems.append("ack latency needs 0 < min ≤ max (milliseconds)")
        if self.ack_distribution not in ("lognormal", "uniform"):
            problems.append(f"ack distribution {self.ack_distribution!r} must be lognormal or uniform")
        if not 0 <= self.device_apply_extra_s_min <= self.device_apply_extra_s_max:
            problems.append("device apply delay needs 0 ≤ min ≤ max (seconds)")
        if self.start_skew_max_s < 0:
            problems.append("start skew can't be negative")
        for name in ("throttle_429_rate", "silent_drop_rate", "read_row_drop_rate"):
            if not 0 <= getattr(self, name) <= 1:
                problems.append(f"{name} must be between 0 and 1")
        for start, duration in self.http_500_windows:
            if start < 0 or duration <= 0:
                problems.append("each 500 window needs start ≥ 0 and a positive duration")
        if self.readback_semantics not in READBACK_SEMANTICS:
            problems.append(f"readback_semantics must be one of {', '.join(READBACK_SEMANTICS)}")
        if self.wrong_length_policy not in WRONG_LENGTH_POLICIES:
            problems.append(f"wrong_length_policy must be one of {', '.join(WRONG_LENGTH_POLICIES)}")
        if self.per_device_min_interval_s is not None and self.per_device_min_interval_s <= 0:
            problems.append("per_device_min_interval_s must be positive (or unset)")
        if self.outage_fail_s < 0:
            problems.append("outage_fail_s can't be negative")
        if problems:
            raise ValueError("Simulator settings are invalid: " + "; ".join(problems) + ".")

    # ---- presets ---------------------------------------------------------------------------------------------
    @classmethod
    def dev(cls) -> SimSettings:
        """Development: no faults, ack 150–800 ms lognormal, device apply +0.3–1.5 s."""
        return cls(label="dev")

    @classmethod
    def from_acceptance(cls, path: Path | str = ACCEPTANCE_FILE) -> SimSettings:
        """The pinned acceptance conditions (07 §7), mapped exactly. Raises ConfigError with the file and fix."""
        path = Path(path)
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except FileNotFoundError:
            raise ConfigError(f"{path} is missing; the simulator acceptance conditions live there.") from None
        except yaml.YAMLError:
            raise ConfigError(f"{path} is not valid YAML. Check indentation and colons.") from None
        try:
            latency, faults = raw["latency"], raw["faults"]
            ack = latency["put_ack_ms"]
            apply = latency["device_apply_extra_s"]
            window_min = faults.get("http_500_window_minutes")
            outage_min = (raw.get("soak") or {}).get("internet_outage_minutes")
            return cls(
                label="acceptance",
                ack_ms_min=float(ack["min"]),
                ack_ms_max=float(ack["max"]),
                ack_distribution=ack.get("distribution", "lognormal"),
                device_apply_extra_s_min=float(apply["min"]),
                device_apply_extra_s_max=float(apply["max"]),
                start_skew_max_s=float(latency["start_skew_max_s"]),
                throttle_429_rate=float(faults["throttle_429_rate"]),
                silent_drop_rate=float(faults["silent_drop_rate"]),
                http_500_window_s=float(window_min) * 60 if window_min is not None else None,
                internet_outage_s=float(outage_min) * 60 if outage_min is not None else None,
            )
        except (KeyError, TypeError) as e:
            raise ConfigError(
                f"{path} is missing {e} (or it has the wrong shape). Restore it from git; these values are pinned."
            ) from None
        except ValueError as e:
            raise ConfigError(f"{path}: {e}") from None

    def with_(self, **changes: Any) -> SimSettings:
        """A copy with some fields changed (validated)."""
        return replace(self, **changes)

    # ---- sampling --------------------------------------------------------------------------------------------
    def sample_ack_s(self, rng: random.Random) -> float:
        lo, hi = self.ack_ms_min, self.ack_ms_max
        if self.ack_distribution == "uniform" or lo == hi:
            ms = rng.uniform(lo, hi)
        else:
            mu = math.log(math.sqrt(lo * hi))
            sigma = math.log(hi / lo) / 4
            ms = rng.lognormvariate(mu, sigma)
        return min(hi, max(lo, ms)) / 1000.0

    def sample_apply_extra_s(self, rng: random.Random) -> float:
        return rng.uniform(self.device_apply_extra_s_min, self.device_apply_extra_s_max)

    def sample_start_skew_s(self, rng: random.Random) -> float:
        return rng.uniform(0.0, self.start_skew_max_s)

    def in_scheduled_500_window(self, seconds_since_start: float) -> bool:
        return any(start <= seconds_since_start < start + dur for start, dur in self.http_500_windows)

    def to_json(self) -> dict:
        d = asdict(self)
        d["http_500_windows"] = [list(w) for w in self.http_500_windows]
        return d
