# Restart matrix — who restarts what, how, and how long it takes

**Who:** as in the table. **When:** when something is stuck and the fix in `show-operation.md` or `outages.md`
says restart it. **How long:** seconds to minutes, per row. Always the smallest restart that fixes it, top first.

"The box" commands need a keyboard on the box or `ssh` over Tailscale, then `sudo` with the box password.
The lights keep their look through every restart in this table; only a module's own power cycle turns that module
off for a moment.

| What | Who | How | How long | What guests see |
|---|---|---|---|---|
| **Kiosk browser** (frozen, blank, wrong page) | on-duty tech | USB keyboard on the box → **Alt+F4** (it reopens by itself), or `sudo canopy-restart kiosk` | 5–10 s | Screen blank a few seconds |
| **Vignette tablet page** | on-duty tech | Leave Guided Access, reload the page; lost sign-in: new link from `/ops` → Set up a device | 30 s (2 min to re-enroll) | That room's tablet only |
| **Conductor** (`/ops` won't load, kiosk resting for no reason) | on-duty tech | `sudo canopy-restart` — or wait: the watchdog restarts it after 3 failed checks (≈ 90 s) | 20–40 s; canopy returns to Lowcountry Calm | Kiosk "resting", then back |
| **Whole box** | on-duty tech | `sudo canopy-restart box`; if frozen: hold the power button 5 s, then press it once | 2–3 min until the kiosk is back | Kiosk dark 2–3 min |
| **Show router** | on-duty tech (tell Alon) | Power-cycle it at its plug (it's on the UPS) | 2–5 min; modules rejoin Wi-Fi by themselves (**UNVERIFIED** how fast), then the Conductor re-applies within 60 s | Kiosk "resting" meanwhile; tablets drop |
| **One module** (offline, stuck, only 12 pucks lit) | on-duty tech or VWBD | **Power-cycle module N at its switch** (off 10 s, on); then **Re-assert all** on `/ops` | About 1 min to rejoin (**UNVERIFIED**); it gets its look back automatically | That module dark for a minute |
| **Kit swap** (controller dead) | Alon or the second on-duty tech, with VWBD | `controller-swap.md` | 30–45 min plus VWBD's physical work | Guest input paused; that module dark |
| **Hubspace sign-in** | one of the two named people | `outages.md` → Re-sign-in | About 5 min | Canopy holds its look; kiosk can't change it |
| **Release** (a bad release) | Alon / second on-duty tech | `sudo canopy-restart --tag <previous tag>` (`release-and-freeze.md`) | About 1 min | Kiosk "resting" 30 s |
| **Break-glass** | Alon / second on-duty tech | `sudo canopy-stop` … `sudo canopy-start` (`break-glass.md`) | 2 min each way | Kiosk "resting" throughout |

After any restart: `/ops` → 7 am checklist → **Run**, and `sudo canopy-status` if you're on the box.

**Guest input stays paused across every restart in this table.** A pause is saved on the box (`<data>/state/`), so a
watchdog restart, a reboot, a release or `canopy-start` never lets guests back in on its own; `/ops` shows "Guest input
paused since …" and an alert. Press **Resume guest input** only when the checks are done.

Don't power-cycle modules one after another hoping for the best: each one needs about a minute to come back, and
a module that's still offline after one power cycle needs `controller-swap.md`, not more cycles.
