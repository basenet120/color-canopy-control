# Research errata and precedence

When sources disagree, trust them in this order:
1. `docs/context/` (01–07) — maintained, corrected against aioafero 9.3.0's source on Sep 26 2026
2. `research/permanent-lights/_synthesis.md` — its **Contradictions** section resolves disagreements between the Sep 26 researchers
3. `research/permanent-lights/*.md` (Sep 26 2026)
4. `research/*.md` (Sep 19 2026 — written for the superseded 200-bulb design)

Skip `research/_raw_workflow_output.json` (raw, redundant with the digests).

## Superseded claims
| Where | Claim | Correction |
|---|---|---|
| `aioafero-source.md` (per-zone clones), Sep 19 brief | aioafero splits multizone string lights into per-zone Light clones | Only main/trim fixtures split; addressable strings get just a `…-light-party-mode` switch clone. Segments live in `color-sequence-v2` framebuffers. |
| `hardware.md` | custom pattern slots custom-1..4 | 3 slots (custom-1..3) on strings and RGBIC tape; custom-4 only on one Leedarson fixture |
| `app-reverse-eng.md` | state URL is https://semantics2.afero.net/… | TLS goes to api2.afero.net with a `Host: semantics2.afero.net` header |
| `performance.md`, `aioafero-source.md` | retries at 0.25/0.5/0.75 s | 3 attempts with waits of 0.5 s and 0.75 s |
| `permanent-lights/put_30_heads_example.txt` | `Authorization: Bearer {access_token}` | aioafero sends the OIDC **id_token**; use `bridge.request` so the library injects the right bearer |
| `permanent-lights/api-model.md` | write the frame and select the slot on every mode change | Store-and-select: write slots at commissioning, only select at show time (`_synthesis.md` Contradictions #3) |
| `permanent-lights/api-model.md` | "after VWBD shortens the wire between pucks" | Strings are never cut; slack is looped above the panel |
| `permanent-lights/api-model.md`, `app-behavior.md` | local paths under `/private/tmp/…/scratchpad/` | The harness and example are vendored in `research/permanent-lights/` (the harness needs `AIOAFERO_CLONES` pointing at an aioafero checkout) |
| `permanent-lights/_synthesis.md` §Cloud session setup, items 1–2 | 04/06 don't exist; 03 line 32 is wrong | Done — both fixed on Sep 26 |
| `permanent-lights/_synthesis.md` §Cloud session setup | `claude --remote`; Afero via cloud-session network allowlist | The CLI flag is `claude --cloud`; live Afero work runs on Railway staging or the venue box, never from a cloud build session |
| Various | "~2 s community polling floor" as a rate limit | Folklore from one integration's config; no measured limit exists |
| Various | Conclave as the confirmation channel | REST readback is the source of truth; Conclave is an optional change trigger and caches several values raw |
