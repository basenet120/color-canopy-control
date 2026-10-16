"""Which canopy positions have a device registered on the engine's driver (D62): `registered_canopy_ids` and
`split_canopy_positions` in conductor/show.py, the one predicate ShowWiring and the guest panel's label share."""

from __future__ import annotations

from types import SimpleNamespace

from partial_canopy import LIVE

from conductor.config import Registry, load_registry
from conductor.show import ShowWiring, registered_canopy_ids, split_canopy_positions

CANOPY = [m.id for m in load_registry().canopy_modules()]
OTHERS = [d for d in CANOPY if d not in LIVE]


def _services(driver: str = "sim") -> SimpleNamespace:
    return SimpleNamespace(registry=load_registry(), engine_driver=SimpleNamespace(name=driver))


def _only(monkeypatch, registered) -> None:
    real = Registry.address

    def address(self, device_id, driver_name):
        return real(self, device_id, driver_name) if device_id in registered else None

    monkeypatch.setattr(Registry, "address", address)


def test_only_the_positions_with_a_device_on_the_engines_driver_are_registered(monkeypatch):
    _only(monkeypatch, LIVE)
    services = _services()
    assert registered_canopy_ids(services) == frozenset(LIVE)
    registered, unregistered = split_canopy_positions(services)
    assert registered == list(LIVE) and unregistered == OTHERS and len(unregistered) == 7


def test_the_simulator_registers_all_nine():
    services = _services()
    assert len(CANOPY) == 9 and registered_canopy_ids(services) == frozenset(CANOPY)
    assert split_canopy_positions(services) == (CANOPY, [])


def test_a_live_driver_without_metadevice_ids_registers_none():
    services = _services("afero")  # config/devices.yaml has no metadevice ids: nothing is registered live yet
    assert registered_canopy_ids(services) == frozenset()
    assert split_canopy_positions(services) == ([], CANOPY)


def test_show_wiring_uses_the_same_predicate(monkeypatch):
    _only(monkeypatch, LIVE)
    wiring = object.__new__(ShowWiring)  # the predicate needs only `services`
    wiring.services = _services()
    assert wiring._registered_ids() == frozenset(LIVE)
    assert [e.id for e in wiring._modules() if wiring._registered(e)] == list(LIVE)
