# Local workshop fork

This is a complete source copy of `color-canopy-workshop-kit-1eb8551/color-canopy-workshop`.
The supplied folder had no Git history. This fork starts with an import commit on `main`;
the connection repair is on `fix/hubspace-connection`.

## Run this copy on Windows

1. Stop the old app with Ctrl+C in its console. Only one app can use port 8000.
2. Double-click `start.bat` in this folder. The first launch creates this copy's Python environment.
3. Open http://localhost:8000/ops/accounts/ and choose **Show devices**.
4. Choose **Use as Bench kit** on the intended lights.
5. Stop the app, open Command Prompt in this folder, run `start.bat allow`, and answer **y**.
6. Start `start.bat` again and open http://localhost:8000/ops/module/.

Local settings and workshop data were copied. `CONDUCTOR_DATA_DIR` now points to this
fork's `workshop-data` folder. The page password is the same. If Hubspace rejects the
copied sign-in token, sign in again on the Accounts page.

The original app folder is unchanged. Credentials, encrypted tokens, telemetry, and
device overrides stay local and are ignored by Git. Share the Git repository rather
than a ZIP of the entire working folder if those private files should be excluded.

The generated `.venv` and Python caches are recreated by the launcher; all supplied
source code, web files, configuration, documentation, launchers, and the dependency
lockfile are included.

## Connection repair

With aioafero 9.3.0 and aiohttp 3.14.3, `bridge.request()` reads the HTTP response and
releases it before returning. The adapter's second `response.read()` then raises
`ClientConnectionError`, including for a successful HTTP 200. This prevents discovery
and can incorrectly raise the Hubspace-unreachable alert.

The adapter now uses `bridge.create_request()` and reads the response before the
context exits. Authentication and the gated transport remain in place. Each adapter
call makes one attempt, preserves HTTP status classification, and bounds both the
request and body read with the existing timeout.

## Verification

Run `uv sync --frozen --extra live`, then `uv run --no-sync pytest`.

The 13 regression cases use the real aiohttp/aioafero libraries with a loopback server
and dummy credentials. They cover discovery, success and error statuses, one send per
request, body timeouts, and rejection of writes to devices outside the allowlist.
They do not contact Hubspace or control real lights.
