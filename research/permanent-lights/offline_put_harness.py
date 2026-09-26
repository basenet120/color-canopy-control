"""Offline harness: run aioafero 9.3.0 source (no network) to show
(1) how a Hubspace string light is split, and
(2) the exact PUT body aioafero emits for a hand-crafted multi-state write.

Third-party deps (aiohttp, bs4, securelogging) are stubbed; nothing is sent anywhere.
"""

import asyncio
import json
import logging
import os
import sys
import types

# Directory holding a checkout of github.com/Expl0dingBanana/aioafero (verified at main@58864da1, v9.3.0):
#   git clone https://github.com/Expl0dingBanana/aioafero "$AIOAFERO_CLONES/aioafero"
CLONES = os.environ.get("AIOAFERO_CLONES", "clones")

# ---- stub third-party modules -------------------------------------------------
aiohttp = types.ModuleType("aiohttp")
class _Err(Exception):
    pass
aiohttp.ClientResponseError = _Err
aiohttp.ContentTypeError = _Err
aiohttp.ClientError = _Err
aiohttp.ClientSession = object
aiohttp.ClientResponse = object
aiohttp.ClientTimeout = lambda *a, **k: None
aiohttp.ClientConnectionError = _Err
aiohttp.ServerDisconnectedError = _Err
aiohttp.WSMsgType = object
web_exceptions = types.ModuleType("aiohttp.web_exceptions")
web_exceptions.HTTPForbidden = type("HTTPForbidden", (Exception,), {})
web_exceptions.HTTPTooManyRequests = type("HTTPTooManyRequests", (Exception,), {})
aiohttp.web_exceptions = web_exceptions
aiohttp.__path__ = []  # mark as package so submodule imports resolve via sys.modules
client_exceptions = types.ModuleType("aiohttp.client_exceptions")
client_exceptions.ClientError = _Err
aiohttp.client_exceptions = client_exceptions
sys.modules["aiohttp"] = aiohttp
sys.modules["aiohttp.web_exceptions"] = web_exceptions
sys.modules["aiohttp.client_exceptions"] = client_exceptions
bs4 = types.ModuleType("bs4"); bs4.BeautifulSoup = object; sys.modules["bs4"] = bs4
sl = types.ModuleType("securelogging")
sl.LogRedactorMessage = lambda *a, **k: (a[0] if a else "")
sl.add_secret = lambda *a, **k: None
sl.remove_secret = lambda *a, **k: None
sys.modules["securelogging"] = sl

sys.path.insert(0, f"{CLONES}/aioafero/src")
sys.path.insert(0, f"{CLONES}/aioafero")

from aioafero.v1.controllers import light as light_mod  # noqa: E402
from aioafero.v1.controllers.light import LightController, light_callback  # noqa: E402
from tests.v1 import utils  # noqa: E402  (aioafero's own test helper)


class FakeResp:
    status = 200

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class FakeEvents:
    async def generate_events_from_update(self, dev):
        return None


class FakeBridge:
    """Implements only what LightController.update() touches."""

    def __init__(self, dev):
        self.logger = logging.getLogger("fake-bridge")
        self.events = FakeEvents()
        self.account_id = "ACCOUNT-ID"
        self.afero_client = "hubspace"
        self.temperature_unit = None
        self._dev = dev
        self.captured = []

    def generate_api_url(self, endpoint):
        return f"https://api2.afero.net{endpoint}"

    def get_afero_device(self, device_id):
        return self._dev

    async def request(self, method, url, **kwargs):
        self.captured.append({"method": method, "url": url, **kwargs})
        return FakeResp({"metadeviceId": kwargs["json"]["metadeviceId"], "values": kwargs["json"]["values"]})


def frame(n, rgb_fn):
    return [
        {"r": r, "g": g, "b": b, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
        for (r, g, b) in (rgb_fn(i) for i in range(n))
    ]


async def main():
    devs = utils.create_devices_from_data("light-with-speed.json")
    string_light = devs[2]
    print("== device:", string_light.manufacturerName, "|", string_light.model, "|", string_light.default_image)

    # (1) split behaviour
    resp = light_callback(string_light)
    print("== light_callback split_devices:")
    for d in resp.split_devices:
        print("   ", d.id, d.device_class, d.friendly_name)
    print("   remove_original:", resp.remove_original)

    # (2) the PUT aioafero emits for a hand-crafted write (same path as bridge.send_service_request)
    bridge = FakeBridge(string_light)
    ctrl = LightController(bridge)
    await ctrl.initialize_elem(string_light)
    n = 20  # this Novolink profile advertises '20-segment'; SQHDK03 count is unverified
    palette = [(255, 176, 64), (255, 140, 30), (255, 110, 20), (255, 200, 120)]
    states = [
        {"functionClass": "color-sequence-v2", "functionInstance": "custom-1",
         "value": {"color-sequence-v2": {
             "id": 0, "version": 1, "headerFlags": 128, "sequenceFlags": 0,
             "brightnessDepth": 100, "brightnessEffect": 0, "brightnessSpeed": 50,
             "motionEffect": 0, "motionSpeed": 50,
             "frameBuffer": {"flags": 0, "framebuffer": frame(n, lambda i: palette[i % 4])},
         }}},
        {"functionClass": "color-individual", "functionInstance": "custom", "value": "custom-1"},
        {"functionClass": "color-mode", "functionInstance": None, "value": "individual"},
        {"functionClass": "power", "functionInstance": None, "value": "on"},
    ]
    await ctrl.update(string_light.id, states=states)
    cap = bridge.captured[0]
    print("== captured request:")
    print("   method:", cap["method"])
    print("   url:", cap["url"])
    print("   headers:", cap["headers"])
    body = cap["json"]
    short = json.loads(json.dumps(body))
    fb = short["values"][0]["value"]["color-sequence-v2"]["frameBuffer"]["framebuffer"]
    short["values"][0]["value"]["color-sequence-v2"]["frameBuffer"]["framebuffer"] = fb[:2] + [f"... {len(fb) - 2} more entries ..."]
    print(json.dumps(short, indent=1))
    print("== number of HTTP requests for the whole scene:", len(bridge.captured))
    print("== body size (bytes, full framebuffer):", len(json.dumps(body)))


asyncio.run(main())
