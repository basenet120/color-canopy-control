"""SupplementalDriver — interface stub only (07 §6, a v1 seam).

If supplemental (non-Hubspace) lighting is ever used (sACN/DDP/WLED/FPP), it plugs in here behind the same
DeviceDriver interface, and kiosk copy must never credit it to Hubspace (05 #12).
"""

from __future__ import annotations

from collections.abc import Sequence

from conductor.drivers.base import DeviceInfo, ReadResult, StateRow, WriteResult
from conductor.gate import Purpose


class SupplementalDriver:
    name = "supplemental"
    simulated = False

    async def discover(self, account: str | None = None) -> list[DeviceInfo]:
        raise NotImplementedError("supplemental lighting is a v1 seam, not built")

    async def fetch_profile(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> DeviceInfo:
        raise NotImplementedError("supplemental lighting is a v1 seam, not built")

    async def read_state(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> ReadResult:
        raise NotImplementedError("supplemental lighting is a v1 seam, not built")

    async def write(self, device_id: str, rows: Sequence[StateRow], *, purpose: Purpose) -> WriteResult:
        raise NotImplementedError("supplemental lighting is a v1 seam, not built")

    def account_of(self, device_id: str) -> str:
        return "supplemental"

    def health(self) -> dict:
        return {"driver": "supplemental", "built": False}

    async def close(self) -> None:
        return None
