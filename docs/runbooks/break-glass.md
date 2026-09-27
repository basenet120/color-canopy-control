# Break-glass — driving the canopy from the Hubspace app

**Who:** Alon or the second on-duty tech, and only when the Conductor can't do the job (a bug, a bad release, the
box won't come up) and waiting isn't acceptable. **When:** rarely; tell Alon first if you can. **How long:** 2 minutes
to take over, 2 minutes to hand back.

The break-glass kit: a staff phone with the **frozen** Hubspace app (auto-update off), signed in to the canopy
account, with the app **Group "Canopy"** holding the nine modules (made on install day), and the ops card.

---

## Take over

1. **Pause the Conductor first.** Otherwise it undoes every change you make in the app within about a minute.
   On the box (keyboard from the tech kit, or `ssh` over Tailscale from a phone or laptop):
   ```
   sudo canopy-stop
   ```
   Type `stop` when asked. The Conductor stops and stays stopped — the watchdog and even a reboot won't start it —
   until someone runs `canopy-start`. The lights keep their look; the kiosk shows "The canopy is resting".
   - Can't reach a terminal? **Pause guest input** on `/ops` at least, and know that the Conductor will still put
     its look back within about a minute of any app change. For one module only, opening it on `/ops/module`
     holds it (the canopy leaves it alone while that page is open, up to 10 idle minutes).
   - If the box is dead anyway, there's nothing to pause.
2. On the break-glass phone: Hubspace app → **Canopy** group → set the look (on/off, brightness, white, colour, a
   preset). The group changes all nine modules; tap a single module to change just one.
3. Don't edit the modules' **saved patterns (slots 1–3)** unless you have to: they hold the painted looks, and the
   Conductor has to recommission them afterwards.
4. Tell the floor what's going on. The kiosk stays "resting" the whole time.

## Hand back

1. Put the phone down: close the Hubspace app.
2. On the box:
   ```
   sudo canopy-start
   ```
   Within about a minute the Conductor puts Lowcountry Calm back on all nine modules — **anything changed in the
   app is undone** — and rewrites any slot that no longer matches what was commissioned (at most once per 10
   minutes per slot).
3. `/ops` → **Run** the 7 am checklist; if *Slot hashes* fails, press **Commission slots…**.
4. **Resume guest input.** Write down what happened for Alon (time, why, what you changed).

Never tap *Sign out of all other devices* in the app — that signs the Conductor out too (`outages.md`).
