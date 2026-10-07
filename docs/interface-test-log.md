# Color Canopy: test log and issue tracker

Companion plan: [Interface troubleshooting workflow](interface-troubleshooting-workflow.md).

No feature below is automatically marked Pass. On the other computer, prior troubleshooting established that device discovery and registration worked after the response-lifecycle repair; the user now reports successful connection and unspecified interface failures. Current control behavior and capability prerequisites need a fresh session check.

Local interactive record: open `../TEST-CENTER.html`. Export a JSON backup at the end of each session; export a Markdown report for your technical partner. Records are saved in this browser on this computer.

## Session

- Date / tester:
- Checkout and Git commit:
- Runtime: simulator / real workshop / venue
- Browser and device:
- Target module:
- Account connected / registered / allowed:
- Gate A result; Gate B/C flags proven on this kit:
- Initial look, selected slot, and brightness:
- Other open control pages / active Hold or sweep:

## Results

Copy one row per test ID and option variant from the workflow. Use Not run, Pass, Fail, Blocked, or Not applicable. Record design improvements in the issue table even when functionality passes.

| Test ID + option | Environment/browser | UI result | Physical result or N/A | Overall status | Evidence / issue ID |
|---|---|---|---|---|---|
| MOD-01 Open intended module | | | | Not run | |
| MOD-05 Power on/off/on | | | | Not run | |
| MOD-06 Brightness | | | | Not run | |
| MOD-07 White temperature | | | | Not run | |
| MOD-08 RGB colour | | | | Not run | |

## Issues and improvements

| Issue ID | Test ID | Page/control | Type: blocker/functional/usability/visual | Reproducible? | Owner | State | Fix commit | Retest result |
|---|---|---|---|---|---|---|---|---|
| UI-001 | | | | | | Needs reproduction | | |

States: Needs reproduction → Diagnosed → Fix ready → Ready for retest → Verified. Use Blocked with a named prerequisite when work cannot continue. A fix commit alone does not mean Verified.

## Copy this report for each issue

```text
Issue ID / test ID:
Page URL and exact control label:
Browser/device and runtime mode:
Real or simulated target:
Git commit:

Starting state and prerequisites:

Steps:
1.
2.
3.

Expected screen behavior:
Expected physical behavior, if applicable:
Actual screen behavior:
Actual physical behavior, if applicable:
Exact error text:
Time of occurrence:
Reproduces: every time / sometimes / once
What changed before the failure:
Evidence: anonymized JSON / screenshot / short video / relevant console error

Suggested improvement, if this is a design issue:

Technical diagnosis:
Fix commit:
Automated checks actually run:
Human retest steps and result:
Remaining limitations:
```

## Session closeout

- Restore the intended reference look and the actual physical calibration.
- Stop any sweep/job and use Done to release module ownership.
- Save evidence and assign each open issue a next action.
- Identify the first untested or blocked row for the next session.
