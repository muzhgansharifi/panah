# PANAH (پناه) admin: login, SMS, password
> Up: [README](../README.md) · code — `server/auth.py` (login), `server/sms.py` (SMS)

## Demo admin
| username | password | phone (for login by code) |
|---|---|---|
| `admin` | `panah1234` | `+93700000000` (made up) |

This is the project's demo data, not a secret: the `DEMO_ADMIN_*` constants in `server/auth.py`. The demo admin is
created at the first start only if the database has no admins at all; a restart does not repeat it and does not reset
a changed password.

## How the admin logs in
- **With a password** (screen 14): `POST /api/admin/login {username, password}` → `{admin}` and a session cookie. The
  username is case-insensitive; Dari digits in the password equal digits 0–9. Wrong → 400 `wrong_login` with
  `attempts_left`; 5 wrong in a row → 429 `locked` for 5 minutes (`retry_after` in seconds), even with the right
  password. Failures are counted per username, even for one that does not exist; a successful login resets the count.
- **With a one-time code** ("ورود با رمز یکبار مصرف"): `POST /api/admin/otp {username}` → `{phone (masked),
  expires_in, resend_in}` — the code goes only by SMS to the admin's phone; then `POST /api/admin/otp/verify
  {username, code}` → `{admin}`. There is no `test_code` here, not even in test mode: the code is only in
  `data/sms.log` and `sms_outbox` — otherwise a user name alone would open the admin panel (experiment 05). Code
  rules are the same as for residents (below). Logging in with a code lifts the password lock.
- **Logout** — `POST /api/auth/logout`, as for residents. `GET /api/me` for an admin → `{user: null, admin: {...}}`,
  for a guest → 200 `{user: null, admin: null}`.
- Admin URLs without an admin login (guest or resident) → 403 `admin_required`.

## Resident login: code rules
The code is 6 digits (`secrets`) and lives 5 minutes; a new one no sooner than 45 s later (429 `too_soon`) and at most
5 per hour per number (429 `too_many`), both with `retry_after`. Wrong → 400 `wrong_code` with `attempts_left`; after
5 mistakes the code is burned; expired, burned or already used → 410 `expired`. A session lasts 30 days.

## Where to see the SMS (test mode)
No real SMS goes out: each "send" is a row in the `sms_outbox` table (`status = test`) and a line in the log.
- a resident's login code is in the `POST /api/auth/code` response → `test_code` (screen 04 shows it); an admin's
  one-time code never is — only in the log below;
- the log `data/sms.log` (UTF-8): one line per SMS — UTC time, `#id`, kind (`otp`, `alert`, `sos`, `other`), status,
  number, text (text lines joined with " | ");
- for the admin: `GET /api/admin/sms?limit=50&kind=otp` → the latest SMS (newest first) and counters: total, today
  (since midnight Kabul time) and by kind; `sms.stats(db)` gives the same counters for the panel.

## Change the password or add an admin
Stop the server; in the `app/` folder (on Windows use `py` instead of `python3`):
```
python3 -c "from server import store, auth; db = store.connect('../data/panah.db'); auth.set_admin_password(db, 'admin', 'NEW-PASSWORD'); db.commit()"
```
```
python3 -c "from server import store, auth; db = store.connect('../data/panah.db'); auth.init(db); auth.add_admin(db, 'rescue1', 'PASSWORD', '0799123456', 'Rescue team'); db.commit()"
```

## How it is protected (for the thesis)
- Password: PBKDF2-HMAC-SHA256, 200 000 iterations, its own random 16-byte salt; only the hash is in the database.
- Login code: only `sha256(salt:code)` is in the database; the session token is `secrets.token_urlsafe(32)`, kept in an
  `HttpOnly` cookie, and only its SHA-256 is in the database.
- The limit check and writing the code happen under one database write lock: a double tap on "send" does not issue
  two codes.

## Real SMS: "future work"
They are plugged in with one call: `sms.use_provider(provider)` (a provider has a method `send(phone, text)`).
Example: `TwilioProvider` in `server/sms.py`, for now a stub (`NotImplementedError`) with notes on how to connect
Twilio. With a real provider, `test_code` in the response is `null` and the code arrives only by SMS. If the
provider fails, nothing counts as sent (no `sms_outbox` row, a `failed` line in `data/sms.log`): a login code
request answers 503 `sms_failed` and may be repeated at once; an alert SMS is retried by the alerts queue after
2, 10 and 60 s and then marked `failed`.
