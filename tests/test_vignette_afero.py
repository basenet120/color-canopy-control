"""Vignette devices through the real Afero adapter on the fake transport (P4; 07 §1, §4).

The AferoDriver writes generic rows, so what matters is that the vignette compiler's rows are exactly what aioafero
9.3.0's own controllers put on the wire for the same intent. Each test runs our action on one device and the
aioafero controller call on an identical twin device, through the same bridge and gated session, and compares the
PUT bodies: same (functionClass, functionInstance, value) rows, in the same order. Plus: our stamps are epoch
milliseconds, and the live gate refuses a vignette device that isn't allowlisted with nothing on the wire.
"""

from __future__ import annotations

import asyncio
import copy

import pytest
from fake_afero import BENCH_MD, SPARE_MD, make_world, raw_device_from_dump

from conductor.clock import run_virtual
from conductor.config import ExecutorSettings, OverlayFile, Registry, RegistryFile
from conductor.executor import CommandExecutor
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.services import Target
from conductor.sim.profiles import load_profile
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry
from conductor.vignettes import RoomConfig, VignetteDeps, VignetteService, VignettesFile

TWINS = {  # logical id → (kind, profile, our metadevice id, aioafero's twin)
    "lamp": ("bulb", "ecosmart-a19-rgb", "md-lamp-ours", "md-lamp-twin"),
    "plug": ("plug", "outdoor-plug-2", "md-plug-ours", "md-plug-twin"),
    "lock": ("lock", "defiant-deadbolt", "md-lock-ours", "md-lock-twin"),
    "fan": ("fan", "ceiling-fan", "md-fan-ours", "md-fan-twin"),
}
LOCKED_OUT = "md-lock-not-allowlisted"


def _registry() -> Registry:
    devices = [
        {"id": "bench-kit", "role": "bench", "account": "bench", "metadevice_id": BENCH_MD},
        {"id": "spare-kit", "role": "spare", "account": "bench", "metadevice_id": SPARE_MD},
        {"id": "door-2", "role": "vignette", "vignette": "room", "kind": "lock", "account": "bench"}
        | {"metadevice_id": LOCKED_OUT},
    ]
    for name, (kind, _profile, ours, twin) in TWINS.items():
        devices.append(
            {"id": name, "role": "vignette", "vignette": "room", "kind": kind, "account": "bench"}
            | {"metadevice_id": ours}
        )
        devices.append({"id": f"{name}-twin", "role": "spare", "account": "bench", "metadevice_id": twin})
    return Registry(
        RegistryFile.model_validate({"accounts": {"bench": {"label": "Bench"}}, "devices": devices}),
        OverlayFile(),
        None,
    )


def _dump(profile: str, states: dict[tuple[str, str | None], object]) -> dict:
    dump = copy.deepcopy(load_profile(profile))
    for row in dump["states"]:
        key = (row["functionClass"], row.get("functionInstance"))
        if key in states:
            row["value"] = states[key]
    return dump


async def _world(tmp_path, states: dict[str, dict]):
    """A connected AferoDriver world whose fake cloud holds each twin pair in the same starting state."""
    from fake_afero import FakeAfero

    fake = FakeAfero()
    for name, (_kind, profile, ours, twin) in TWINS.items():
        dump = _dump(profile, states.get(name, {}))
        for md in (ours, twin):
            fake.add_device(md, raw_device_from_dump(md, dump))
    fake.add_device(LOCKED_OUT, raw_device_from_dump(LOCKED_OUT, _dump("defiant-deadbolt", {})))
    registry = _registry()
    allow = tuple(md for _k, _p, ours, twin in TWINS.values() for md in (ours, twin)) + (BENCH_MD,)
    world = await make_world(tmp_path, fake=fake, registry=registry, allow=allow)
    locks = DeviceLockManager(lambda a: (world.gate.device_writable(a, Purpose.BENCH), "no"), world.clock)
    telemetry = Telemetry(None, world.clock)
    executor = CommandExecutor(
        world.driver, locks=locks, telemetry=telemetry, clock=world.clock, policy=ExecutorSettings()
    )
    tracker = DeviceStateTracker(world.clock, telemetry)
    tracker.attach(executor)
    rooms = VignettesFile(rooms={"room": RoomConfig(space="Test room", title="Test")})
    service = VignetteService(
        VignetteDeps(
            registry=lambda: registry,
            target=lambda d, _p: Target(d, registry.address(d, "afero"), world.driver, executor),
            rooms=rooms,
            tracker=tracker,
            clock=world.clock,
        )
    )
    bridge = world.driver._link("bench").bridge  # aioafero's own controllers, on the same gated session
    return world, service, bridge, executor


def _put_rows(world, md: str) -> list[list[dict]]:
    return [
        [{k: v for k, v in row.items() if k != "lastUpdateTime"} for row in r.json["values"]]
        for r in world.fake.requests
        if r.method == "PUT" and r.path.endswith(f"/metadevices/{md}/state")
    ]


def _stamps(world, md: str) -> list[int]:
    return [
        row["lastUpdateTime"]
        for r in world.fake.requests
        if r.method == "PUT" and r.path.endswith(f"/metadevices/{md}/state")
        for row in r.json["values"]
    ]


def _scene(service: VignetteService, device: str, settings: dict) -> None:
    from conductor.vignettes import Scene

    service.rooms.rooms["room"].scenes[:] = [
        Scene.model_validate({"id": "s", "label": "S", "steps": [{"device": device, "set": settings}]})
    ]


# ---- EcoSmart A19 colour bulb (LightController) -------------------------------------------------------------------
LAMP_START = {  # every row differs from what the tests ask for, so aioafero sends every row it owns
    ("power", None): "off",
    ("color-rgb", None): {"color-rgb": {"r": 0, "g": 0, "b": 0}},
    ("brightness", None): 30,
    ("color-temperature", None): 2700,
    ("color-sequence", "preset"): "fade-3",
    ("color-sequence", "custom"): "focus",
    ("speed", "color-sequence"): 5,
}


@pytest.mark.parametrize(
    ("start_mode", "ours", "theirs"),
    [
        (
            "sequence",
            {"color": "#E0182D", "brightness": 80},
            {"on": True, "color": (224, 24, 45), "color_mode": "color", "brightness": 80},
        ),
        (
            "sequence",
            {"color_temperature": 4000, "brightness": 50},
            {"on": True, "temperature": 4000, "color_mode": "white", "brightness": 50},
        ),
        (
            "white",
            {"effect": "christmas", "speed": 0, "brightness": 60},
            {
                "on": True,
                "effect": "christmas",
                "color_mode": "sequence",
                "brightness": 60,
                "numbers": {("speed", "color-sequence"): 0},
            },
        ),
        (
            "white",
            {"effect": "fade-7", "speed": 0, "brightness": 60},
            {
                "on": True,
                "effect": "fade-7",
                "color_mode": "sequence",
                "brightness": 60,
                "numbers": {("speed", "color-sequence"): 0},
            },
        ),
    ],
    ids=["colour", "white", "program-effect", "preset-effect"],
)
def test_a_bulb_look_is_exactly_what_aioafero_sends(tmp_path, start_mode, ours, theirs):
    start = {**LAMP_START, ("color-mode", None): start_mode}

    async def main():
        world, service, bridge, _ = await _world(tmp_path, {"lamp": start})
        _scene(service, "lamp", ours)
        t0 = round(world.clock.now() * 1000)
        report = await service.scene("room", "s")
        t1 = round(world.clock.now() * 1000)
        await bridge.lights.set_state("md-lamp-twin", **theirs)
        await world.driver.close()
        return world, report, (t0, t1)

    world, report, (t0, t1) = run_virtual(main)
    assert report["results"][0]["status"] == "applied", report
    [our_put] = _put_rows(world, "md-lamp-ours")
    [their_put] = _put_rows(world, "md-lamp-twin")
    assert our_put == their_put
    stamps = _stamps(world, "md-lamp-ours")
    assert len(set(stamps)) == 1 and t0 <= stamps[0] <= t1 and stamps[0] > 10**12  # epoch ms, from our clock


def test_a_bulb_colour_change_carries_aioafero_rows_plus_the_rest_of_the_look(tmp_path):
    """A single colour tap: aioafero's set_rgb sends only what changed; our PUT is the full look (07 §5), so it
    carries exactly aioafero's rows plus the unchanged ones restated in the same encoding."""
    start = {("power", None): "on", ("color-mode", None): "white", ("brightness", None): 70}

    async def main():
        world, service, bridge, _ = await _world(tmp_path, {"lamp": start})
        report = await service.action("room", device_id="lamp", action="color", value="#E0182D")
        await bridge.lights.set_rgb("md-lamp-twin", 224, 24, 45)
        await world.driver.close()
        return world, report

    world, report = run_virtual(main)
    assert report["results"][0]["status"] == "applied"
    [ours] = _put_rows(world, "md-lamp-ours")
    [theirs] = _put_rows(world, "md-lamp-twin")
    assert theirs == [
        {
            "functionClass": "color-rgb",
            "functionInstance": None,
            "value": {"color-rgb": {"r": 224, "g": 24, "b": 45}},
        },
        {"functionClass": "color-mode", "functionInstance": None, "value": "color"},
    ]
    assert [r for r in ours if r in theirs] == theirs  # same rows, same order
    extras = [r for r in ours if r not in theirs]
    assert extras == [
        {"functionClass": "power", "functionInstance": None, "value": "on"},
        {"functionClass": "brightness", "functionInstance": None, "value": 70},
    ]


# ---- Defiant dual outdoor plug (SwitchController) -----------------------------------------------------------------
def test_an_outlet_toggle_uses_aioafero_encoding_per_outlet(tmp_path):
    start = {("power", "outlet-1"): "off", ("power", "outlet-2"): "on"}

    async def main():
        world, service, bridge, _ = await _world(tmp_path, {"plug": start})
        report = await service.action("room", device_id="plug", action="power", outlet="outlet-1", value=True)
        await bridge.switches.turn_on("md-plug-twin", instance="outlet-1")
        await asyncio.sleep(1.5)  # the per-device budget before the twin's second write
        await bridge.switches.turn_off("md-plug-twin", instance="outlet-2")
        await world.driver.close()
        return world, report

    world, report = run_virtual(main)
    assert report["results"][0]["status"] == "applied"
    [ours] = _put_rows(world, "md-plug-ours")
    turn_on, turn_off = _put_rows(world, "md-plug-twin")
    assert turn_on == [{"functionClass": "power", "functionInstance": "outlet-1", "value": "on"}]
    # our one PUT = the toggled outlet exactly as aioafero sends it + the other outlet's state, restated
    assert ours == [*turn_on, {"functionClass": "power", "functionInstance": "outlet-2", "value": "on"}]
    assert turn_off == [
        {"functionClass": "power", "functionInstance": "outlet-2", "value": "off"}
    ]  # same shape


# ---- Defiant deadbolt (LockController) ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("start", "action", "call"), [("unlocked", "lock", "lock"), ("locked", "unlock", "unlock")]
)
def test_a_lock_action_is_exactly_what_aioafero_sends(tmp_path, start, action, call):
    async def main():
        world, service, bridge, _ = await _world(tmp_path, {"lock": {("lock-control", None): start}})
        report = await service.action("room", device_id="lock", action=action, confirm=True)
        await getattr(bridge.locks, call)("md-lock-twin")
        await world.driver.close()
        return world, report

    world, report = run_virtual(main)
    [ours] = _put_rows(world, "md-lock-ours")
    [theirs] = _put_rows(world, "md-lock-twin")
    assert (
        ours
        == theirs
        == [{"functionClass": "lock-control", "functionInstance": None, "value": f"{action}ing"}]
    )
    # The fake cloud stores "locking" and never settles, like a lock that hasn't reported yet: the command is
    # verified against the settled position, so it honestly doesn't read back as done.
    assert report["results"][0]["status"] == "mismatch"


def test_a_lock_action_without_confirm_sends_nothing(tmp_path):
    from conductor.vignettes import ConfirmRequired

    async def main():
        world, service, _, _ = await _world(tmp_path, {})
        with pytest.raises(ConfirmRequired):
            await service.action("room", device_id="lock", action="unlock")
        await world.driver.close()
        return world

    world = run_virtual(main)
    assert _put_rows(world, "md-lock-ours") == []


def test_the_gate_refuses_a_vignette_device_outside_the_allowlist_with_nothing_on_the_wire(tmp_path):
    async def main():
        world, service, _, _ = await _world(tmp_path, {})
        report = await service.action("room", device_id="door-2", action="unlock", confirm=True)
        control = await service.action("room", device_id="lock", action="unlock", confirm=True)
        await world.driver.close()
        return world, report, control

    world, report, control = run_virtual(main)
    assert report["results"][0]["status"] == "denied"
    assert [r for r in world.fake.requests if LOCKED_OUT in r.path and r.method != "GET"] == []
    assert control["results"][0]["status"] != "denied" and _put_rows(world, "md-lock-ours")


# ---- ceiling fan (FanController) -------------------------------------------------------------------------------------
def test_a_fan_speed_is_exactly_what_aioafero_sends(tmp_path):
    start = {("power", "fan-power"): "off", ("fan-speed", "fan-speed"): "fan-speed-6-033"}

    async def main():
        world, service, bridge, _ = await _world(tmp_path, {"fan": start})
        report = await service.action("room", device_id="fan", action="fan_speed", value="fan-speed-6-066")
        await bridge.fans.set_speed("md-fan-twin", 66)  # aioafero speaks percent; 66 % is step 4 of 6
        await world.driver.close()
        return world, report

    world, report = run_virtual(main)
    assert report["results"][0]["status"] == "applied"
    assert (
        _put_rows(world, "md-fan-ours")
        == _put_rows(world, "md-fan-twin")
        == [
            [
                {"functionClass": "power", "functionInstance": "fan-power", "value": "on"},
                {"functionClass": "fan-speed", "functionInstance": "fan-speed", "value": "fan-speed-6-066"},
            ]
        ]
    )


def test_aioafero_parses_every_synthesized_profile_as_the_right_device_class(tmp_path):
    async def main():
        world, _, bridge, _ = await _world(tmp_path, {})
        found = {
            "lights": {i.id for i in bridge.lights.items},
            "switches": {i.id for i in bridge.switches.items},
            "locks": {i.id for i in bridge.locks.items},
            "fans": {i.id for i in bridge.fans.items},
        }
        await world.driver.close()
        return found

    found = run_virtual(main)
    assert {"md-lamp-ours", "md-lamp-twin"} <= found["lights"]
    assert {"md-plug-ours", "md-plug-twin"} <= found["switches"]
    assert {"md-lock-ours", "md-lock-twin", LOCKED_OUT} <= found["locks"]
    assert {"md-fan-ours", "md-fan-twin"} <= found["fans"]
