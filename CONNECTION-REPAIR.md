# Hubspace device discovery repair — September 28, 2026

The user reported that **Show devices** failed with `Couldn't reach Hubspace (ClientConnectionError)` after account sign-in. The running copy contained the response-lifecycle bug previously repaired on VWBD.

`conductor/drivers/afero.py` now uses `bridge.create_request(...)` and reads the body while the response context is still open. The existing timeout covers the entire request and body read. HTTP error classification, authentication, token persistence, request gating and one-attempt behavior remain in place. Device registration and allowlists still apply normally.

Added `tests/test_hubspace_transport.py`: 14 unittest tests using real aiohttp/aioafero behavior, dummy credentials and a loopback server. Together with the 6 interface structure tests, **all 20 tests passed** on September 28, 2026. The tests reproduce the old response-read error and verify the repaired discovery path, status handling, timeouts and blocked writes.

The existing Python installation could not be used from Codex because Windows denied access even after a permission grant. A separate runtime under this chat's `work/hubspace-runtime` was installed with the same 33 dependency versions. The normal `start.bat` still uses the user's original installation and also loads the repaired source after a restart.

The repaired app has been restarted. Live discovery succeeded, the user registered the bench kit, and the normal allow command authorized it. Power off and on both passed cloud verification and the user's physical observation. Brightness, white and colour checks are recorded separately in `docs/current-live-test-session.md`. Account credentials were preserved; registry and allowlist updates used the normal registration/startup workflow.

Prior `VALIDATION.md` and baseline hashes describe the package before this repair. The connection source and new test are intentional changes from that initial baseline.
