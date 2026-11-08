# PANAH on phones — two ways to host it

Phones need an **https** address: browsers give the phone's location (GPS) only to secure pages (`https://…`) or to
the computer itself (`localhost`). `run.bat` alone serves plain `http://` in your Wi-Fi, so phones there get no GPS
(they can still pick their place on the map). Choose one of the two ways below.

| | Option 1: `share-online.bat` | Option 2: Render |
|---|---|---|
| Cost | free | free |
| Accounts | none | GitHub and Render |
| Works | while the laptop is on and the window is open | always; sleeps after 15 min without visitors, wakes in about 1 min |
| Address | new at every start (`https://….trycloudflare.com`) | fixed (`https://<name>.onrender.com`) |
| Data | stays on the laptop (`data/`) | starts empty again after every sleep, restart or new upload |

**What was tested.** The server side (port from `PORT`, `--host`, the tunnel start-up and link printing with a
stand-in program, live updates in short-stream mode) is covered by the automated tests. **Not tested:** a real
Cloudflare tunnel and a real Render deployment — both publish the app on the internet, so do one test run yourself
before the defense (checklist at the end).

---

## Option 1 — a secure link from your laptop (`share-online.bat`)

Free, no account. Uses a Cloudflare *quick tunnel*: Cloudflare gives a random `https://` address and passes its
visitors to the PANAH server on your laptop.

1. **Python 3.10 or newer** must be installed (the same as for `run.bat`): <https://www.python.org/downloads/> — in
   the installer tick **"Add python.exe to PATH"**.
2. **Download cloudflared once** — the official Cloudflare program, from Cloudflare's release page on GitHub:
   <https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe>
   (all versions: <https://github.com/cloudflare/cloudflared/releases/latest>; for 32-bit Windows take
   `cloudflared-windows-386.exe`).
3. **Rename** the downloaded file to `cloudflared.exe` and **put it in the PANAH folder**, next to
   `share-online.bat`. (If Windows hides extensions, the name may show as just `cloudflared` — that is fine.)
4. **Recommended — your own admin password.** The demo admin password is printed in the project's README, so anyone
   with the link could open the admin panel. To use your own, open the PANAH folder, type `cmd` in the address bar of
   File Explorer, press Enter, and run:
   ```
   set ADMIN_PASSWORD=Choose-A-Strong-Password
   share-online.bat
   ```
   The new password is saved in `data/panah.db` and keeps working on later starts. (Otherwise just double-click
   `share-online.bat`.)
5. **Wait 10–30 seconds** for the box **"Secure link for phones"** with an address like
   `https://some-random-words.trycloudflare.com`. Open it on any phone — Wi-Fi or mobile data — and allow location
   when the browser asks. The laptop's own browser opens `http://127.0.0.1:8000/` by itself.
6. **Keep the window open.** Closing it (or Ctrl+C) ends the link. Next time the address is different.

Good to know (Cloudflare's own notes on quick tunnels:
<https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/>,
read 7 Nov 2026):
- no uptime guarantee; at most 200 requests in flight at once (more get an error 429) — fine for a class demo;
- quick tunnels **do not support Server-Sent Events**, which PANAH uses for live alerts and chat replies. Through a
  quick tunnel PANAH therefore ends each live response after its events and the browser reconnects within about a
  second (nothing is lost; screens also re-read their data on every reconnect). **Not tested through a real
  tunnel** — check it with two phones before the defense (checklist below).
- The data (accounts, alerts, SOS chats) stays on the laptop in `data/panah.db`.
- If nothing appears after 30 seconds: is the internet on? Details are in `data/tunnel.log`.

---

## Option 2 — a permanent free address on Render

Render (<https://render.com>) runs the server in the cloud. You need a GitHub account (to hold the code) and a
Render account (both free).

### A. Put the code on GitHub
1. Create an account: <https://github.com/signup>.
2. Top right **+** → **New repository** → name `panah` → **Public** → **Create repository**.
3. On the new page click **"uploading an existing file"** and drag in, from the PANAH folder: the **`app`** folder,
   `run.bat`, `share-online.bat`, `HOSTING.md` and `README.md`. Do **not** upload `data/` or `cloudflared.exe`.
   (The web page takes up to 100 files at once; PANAH's `app` folder has about 85.) Click **Commit changes**.

### B. Create the web service on Render
1. Sign up at <https://dashboard.render.com/register> (signing up with GitHub is easiest).
2. **New** → **Web Service** → choose your `panah` repository (or **Public Git Repository** and paste its
   `https://github.com/<you>/panah` address).
3. Fill in:

   | Field | Value |
   |---|---|
   | Name | `panah` (the address becomes `https://panah.onrender.com`, or with a suffix if taken) |
   | Language | Python 3 |
   | Branch | `main` |
   | Build Command | `python --version` (PANAH needs no packages) |
   | Start Command | `python app/main.py --no-browser` |
   | Instance Type | **Free** |

4. **Environment Variables** → **Add**: `ADMIN_PASSWORD` = your own strong password. This is a must on a public
   address: without it the demo password from the README opens the admin panel. Do **not** add `PORT` — Render sets
   it and PANAH listens on it.
5. Optional: **Health Check Path** `/api/health`.
6. **Create Web Service**, wait until it shows **Live** (a few minutes), open `https://<name>.onrender.com`.

To update the app later: upload the changed files to GitHub again — Render deploys them by itself.

Honest limits of the free plan (Render's docs: <https://render.com/docs/free>, read 7 Nov 2026):
- The server **sleeps after 15 minutes without visitors**; the next visit waits about a minute while it wakes up.
- The disk is temporary: "local SQLite databases, uploaded images … are lost every time the service redeploys,
  restarts, or spins down". So after **every sleep, restart or new upload PANAH starts with a fresh database**: the
  admin (with your `ADMIN_PASSWORD`), the emergency numbers, videos and safe places are there again, and the 114
  historical earthquakes (from the USGS catalogue copy in the package) and the last 30 days of the live USGS feed are
  loaded again — but residents' accounts, alerts made by the admin, SOS chats and
  photos are gone. For the defense: open the site about 2 minutes before you start and do the whole demo in one go.
- 750 free hours per month — enough for one service running all month.

---

## Before the defense — 5-minute check
1. Open the link on two phones; allow location; sign up on one (the SMS code is shown on the screen — test mode).
2. On the laptop or a third device open `/#/admin`, log in, publish a flood alert near the phones.
3. Both phones should show the alert within a few seconds without reloading; write an SOS message on one and reply
   from the admin chat.
4. If live updates do not arrive through the quick tunnel, reload the page as a fallback — or use Option 2.

Which to choose: Option 2 for a fixed address on the slides; Option 1 as the backup that needs no accounts.
