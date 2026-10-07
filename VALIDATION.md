# Distribution validation — October 2, 2026

The staged VWBD distribution passed **51 Python tests and 6 JavaScript tests**. The actual Test Center importer accepted the saved history unchanged: **88 base cases and 22 variants**.

Commands, run from this packaged app folder using the existing repaired Windows runtime (Python 3.12.14; Node 24.19):

```text
python -B -m unittest discover -s tests -v
node --test tests/preset-preview.test.mjs
node tests/validate_live_import.cjs
```

The suites cover connection response lifetime and timeout/status handling using a local dummy server, authorization refusal, interface structure, preset rendering and animation, the 30/60 controller map, address-probe guards, bounded effect probes and restoration/cancellation, and full effect-header readback. Detailed output is in `docs/release-validation.json`.

The packaging process checks source parity with the working app before staging, re-scrubs all 13 included hardware evidence records, checks for known private credential/device values, excludes runtime data and virtual environments, generates `MANIFEST.sha256`, and verifies the completed archive against that manifest and ZIP CRCs. `docs/source-changes-from-user-baseline.json` lists files changed from the original 119-file baseline; later repairs intentionally differ from those original hashes.

No real light commands were sent during packaging. These automated tests do not establish that the app has been installed, signed in, authorized or physically tested on VWBD. Dependencies were not freshly reinstalled during packaging. The live observations are from September 28 and are preserved in `VWBD-HANDOFF.md` and the session reports. The custom-motion blocks test still needs a physical observation; the rest of the app's full browser workflow is not certified.

The initial tracker-only QA, conducted before the live repairs and tests, is retained as `docs/initial-tracker-validation-2026-09-28.md`. Its old statements about no live testing refer to that earlier stage, not this distribution.
