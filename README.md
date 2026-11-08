# پناه (PANAH) — A warning today, a protection tomorrow

> **Sent by email in parts:** extract every `PANAH-*.zip` into the same folder (for example `C:\`):
> together they make one `PANAH` folder.

*The official name of the system given by the group.*

PANAH (پناه, "shelter") warns people in Afghanistan about earthquakes and floods. It is a web application for
phones in Dari (also Pashto and English), written right to left, with a small server in Python:

- **Residents** sign in with a phone number and an SMS code, save their place, and get an alert when an event is
  **within 2 km** of it: a red banner, a map with the 2 km circle, the distance and the nearest safe places. They can
  send an **SOS** with their location and chat with the rescue team, watch safety videos and call emergency numbers.
- **The administrator** (rescue team) publishes alerts on a map, sees a dashboard, answers SOS chats and edits the
  emergency numbers and videos.
- **Earthquakes** in Afghanistan come by themselves from the free USGS feed, checked once a minute.
- **SMS are in test mode:** nothing is really sent. Login codes are shown on the screen, and every SMS the system
  would send is written to `data/sms.log`.

The server uses only Python's standard library and an SQLite database (one file): nothing to install except Python.

## What is in this folder
| Path | What |
|---|---|
| `run.bat` | double-click to start PANAH on Windows |
| `share-online.bat`, `HOSTING.md` | making PANAH reachable from the internet: `HOSTING.md` explains the ways, `share-online.bat` starts it |
| `app/` | the system: `main.py` (start), `server/` (Python server, one module per part), `web/` (the screens: HTML, CSS, JavaScript, dictionaries for Dari, Pashto and English), `tests/` (automatic tests), `README-admin.md` (demo administrator and SMS test mode) |
| `experiments/` | the 10 experiments: overview in `experiments/README.md`; each folder has its report (`README.md`), the script that repeats it, the raw data and the figures |
| `thesis/` | the thesis — `PANAH-Thesis.docx` (to edit) and `PANAH-Thesis.pdf` (to read), `figures/` (diagrams as SVG and PNG), `README.md` |
| `defense/` | `speech.md` (the talk), `demo-checklist.md` (what to show, in which order), `qa.md` (likely questions and answers), `explained/` — `PANAH-Thesis-Explained.docx` and `.pdf` (the whole thesis explained in plain English) |
| `screenshots/` | a picture of every screen — a backup if the live demo fails |
| `data/` | not in the package: created at the first start (database `panah.db`, logs `server.log` and `sms.log`, uploaded pictures `uploads/`) |

## What you need
- **Windows 10 or 11.**
- **Python 3.10 or newer** from <https://www.python.org/downloads/>. In the installer tick **"Add python.exe to
  PATH"**. Nothing else needs to be installed.
- **Google Chrome or Microsoft Edge** (PANAH opens in the default browser).
- **Internet** is needed only for the map pictures, the USGS earthquake feed, place names and address search
  (OpenStreetMap) and the videos (YouTube). Without internet everything else works: sign-in with the test code,
  publishing alerts, live delivery to phones, SOS chat, the dashboard, emergency numbers.

## Start
1. **Extract the zip first:** right-click it → *Extract All…* → choose a short destination, for example `C:\` (you get
   `C:\PANAH`), not a OneDrive-synced folder → *Extract*. Starting `run.bat` from inside the zip does not work.
   Got **`PANAH-1-app-gmail.zip`** (the copy that passes Gmail)? In it `run.bat` and the `.js` files end with `.txt`:
   after extracting, double-click **`restore_files.py`** in the `PANAH` folder once (see `READ ME FIRST.txt`).
2. Open the `PANAH` folder and double-click **`run.bat`**.
3. A black window shows the addresses, and the browser opens by itself:
   ```
   PANAH is running: http://localhost:8000/
   Phone in the same Wi-Fi: http://192.168.1.23:8000/
   Data: C:\PANAH\data
   Stop: Ctrl+C
   ```
   On this laptop use **http://localhost:8000/**. If port 8000 is busy, the next free one is taken (8001, 8002, …):
   always use the address shown in the window.
4. **Keep the black window open** — it is the server.

**Stop:** press **Ctrl+C** in the black window (if it asks *"Terminate batch job (Y/N)?"*, type `Y`), or simply close
the window. The data stays in `data/` for the next start.

Options (open a Command Prompt in the folder — type `cmd` in the Explorer address bar — and add them after `run.bat`):

| Command | Does |
|---|---|
| `run.bat --port 8080` | start on another port |
| `run.bat --no-usgs` | do not load earthquakes from USGS (a quiet demo) |
| `run.bat --no-browser` | do not open the browser |
| `run.bat --db C:\demo\panah.db` | use another database; its folder also gets the logs |

## Phones in the same Wi-Fi
1. Connect the phone to the **same Wi-Fi** as the laptop.
2. On the phone open the address from the black window, with `http://` at the start:
   `http://<laptop IP>:<port>/`, for example `http://192.168.1.23:8000/`.
   To find the laptop IP yourself: open Command Prompt, type `ipconfig`, and take the **IPv4 Address** under
   *Wireless LAN adapter Wi-Fi*.
3. **Windows Firewall:** at the first start Windows asks whether Python may use the network. Tick **Private
   networks** and click **Allow access**. If the phone still cannot connect: Windows Security → *Firewall & network
   protection* → *Allow an app through firewall* → *Change settings* → tick *Private* for Python; and check that the
   Wi-Fi network profile is **Private** (Settings → *Network & internet* → *Wi-Fi* → your network).
4. **GPS on the phone does not work over `http://`** — browsers give the location only to secure (https) pages
   and to `localhost`. On the phone choose the place on the map («موقعیت خود را روی نقشه انتخاب کنید» — *Choose your
   location on the map*). On the laptop (`http://localhost:…`) "use current location" works if Windows location is on.

Some campus and guest networks do not let devices see each other. Then use a phone hotspot (connect the laptop to
it), or the laptop's *Mobile hotspot* (Settings → *Network & internet*): connect the phone to it and use
`http://192.168.137.1:<port>/` (check with `ipconfig`).

## Demo: sign-in and the administrator
The full order of the demo is in `defense/demo-checklist.md`. The essentials:

**Resident** (phone, or the laptop):
- On the sign-in screen tap «حساب جدید بسازید» (*Create a new account*) or enter a number.
- The number has **10 digits** and starts with `07`, for example `0700 000 009`. Any made-up number works: no real SMS
  is sent.
- **Test mode:** the 6-digit code is shown on the code screen in a box «حالت آزمایشی — کد: …» (*Test mode — code*).
  A code is valid for 5 minutes; a new code only after 45 seconds and at most 5 per hour for one number; 5 wrong
  tries burn the code. All SMS (login codes, alert SMS, SOS) are also written to **`data/sms.log`** (UTF-8, open it
  with Notepad).
- Without an account: on the *Create a new account* screen «رد کردن» (*Skip*) opens the home screen as a guest; on
  the sign-in screen «دسترسی اضطراری بدون ورود به سیستم» (*Emergency access without signing in*) starts an SOS
  (3 seconds to cancel).
- The language (Dari, Pashto, English) is switched on the sign-in screen, in the profile and on the admin dashboard.

**Administrator:**
- Open **http://localhost:8000/#/admin** (from a phone: `http://<laptop IP>:<port>/#/admin`).
- Username **`admin`**, password **`panah1234`**. This demo account is created at the first start when the database
  has no administrator; it is public, so change the password before any real use (`app/README-admin.md` shows how).
- «ورود با رمز یکبار مصرف» (*Sign in with a one-time code*): this code is **not** shown on the screen — it is "sent"
  by SMS to the administrator's phone, so in test mode it is only in **`data/sms.log`** (the newest `otp` line).
- 5 wrong passwords lock the password sign-in for 5 minutes; signing in with a one-time code removes the lock.
- One browser keeps one sign-in. For a resident and the administrator at the same time, use two browsers, or a
  private (incognito) window for the second one.

With internet, the first start loads the earthquakes of the last 30 days in Afghanistan from USGS as past alerts; an
earthquake of magnitude 4.0 or more in the last 24 hours becomes an active alert.

## Start again with empty data
Stop the server, then delete the `data` folder (or rename it, for example to `data-old`, to keep it). The next start
creates a new database with the demo administrator, the emergency numbers, the videos and the sample safe places
(and, with internet, the USGS earthquakes of the last 30 days).

## Automatic tests
In a Command Prompt in the `PANAH` folder:
```
python -m unittest discover app/tests
```
(or `py -m unittest discover app/tests`); it takes a few minutes. The tests do not use the internet: they use
recorded answers of the outside services (`app/tests/data/`). Keep the computer connected to some network (Wi-Fi or
cable) while they run: the browser tests that switch the connection off and on fail when there is no network at all.
The screen tests start **Google Chrome** without a window and are skipped if Chrome is not installed; a few format
checks need Node.js and are skipped without it. The screen tests save their screenshots and temporary browser
profiles in a `stand` folder inside `PANAH` — it can be deleted.

## Common problems
| Problem | What to do |
|---|---|
| "Python 3.10 or newer was not found" | Install Python from python.org with **"Add python.exe to PATH"** ticked, then double-click `run.bat` again. If typing `python` opens the Microsoft Store, install from python.org anyway (`run.bat` also uses the `py` launcher it brings). |
| The window opens and closes at once, or `app\main.py` is not found | The zip was not extracted, or `run.bat` was moved away from the `app` folder: extract the whole zip and start `run.bat` inside `PANAH`. |
| Windows warns about the file ("Windows protected your PC" or "Open File – Security Warning") | Click *More info* → *Run anyway*, or *Run*. To avoid it: before extracting, right-click the zip → *Properties* → tick *Unblock* → *OK*. |
| Port 8000 is busy | Nothing to do: the next free port is taken. Use the address shown in the black window (or start with `run.bat --port 8080`). |
| The map is grey | No internet: the map pictures come from OpenStreetMap. Alerts, the 2 km circle, distances and safe places still work; tap the grey map to choose a place. |
| No new earthquakes, place names say «نزدیک: …» (*Near: …*), address search finds nothing, videos do not play | Also no internet (USGS, OpenStreetMap Nominatim, YouTube). The window logs it and retries. |
| The phone cannot open the page | Same Wi-Fi? Address typed exactly, with `http://`? Python allowed in the firewall for private networks? Otherwise use a hotspot (see above). |
| Strange characters (`???` or boxes) instead of Dari, Pashto or other non-Latin text in the black window | Only the console font; the application is not affected. `run.bat` switches the console to UTF-8; the logs `data/server.log` and `data/sms.log` are UTF-8 — open them with Notepad. If the folder path has non-Latin letters and the start fails, move the `PANAH` folder to `C:\PANAH`. |
| "Wait before a new code" | Test mode allows a new code after 45 seconds and 5 codes per hour for one number. Use the code already shown, or another made-up number. |

## Third-party parts and data
| Part | Licence | Where |
|---|---|---|
| Leaflet 1.9.4 (map library) | BSD 2-Clause License, © 2010–2023 Volodymyr Agafonkin, © 2010–2011 CloudMade | `app/web/vendor/leaflet/LICENSE` |
| Vazirmatn font | SIL Open Font License 1.1, © 2015 The Vazirmatn Project Authors | `app/web/vendor/vazirmatn/OFL.txt`, `AUTHORS.txt` (a copy for the experiment sheets in `experiments/09-usability-sus/sheets/fonts/`) |
| Map tiles, place names and address search (Nominatim), coordinates of the sample safe places | © OpenStreetMap contributors; the data is available under the Open Database License (ODbL), <https://www.openstreetmap.org/copyright> | loaded from openstreetmap.org while the app runs; the free tile and Nominatim services have usage policies — fine for a demo, not for heavy real use |
| Earthquake data | USGS Earthquake Hazards Program feeds (earthquake.usgs.gov). USGS policy: "USGS-authored or produced data and information are considered to be in the U.S. Public Domain"; credit: U.S. Geological Survey | loaded while the app runs |
| Safety videos | YouTube videos of their publishers, only linked — not part of this package | list and sources: `app/server/content_sources.md` |
| Test samples | recorded USGS and Nominatim answers (same terms as above) | `app/tests/data/` |

This package has not yet been tried on Windows itself: do one full rehearsal on the laptop that will be used at the
defense.
