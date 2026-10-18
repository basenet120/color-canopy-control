# Running the show

**Who:** the on-duty tech, with `/ops` open on a phone all day. **When:** public hours Oct 23–25 and the evening of
Oct 29. **How long:** the whole show; each action below takes seconds.

`/ops` is `http://<box address>:8000/ops/` on the show Wi-Fi (the ops password), or over Tailscale
(`mac-show-box.md`). It updates live; you don't need to reload it.

---

## The controls on `/ops`

- **Pause guest input** — the kiosk shows "The canopy is resting" and can't change anything. The canopy keeps its
  current look. Use it for photos, VIP moments, fixing something, or anything odd. **Resume guest input** undoes it.
  A pause stays on through a Conductor restart or a reboot until someone resumes it.
- **Safe look** — Lowcountry Calm (warm ivory) on all nine modules, now, whatever else is going on. Use it when the
  canopy looks wrong and you want a known-good look while you think.
- **Re-assert all** — every module whose Hubspace readback differs from its look gets the look again. Harmless;
  use it after power-cycling a module or when one looks stuck.
- **Change** (mode picker) — show a mode yourself (no guest cooldown).
- **Commission slots…** — rewrites the painted looks into the modules (install day, a controller swap, or when the
  7 am check says a slot drifted). Not during a busy moment: guest input waits for it (about a minute).

What guests see on the kiosk: six moods; after a choice the canopy changes in a few seconds ("Changing the light…"
→ "Ready"); the next choice (or brightness, or on/off) waits a moment, paced by the account's write budget (D70): 2 s
while the show drives two strands, 7.5 s once all nine are driven ("New scene in N s"); after 3 minutes with nobody
touching it the canopy goes back to Lowcountry Calm.

## "Needs attention" — what it means per module, and what to do

The **Needs attention** card names the module (Module 1–9: the plan's top-left panel as seen looking up is 1, the
middle is 5) and why:

| It says | It means | Do this |
|---|---|---|
| Offline | Hubspace can't reach that controller | Power-cycle **module N at its switch** (restart-matrix.md). About a minute later it's back and gets its look again. If not: `controller-swap.md`. |
| Wrong after re-asserts / keeps differing | Hubspace stored something else, or the module ignored a write | **Re-assert all** once. Then open the module on `/ops/module` and look at the telemetry strip. Power-cycle it if it stays. |
| Open on the module page by … | Someone is testing it on `/ops/module`; the canopy leaves it alone | Close that page when done (it lets go after 10 idle minutes by itself). |
| Needs a new sign-in | The Hubspace sign-in died | `outages.md` → Re-sign-in. |
| The canopy is resting | Hubspace or the internet isn't answering | `outages.md` → Internet down. Nothing to do on the canopy. |
| Head count | A string isn't counted (maybe only 12 pucks light) | Check its connectors, power-cycle the controller. Never cut a string. |

A module wrong for more than about 10 s makes the Conductor fall back to a simpler version of that mode (the kiosk
copy changes with it, so it stays true); it goes back after 5 clean minutes. That's by design.

## The Hubspace app: hands off during public hours

- Nobody opens the Hubspace app on the **canopy** account while guests
  are in. Anything changed there is undone by the Conductor within about a minute — and slot edits break the
  painted looks until they're recommissioned.
- **Never** tap *Sign out of all other devices* in the app: it signs the Conductor out within about 2 minutes
  (`outages.md`).
- **App version frozen:** the staff phones keep the app version tested before Oct 20 (an update in Aug 2026 removed
  custom colours from some effects). iPhone: Settings → App Store → App Updates **off**. Android: Play Store →
  Hubspace → ⋮ → **Enable auto update** off. Don't update until after strike.
- The app is for break-glass only (`break-glass.md`).

## Opening and closing

- **Open:** the 7 am check (`seven-am-check.md`) → **Resume guest input** just before doors.
- **Close:** **Pause guest input** (the kiosk rests). Leave the box and the canopy powered; the Conductor holds the
  look overnight and the backup runs at 04:30. If the canopy should be dim overnight, choose *The Goodnight Glow*
  with **Change**.

## When to call Alon

Anything the tables here don't cover, two modules down at once, the canopy resting for more than 15 minutes, a
sign-in that won't come back, or you're about to use break-glass.
