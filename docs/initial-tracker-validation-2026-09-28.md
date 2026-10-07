> Historical record from initial extraction, before the later live repairs and tests. For the current release, see ../VALIDATION.md and ../VWBD-HANDOFF.md.

# Validation — September 28, 2026

## Completed

- Verified SHA-256 hashes for all **119 original files** against the supplied ZIP: all unchanged.
- Ran `python -m unittest discover -s tests -p test_interface_structure.py -v`: **6 passed**. Checks cover the ten app page templates, duplicate IDs, local scripts/stylesheets, case inventory consistency and tracker links/data.
- Checked the tracker's JavaScript with `node --check`: passed.
- Opened the test center in the Codex browser and verified all 88 base checks load as Not run in a fresh session.
- Verified search, result filtering, stage navigation, status entry, separate screen/physical notes and option variants.
- Reloaded the page and verified the synthetic QA session, notes and variant persisted.
- Verified JSON export content, rejection of invalid pasted backups, successful backup import and preservation of an existing session as a separate imported copy.
- Verified the Markdown report contains mode, observations and variant results.
- Fixed narrow-window overflow and verified document width fits the available 596-pixel browser viewport.
- Verified a new real-workshop session starts with 88 Not run, 0 Pass, 0 Fail and 0 Blocked. Synthetic tracker QA records are labeled explicitly and are separate from that session.
- Browser error log was empty after the final verification.

## Limits

The extracted app was not started or connected to Hubspace. No light command was sent and no physical light behavior was observed. The structural checks and tracker QA do not certify app functionality. All hardware checks await the user's session.

The browser's direct download event did not yield a saved-file confirmation. Exports therefore include a visible text preview and copy fallback, and imports accept pasted JSON. The JSON round trip through those controls was verified. Native download and file-picker flows were not validated.

Only the available narrow desktop browser viewport was inspected; a separate phone/device pass is still in the functionality checklist. Browser interactions were verified using keyboard activation after pointer-coordinate mismatch in the browser automation surface.

The original remote workflow was partially truncated by chat retrieval. `LOCAL-TESTING.md` records exactly what was recovered and what was added locally.
