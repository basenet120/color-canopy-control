# Color Canopy — Prototype 01 test program (workshop kit)

This program lets you control **one Color Canopy test module** — one Hampton Bay Hubspace permanent-light kit, 30 pucks —
from a phone in your workshop: paint individual pucks, try the show colours through the Mylar, check the wiring order,
and hold a look for photos. It is the same software that will run the canopy at 284 Meeting Street, limited to one
module.

It runs on **one laptop** in the shop. You use it from that laptop's browser or from a phone on the same Wi-Fi.
The lights are reached through the Hubspace cloud, exactly like the Hubspace app — so the module only needs power and
the shop Wi-Fi.

> Questions or anything odd: Alon (tech lead). Send screenshots and the "Copy anonymized JSON" text — never passwords.

---

## What you need
- **A laptop** (Mac or Windows) that stays on the shop Wi-Fi while you test. About 1 GB free space.
- **The test module** set up in the Hubspace app on a 2.4 GHz Wi-Fi network, showing as **one device with 30 pucks**.
- **The Hubspace account** that module is on (email + password; the account must *not* have two-step verification
  turned on). Use the test/bench account Alon gives you — **never one of the show accounts.**
- **A phone** on the same Wi-Fi (optional, but it's the easiest way to use it under the module).

## 1 · Install and practise (10 minutes, no lights involved)
**Mac**
1. Unzip the download. Open **Terminal** (Applications → Utilities).
2. Type `cd ` (with a space), drag the unzipped folder onto the Terminal window, press **Return**.
3. Type `./workshop/start.sh sim` and press **Return**.
   (Double-clicking `start-simulator.command` also works; if the Mac says it "can't be opened", right-click it →
   **Open** → **Open**.)

**Windows**
1. Unzip the download (right-click → **Extract All**).
2. Open the folder and double-click **start.bat**. To practise first, open a Command Prompt in the folder and type
   `start.bat sim`. If Windows shows "Windows protected your PC", choose **More info → Run anyway**.

**The first start** installs a small Python manager (`uv`) and the program's parts — a few minutes, once. Then it asks
for **the Hubspace email of the test module's account**, creates a password for the pages, and prints it:

```
Page sign-in:  user 'workshop'   password 'xxxxxxxxxxxx'
```

It also shows the addresses to open, for example:

```
On this laptop:            http://localhost:8000/ops/module/
On a phone (same Wi-Fi):   http://192.168.1.23:8000/ops/module/
```

Open one, sign in as **workshop** with that password. In practice mode everything says **SIMULATOR** — nothing you do
touches real lights, so try every button. Stop the program with **Ctrl+C** in the Terminal / Command Prompt window.

## 2 · Connect the real module (once, about 5 minutes)
1. Start the program **without** `sim`: Mac `./workshop/start.sh` (or double-click `start.command`); Windows
   double-click `start.bat`.
2. On the laptop open **http://localhost:8000/ops/accounts/** (sign in as *workshop*).
3. Under **Sign in to an account** choose **Bench kit account**, enter the module's Hubspace email and password, press
   **Sign in**. If Hubspace emails a code, the page asks for it — enter it there (don't stop the program in between).
   The password is not stored; only an encrypted sign-in token stays on this laptop.
4. The page lists the account's devices. On your module press **Use as Bench kit (Prototype 01)**.
5. Stop the program (**Ctrl+C**) and run the allow step: Mac `./workshop/start.sh allow`, Windows `start.bat allow`.
   It shows the module's id ending and asks **y** to confirm. This is a deliberate safety step: the program refuses to
   change any device you haven't allowed.
6. Start the program again (as in step 1).

## 3 · Unlock puck painting (about 10 minutes, once)
Painting individual pucks only switches on after a short check proves the Hubspace cloud accepts it for **your**
module. Open **http://localhost:8000/ops/bench/** and, with the module in view:
1. **Gate A — read-only profile dump.** Changes nothing. Press **Run**. It should say PASS (30 pucks, 3 saved
   patterns). Press **Copy anonymized JSON** and send it to Alon.
2. **Gate B — select a slot by API**, then **Gate C — replay slot 1 into slot 2**. Answer the questions on screen
   honestly (e.g. "Did the strand switch to slot 2 within about 2 s?"). When a gate passes, press **Apply flags**.
   If Gate C says slot 2 already matches slot 1, it tells you what to do: run **Hue ramp — rainbow for the index map**
   with **Options → slot** set to `custom-2` (a good wiring photo, too), then Gate C again.
4. Optional: open **http://localhost:8000/ops/bench/clock/** on a second screen next to the module and film both —
   the video tells us how fast the lights respond.

If Gate C fails, tell Alon — you can still test whole-module colours and saved patterns painted in the Hubspace app.

## 4 · Testing Prototype 01 — the module page
Open **/ops/module/** (laptop or phone). The page takes control of the module while it's open and lets go when you
press **Done**, close it, or leave it for 10 minutes. At the top it says who is using it.

- **The map** shows the 30 pucks in their real 8 / 7 / 8 / 7 layout, with the wire order. Tap or drag to select
  pucks. The centre of each dot is what Hubspace reports; the ring is what we sent.
- **Paint**: pick a colour (RGB or warm/cool white, colour and white brightness) → **Apply**. The page writes the
  pattern into a saved slot and shows which pucks match.
- **Module**: whole-module power, brightness, white temperature, colour, presets with speed, named programs — like the
  Hubspace app.
- **Tests** (one tap each), for the Prototype 01 list:
  - **Full white** at 2700 / 3000 / 4000 K
  - **Show colours**: Home Depot orange, amber, peach, warm ivory — whole strand, or painted
  - **Recipe compare**: left half RGB-only vs right half RGB + warm white, same target colour
  - **Thirds split**: three bands with their own colour and brightness — compare Mylar densities side by side
  - **Hue ramp**, **Single head**, and the **Index sweep** (lights one puck at a time along the wire; pause/resume;
    one step every 2 s — use it to check the wiring order)
- **Slots**: the three saved patterns, with a preview of each. If you paint a pattern in the Hubspace app, use
  **"Painted in the Hubspace app — record it"** so the show can use it.
- **Calibrate**: after the index sweep or a hue-ramp photo, set the start corner / cable path, or **Flip** the order,
  and **Save**. This records how your crew wired the module — please do it once the routing is final.
- **Hold**: freezes the current look and shows its name in big letters — for photos from below and at eye level.
- **Telemetry** (bottom): how long each change took, and the last error. **Copy anonymized JSON** for Alon.

## Good to know
- Leave the **Hubspace app** alone on this account while the module page is open, or the two will fight.
- The program only works until **Oct 19 2026** (a safety date for test setups).
- It runs only while the window is open. Closing it doesn't change the lights — they keep the last look.
- The laptop's firewall may ask to allow incoming connections the first time — allow it so the phone can connect.
- Everything the program saves is in the folder (`workshop.env`, `workshop-data/`). Keep the folder private; delete
  it when testing is over (that also removes the saved Hubspace sign-in).

## If something goes wrong
| What you see | What to do |
|---|---|
| The browser asks for a password | User **workshop**, password in `workshop.env` (open it with a text editor). |
| The phone can't open the page | Same Wi-Fi as the laptop? Use the address the program printed; allow the firewall prompt. |
| "isn't in the allowlist" / "can't be locked" | Do step 2.4–2.6 (Use as Bench kit → allow → restart). |
| Painting is greyed out | Gate C hasn't passed on this module yet (step 3). |
| "In use by …" | Someone else has the module page open. Wait, or press take over. |
| "Re-sign-in required" | Sign in again on /ops/accounts (step 2.3). |
| Lights don't change but the page says done | Check the module has power and Wi-Fi (the Hubspace app shows it online), then reload the page. |
| Anything else | Send Alon a screenshot and the Copy anonymized JSON text. |
