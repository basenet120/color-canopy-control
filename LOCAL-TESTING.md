# Local functionality testing

Open **TEST-CENTER.html** in a browser, or double-click **open-test-center.bat**. The tracker needs no installation and works without the app running. When the app is running, its **Open app page** links use the address recorded in the session.

This distribution starts from the user's `color-canopy-workshop-kit-1eb8551.zip` and includes the subsequent connection, viewport, individual-head mapping and effect-readback repairs. The source was tested against the real bench kit on September 28. Private account settings and local authorization are excluded. Read `VWBD-HANDOFF.md` before starting on a different computer; `VALIDATION.md` records current automated checks. Import `docs/live-workshop-2026-09-28.test-center.json` to recover the saved test session.

## First session

1. Record the mode, browser, target and actual running folder/build. Start a new session when changing mode; simulator and hardware results need independent records.
2. Run LOC-01, SET-01 and ACC-01 to establish the current app and kit.
3. Test MOD-01, MOD-05, MOD-06, MOD-07 and MOD-08: open the module, power, brightness, white and colour.
4. Record what the screen did and what the lights did separately. Leave a check **Not run** until performed; use **Blocked** for missing setup or capabilities.
5. Use **Add variant** for every preset, slot and numeric boundary. An individual pass does not establish that all variants work.
6. Export a JSON backup to preserve all sessions. Export a Markdown report to share the current session and its issue details with your technical partner.

The tracker records observations and opens pages. It does not run device commands. Browser storage is specific to this computer, browser and file/location; moving the HTML or changing browsers may not carry results. Import merges backup sessions and preserves an imported copy on ID conflicts. Use exported JSON as the durable record. Export opens a text preview with Download file and Copy text options; if a browser blocks downloads, copy the text into a file. Import accepts a file or pasted JSON.

## Testing structure

- `TEST-CENTER.html`: interactive checklist, filters, variants, session history, JSON import/export and Markdown report.
- `docs/interface-troubleshooting-workflow.md`: full local workflow, expected behavior and recovery provenance.
- `docs/interface-test-log.md`: plain Markdown issue-report template.
- `docs/test-cases.json`: machine-readable inventory.
- `tests/test_interface_structure.py`: dependency-free checks for page assets, duplicate IDs, the checklist and supplied-source integrity.
- `docs/testing-baseline.json`: archive identity and original-file hashes.

Run structural checks from this folder with an installed Python:

```powershell
python -m unittest discover -s tests -p "test_interface_structure.py" -v
```

These structural checks do not prove API behavior, browser interactions or physical output. See `VALIDATION.md` for checks actually performed while preparing this package.

## Source and recovery limits

The workflow came from **Troubleshoot Hubspace control app** on VWBD, chat `01a0e283-d4eb-7df0-9f58-96a363797625`, workflow commit `54190de` dated September 27, 2026. The source reported 88 cases. The chat retrieval limit provided 86 complete cases and part of UX-07. UX-07 was completed locally, and LOC-01 was added for the local baseline, giving this inventory 88 checks. The unavailable final source case and trailing prose are not represented as an exact copy. The companion issue template was recovered in full.

The recovered workflow is background; the later local hardware evidence is in `docs/current-live-test-session.md`, `docs/preset-functionality-session.md` and `docs/custom-pattern-session.md`. Keep those observations separate from new VWBD tests. The full interface checklist is not complete merely because individual API variants passed.

The original source uses simulated Ops/kiosk controls in workshop bench mode; Module and Bench can target the real kit. Per-puck painting depends on the kit's proven capability gates. Check those prerequisites before classifying a disabled control as a defect.
