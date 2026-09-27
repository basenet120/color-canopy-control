"""The vignette compiler (P4): per-kind action → rows against aioafero 9.3.0's encodings, range checks, the lock
confirm, scenes as data, and the synthesized simulator profiles. (What aioafero's controllers put on the wire for
the same intents is compared directly in tests/test_vignette_afero.py.)"""

from __future__ import annotations

import copy

import pytest

from conductor.capabilities import detect
from conductor.clock import VirtualClock, run_virtual
from conductor.config import ConfigError, load_registry
from conductor.drivers.base import StateRow
from conductor.gate import Purpose
from conductor.rows import compare
from conductor.sim.profiles import (
    SYNTHESIZED,
    is_synthesized,
    load_profile,
    profile_names,
    profile_parts,
)
from conductor.vignettes import (
    ACTIONS,
    ConfirmRequired,
    DeviceModel,
    VignetteError,
    VignettesFile,
    apply_action,
    check_against_registry,
    compile_action,
    compile_step,
    default_look,
    load_vignettes,
    look_from_rows,
    pretty,
    vector,
)

PROFILE_KIND = {
    "ecosmart-a19-rgb": "bulb",
    "outdoor-plug-2": "plug",
    "indoor-plug": "plug",
    "defiant-deadbolt": "lock",
    "ceiling-fan": "fan",
    "path-lights": "path",
    "string-20seg": "string",
}


def model(profile: str) -> tuple[DeviceModel, dict]:
    functions, states = profile_parts(profile)
    m = DeviceModel.from_profile(f"test-{profile}", PROFILE_KIND[profile], functions)
    return m, look_from_rows(m, states)


def wire(rows: list[StateRow]) -> list[dict]:
    return [r.to_json() for r in rows]


def row(cls: str, value, instance: str | None = None) -> dict:
    return {"functionClass": cls, "functionInstance": instance, "value": value}


# ---- the synthesized profiles ---------------------------------------------------------------------------------------
def test_every_vignette_profile_is_listed_and_labelled_synthesized():
    names = profile_names()
    for name in ("ecosmart-a19-rgb", "outdoor-plug-2", "defiant-deadbolt", "ceiling-fan", "path-lights"):
        assert name in names and is_synthesized(name)
    assert not is_synthesized("string-20seg") and not is_synthesized("sqhdk03-sim")
    import conductor.sim.profiles as P

    assert "synthesized from aioafero 9.3.0 controller source — UNVERIFIED until a real dump" in P.__doc__


@pytest.mark.parametrize("name", sorted(SYNTHESIZED))
def test_synthesized_profiles_are_shaped_like_aioafero_devices(name):
    dump = load_profile(name)
    for key in ("id", "device_id", "model", "device_class", "default_name", "default_image", "friendly_name"):
        assert isinstance(dump[key], str) and dump[key]
    assert dump["id"].startswith("sim-profile-")  # fixture ids only, never a real UUID
    declared = {(f["functionClass"], f.get("functionInstance")) for f in dump["functions"]}
    for state in dump["states"]:
        key = (state["functionClass"], state.get("functionInstance"))
        assert key in declared or state["functionClass"] in (
            "available",
            "visible",
            "direct",
            "wifi-rssi",
            "wifi-steady-state",
        )
    for fn in dump["functions"]:
        for value in fn["values"]:
            assert "name" in value
            if fn["type"] == "numeric":
                assert {"min", "max", "step"} <= set(
                    value["range"]
                )  # aioafero's process_range reads all three


def test_capabilities_from_the_profiles():
    bulb = detect(profile_parts("ecosmart-a19-rgb")[0])
    assert bulb.rgb and bulb.color_modes == ["white", "color", "sequence"] and bulb.cct_range == (2200, 6500)
    assert "custom" not in bulb.presets and "christmas" in bulb.programs and bulb.speed_range == (-10, 10)
    plug = detect(profile_parts("outdoor-plug-2")[0])
    assert plug.outlets == ["outlet-1", "outlet-2"]
    lock = detect(profile_parts("defiant-deadbolt")[0])
    assert lock.lock and lock.lock_values == ["locked", "locking", "unlocked", "unlocking"]
    fan = detect(profile_parts("ceiling-fan")[0])
    assert fan.fan and fan.fan_speeds == [
        f"fan-speed-6-{n}" for n in ("016", "033", "050", "066", "083", "100")
    ]


@pytest.mark.parametrize("profile", sorted(p for p, k in PROFILE_KIND.items() if k != "lock"))
def test_the_look_a_device_already_shows_round_trips(profile):
    """What a device reports becomes rows that compare equal to that report — so the page can call it Ready."""
    m, look = model(profile)
    _, states = profile_parts(profile)
    rows, expect = vector(m, look)
    assert rows == expect and compare(rows, states) == []


# ---- bulbs --------------------------------------------------------------------------------------------------------
def test_bulb_colour_is_rgb_plus_color_mode_in_aioafero_order():
    m, look = model("ecosmart-a19-rgb")
    c = compile_action(m, look, "color", "#E0182D")
    assert wire(c.rows) == [
        row("power", "on"),
        row("color-rgb", {"color-rgb": {"r": 224, "g": 24, "b": 45}}),
        row("color-mode", "color"),
        row("brightness", 100),
    ]
    assert c.look["mode"] == "color" and c.expect == c.rows


def test_bulb_white_temperature_is_a_string_and_range_checked_never_snapped():
    m, look = model("ecosmart-a19-rgb")
    c = compile_action(m, look, "color_temperature", 3000)
    assert wire(c.rows) == [
        row("power", "on"),
        row("color-mode", "white"),
        row("color-temperature", "3000"),
        row("brightness", 100),
    ]
    for bad, why in (
        (1800, "outside this device's range 2200–6500 K"),
        (7000, "outside"),
        (2750, "steps of 100"),
    ):
        with pytest.raises(VignetteError, match=why):
            compile_action(m, look, "color_temperature", bad)


def test_bulb_effects_by_name_from_the_device_list():
    m, look = model("ecosmart-a19-rgb")
    preset = compile_action(m, look, "effect", "fade-7")
    assert wire(preset.rows) == [
        row("power", "on"),
        row("color-mode", "sequence"),
        row("brightness", 100),
        row("color-sequence", "fade-7", "preset"),
        row("speed", 0, "color-sequence"),
    ]
    program = compile_action(m, look, "effect", "christmas")
    assert wire(program.rows)[3:5] == [
        row("color-sequence", "custom", "preset"),
        row("color-sequence", "christmas", "custom"),
    ]
    with pytest.raises(VignetteError, match="isn't one of this device's effects"):
        compile_action(m, look, "effect", "halloween")  # a string-light preset, not the bulb's
    with pytest.raises(VignetteError):
        compile_action(m, look, "effect", "custom")  # the selector, not an effect


def test_bulb_speed_brightness_and_white_mode():
    m, look = model("ecosmart-a19-rgb")
    with pytest.raises(VignetteError, match="Choose an effect first"):
        compile_action(m, look, "speed", 3)
    effect = compile_action(m, look, "effect", "fade-3").look
    fast = compile_action(m, effect, "speed", 7)
    assert wire(fast.rows)[-1] == row("speed", 7, "color-sequence")
    with pytest.raises(VignetteError, match="Speed goes from -10 to 10"):
        compile_action(m, effect, "speed", 11)
    dim = compile_action(m, look, "brightness", 35)
    assert row("brightness", 35) in wire(dim.rows)
    for bad in (0, 101):
        with pytest.raises(VignetteError, match="outside this device's range 1–100"):
            compile_action(m, look, "brightness", bad)
    colour = compile_action(m, look, "color", "#3C7DD9").look
    back = compile_action(m, colour, "white", True)
    assert row("color-mode", "white") in wire(back.rows) and row("color-temperature", "2700") in wire(
        back.rows
    )


def test_power_off_sends_only_power_and_remembers_the_look():
    m, look = model("ecosmart-a19-rgb")
    colour = compile_action(m, look, "color", "#3C7DD9").look
    off = compile_action(m, colour, "power", False)
    assert wire(off.rows) == [row("power", "off")]
    on = compile_action(m, off.look, "power", True)
    assert row("color-rgb", {"color-rgb": {"r": 60, "g": 125, "b": 217}}) in wire(on.rows)


def test_only_stock_app_actions_per_kind():
    m, look = model("ecosmart-a19-rgb")
    for action in ("slot", "fan_speed", "lock", "outlets", "restore-values", "party_mode"):
        with pytest.raises(VignetteError):
            compile_action(m, look, action, "x")
    assert set(ACTIONS["bulb"]) == {
        "power",
        "brightness",
        "color_temperature",
        "white",
        "color",
        "effect",
        "speed",
    }


# ---- plugs --------------------------------------------------------------------------------------------------------
def test_an_outlet_toggle_is_one_put_with_both_outlets():
    m, look = model("outdoor-plug-2")
    c = compile_action(m, look, "power", True, outlet="outlet-1")
    assert wire(c.rows) == [row("power", "on", "outlet-1"), row("power", "off", "outlet-2")]
    both = compile_action(m, c.look, "power", False)
    assert wire(both.rows) == [row("power", "off", "outlet-1"), row("power", "off", "outlet-2")]
    with pytest.raises(VignetteError, match="no outlet"):
        compile_action(m, look, "power", True, outlet="outlet-3")


def test_a_single_outlet_plug_has_no_instance():
    m, look = model("indoor-plug")
    assert m.outlets == [("power", None)]
    assert wire(compile_action(m, look, "power", True).rows) == [row("power", "on")]
    assert m.controls({"power": "Humidifier"})["outlets"] == [{"key": "power", "label": "Humidifier"}]


# ---- the lock -----------------------------------------------------------------------------------------------------
def test_a_lock_needs_confirm_and_is_verified_against_the_settled_position():
    m, look = model("defiant-deadbolt")
    assert look == {"position": "locked"}
    with pytest.raises(ConfirmRequired):
        compile_action(m, look, "unlock")
    c = compile_action(m, look, "unlock", confirm=True)
    assert wire(c.rows) == [row("lock-control", "unlocking")]  # aioafero LockController.unlock()
    assert wire(c.expect) == [row("lock-control", "unlocked")]  # what the lock reports once the bolt moved
    assert wire(compile_action(m, c.look, "lock", confirm=True).rows) == [row("lock-control", "locking")]
    with pytest.raises(VignetteError):
        compile_action(m, look, "power", True, confirm=True)
    with pytest.raises(VignetteError):
        vector(m, look)


def test_the_simulated_lock_settles_and_reports_back():
    from conductor.config import OverlayFile, Registry, RegistryFile
    from conductor.drivers.sim import SimDriver
    from conductor.sim.settings import SimSettings

    registry = Registry(
        RegistryFile.model_validate(
            {
                "accounts": {"entry": {}},
                "devices": [
                    {
                        "id": "door",
                        "role": "vignette",
                        "vignette": "entry",
                        "kind": "lock",
                        "account": "entry",
                    }
                    | {"profile": "defiant-deadbolt"}
                ],
            }
        ),
        OverlayFile(),
        None,
    )

    async def main():
        sim = SimDriver(
            registry,
            clock=VirtualClock(),
            settings=SimSettings.dev().with_(throttle_429_rate=0, silent_drop_rate=0),
        )
        result = await sim.write(
            "sim-door", [StateRow("lock-control", None, "unlocking")], purpose=Purpose.ENGINE
        )
        before = (await sim.read_state("sim-door")).get("lock-control")
        import asyncio

        await asyncio.sleep(5)
        after = (await sim.read_state("sim-door")).get("lock-control")
        return result, before, after

    result, before, after = run_virtual(main)
    assert result.ok and before == "unlocking" and after == "unlocked"


# ---- fans, strings, path lights ---------------------------------------------------------------------------------------
def test_fan_speed_comes_from_the_fans_own_list_and_turns_it_on():
    m, look = model("ceiling-fan")
    c = compile_action(m, look, "fan_speed", "fan-speed-6-083")
    assert wire(c.rows) == [row("power", "on", "fan-power"), row("fan-speed", "fan-speed-6-083", "fan-speed")]
    for bad in ("fan-speed-6-000", "high", 3):
        with pytest.raises(VignetteError, match="speeds"):
            compile_action(m, look, "fan_speed", bad)
    assert [s["label"] for s in m.controls()["speeds"]] == ["1", "2", "3", "4", "5", "6"]
    assert wire(compile_action(m, c.look, "power", False).rows) == [row("power", "off", "fan-power")]


def test_string_looks_use_the_canopy_full_state_vector():
    m, look = model("string-20seg")
    slot = compile_action(m, look, "slot", "custom-2")
    assert wire(slot.rows) == [
        row("power", "on"),
        row("color-individual", "custom-2", "custom"),
        row("color-mode", "individual"),
        row("brightness", 100),
        row("speed", -10, "color-sequence"),
        row("toggle", "off", "party-mode"),
    ]
    white = compile_action(m, look, "color_temperature", 2700)
    assert row("color-temperature", "2700") in wire(white.rows) and row(
        "toggle", "off", "party-mode"
    ) in wire(white.rows)
    with pytest.raises(VignetteError):
        compile_action(m, look, "color_temperature", 2200)  # this string starts at 2700 K
    assert [s["label"] for s in m.controls()["slots"]] == ["Pattern 1", "Pattern 2", "Pattern 3"]


def test_path_lights_are_power_and_brightness_only():
    m, look = model("path-lights")
    assert wire(compile_action(m, look, "brightness", 60).rows) == [row("power", "on"), row("brightness", 60)]
    for action, value in (("color", "#FF0000"), ("color_temperature", 3000), ("effect", "rainbow")):
        with pytest.raises(VignetteError, match="can't do"):
            compile_action(m, look, action, value)


# ---- scenes as data -------------------------------------------------------------------------------------------------
def test_the_repo_scenes_match_the_registry():
    rooms = load_vignettes()
    check_against_registry(rooms, load_registry())
    assert {r: rooms.rooms[r].title for r in rooms.rooms} == {
        "entry": "A smarter welcome",
        "living": "Set the holiday mood",
        "bedroom": "Find your evening glow",
        "garden": "Bring the garden to life",
    }
    holiday = next(s for s in rooms.rooms["living"].scenes if s.id == "holiday-evening")
    assert holiday.label == "Holiday evening"


def test_a_scene_step_applies_power_last():
    m, look = model("ecosmart-a19-rgb")
    off = compile_step(m, look, {"power": False, "color_temperature": 2700, "brightness": 60})
    assert wire(off.rows) == [row("power", "off")] and off.look["brightness"] == 60
    on = compile_step(m, look, {"color_temperature": 2700, "brightness": 60, "power": True})
    assert wire(on.rows) == [
        row("power", "on"),
        row("color-mode", "white"),
        row("color-temperature", "2700"),
        row("brightness", 60),
    ]


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"device": "entry-deadbolt", "set": {"unlock": True}}, "locks can't be part of a scene"),
        ({"kind": "lock", "set": {"power": True}}, "locks can't be part of a scene"),
        ({"kind": "bulb", "set": {"color": "red"}}, "needs a colour"),
        ({"kind": "bulb", "set": {"brightness": "bright"}}, "needs a whole number"),
        ({"kind": "bulb", "set": {"sparkle": True}}, "not an action"),
        ({"kind": "bulb", "device": "living-lamp-1", "set": {"power": True}}, "exactly one of"),
    ],
)
def test_bad_scene_steps_are_refused_with_a_plain_reason(step, message):
    raw = {
        "rooms": {
            "living": {"space": "x", "title": "y", "scenes": [{"id": "s", "label": "S", "steps": [step]}]}
        }
    }
    with pytest.raises(ValueError, match=message):
        VignettesFile.model_validate(raw)


def test_scenes_are_cross_checked_against_the_registry():
    registry = load_registry()
    raw = {
        "rooms": {room: {"space": room, "title": room} for room in ("entry", "living", "bedroom", "garden")}
    }
    raw["rooms"]["living"]["scenes"] = [
        {"id": "a", "label": "A", "steps": [{"device": "garden-string", "set": {"power": True}}]},
        {"id": "b", "label": "B", "steps": [{"kind": "plug", "set": {"brightness": 50}}]},
    ]
    raw["rooms"]["living"]["outlets"] = {"living-lamp-1": {"outlet-1": "Tree"}}
    with pytest.raises(ConfigError) as err:
        check_against_registry(VignettesFile.model_validate(raw), registry)
    text = str(err.value)
    assert "'garden-string' isn't a vignette device in this room" in text
    assert "a plug can't do brightness" in text
    assert "'living-lamp-1', which isn't a plug" in text
    missing = copy.deepcopy(raw)
    del missing["rooms"]["garden"]
    missing["rooms"]["living"].pop("scenes")
    missing["rooms"]["living"].pop("outlets")
    with pytest.raises(ConfigError, match="room 'garden', which config/vignettes.yaml doesn't list"):
        check_against_registry(VignettesFile.model_validate(missing), registry)


def test_the_registry_refuses_a_vignette_device_without_room_or_kind(tmp_path):
    (tmp_path / "devices.yaml").write_text(
        "accounts: {living: {}}\n"
        "devices:\n"
        "  - {id: lamp, role: vignette, vignette: living, kind: lamp, account: living}\n"
    )
    with pytest.raises(ConfigError, match="needs `kind:` set to one of bulb, plug, lock, fan, string, path"):
        load_registry(tmp_path)
    (tmp_path / "devices.yaml").write_text(
        "accounts: {living: {}}\ndevices:\n  - {id: lamp, role: vignette, kind: bulb, account: living}\n"
    )
    with pytest.raises(ConfigError, match="needs `vignette:`"):
        load_registry(tmp_path)


def test_the_placeholder_census_is_in_the_registry():
    registry = load_registry()
    by_room: dict[str, list[str]] = {}
    for d in registry.by_role("vignette"):
        by_room.setdefault(d.vignette, []).append(d.kind)
    assert by_room == {
        "entry": ["lock"],
        "living": ["bulb"] * 4 + ["plug"],
        "bedroom": ["bulb"] * 4 + ["plug"],  # the fan is there but disabled until confirmed (06 #18)
        "garden": ["string", "path", "plug"],
    }
    fan = registry.get("bedroom-fan")
    assert fan.enabled is False and fan.kind == "fan" and fan.profile == "ceiling-fan"
    for d in registry.by_role("vignette"):
        assert (
            d.account == d.vignette and d.writable and d.metadevice_id is None
        )  # real ids only in the overlay
    from conductor.config import CONFIG_DIR

    text = (CONFIG_DIR / "devices.yaml").read_text()
    assert "PLACEHOLDER census" in text


def test_defaults_and_pretty_names():
    m, _ = model("ecosmart-a19-rgb")
    look = default_look(m)
    assert look["cct"] == 2700 and look["on"] is False and look["effect"] == "flash"
    assert (
        pretty("fade-3") == "Fade 3" and pretty("july-4th") == "July 4th" and pretty("wake-up") == "Wake up"
    )
    with pytest.raises(VignetteError):
        apply_action(m, look, "brightness", "lots")
