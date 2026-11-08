# app/web — PANAH (پناه) screens (the ui-core engine)
> Up: [README](../../README.md) · the screens as built — [screenshots/](../../screenshots/README.md)

The base of all screens: the Figma look, right to left, a 360–440 px column, `#/…` navigation, API, live events, map,
strings in three languages, the Solar Hijri calendar and Dari digits. No npm and no build: ES2020, a single global `App`.

## Files
| What | Where |
|---|---|
| App page (loads everything) | `index.html` |
| Figma colors, font, column, utilities | `css/base.css` |
| Components | `css/components.css`, `js/ui.js` (`App.ui.*`, `App.icon`) |
| The `App` core | `js/core.js` |
| Strings: Dari (main), Pashto, English | common — `i18n/fa.json`, `i18n/ps.json`, `i18n/en.json`; per group — `i18n/<lang>/<group>.json` |
| Group screens | `js/screens/<group>.js` + `css/<group>.css`: `auth` (02, 03, 04, 01), `home` (05/06, alert, 09, the resident's videos and numbers), `sos` (10–13), `admin` (14–18, dashboard, events, rescuer chat) |
| Demo of all components | `dev/components.html` |
| Vazirmatn font (OFL), Leaflet 1.9.4 (BSD-2) | `vendor/` — with licenses |

Each group's own files are already linked in `index.html` (empty at first), so there is no need to touch `index.html`.
A new group is one `<link>` line and one `<script>` line there.

## How to write a screen
```js
// js/screens/home.js
App.screen('alert', {
  route: '#/alert/:id',          // path parameters → params.id; ?q=… → params.q
  nav: 'resident',               // bottom menu: 'resident' | 'admin' | null
  tab: 'home',                   // which tab is highlighted (otherwise — by the address)
  auth: null,                    // 'user' | 'admin' — without signing in, sends to sign-in (?next=address)
  async render(params, el) {
    const a = await App.api('GET', `/api/alerts/${params.id}`);   // error → a screen with "try again"
    el.innerHTML = App.html`
      ${App.ui.header({ title: App.t('home.alertFlood') })}
      ${App.ui.banner({ kind: 'alert', title: a.title, text: App.t('home.distance', { d: App.fmt.distance(a.distance_km) }) })}
      <div class="map" id="m"></div>
      ${App.ui.button({ text: App.t('common.goHome'), go: '#/home', kind: 'outline' })}`;
    App.map(el.querySelector('#m'), { center: [a.lat, a.lon], markers: [{ lat: a.lat, lon: a.lon, color: 'red' }],
      circle: { lat: a.lat, lon: a.lon, radius: 2000 } });
    App.live.on('alert_end', (d) => { if (d.id == params.id) App.go('#/home'); }); // unsubscribed automatically on leaving
    const timer = setInterval(() => {}, 30000);
    return () => clearInterval(timer);                            // cleanup when leaving the screen
  },
});
```
Rules: interface strings — only `App.t('key')`; HTML — via `App.html\`…\`` (every interpolated value is escaped,
except `App.ui.*`, `App.icon`, `App.html`); numbers, dates, distances, phones — via `App.fmt` / `App.phone` (Dari
digits); in CSS — logical properties only (`margin-inline-start`, `inset-inline-end`, `text-align: start`), then
English (`dir="ltr"`) flips the layout by itself. **New strings go to `i18n/<lang>/<group>.json`** (fa, ps and en —
with the same keys; a group key overrides the common one from `i18n/<lang>.json`, the common files are not edited); an
empty string in `ps`/`en` → Dari is shown. The Figma texts of all 18 screens are already in the common dictionaries (`auth.*`, `home.*`, `sos.*`, `admin.*`). Buttons and links without JS:
`data-go="#/route"`, `data-back` (or `data-back="#/fallback"`), `data-lang="ps"`.

## App — functions
| | |
|---|---|
| `App.screen(name, {route, render, nav, tab, auth, title})` | a screen; `render` can be `async` and return a cleanup function |
| `App.go(route)` · `App.replace(route)` · `App.back(fallback)` · `App.refresh()` · `App.current()` · `App.onLeave(fn)` | navigation |
| `App.config` | `home '#/home'`, `login '#/login'`, `adminHome '#/admin'`, `adminLogin '#/admin/login'`, `me '/api/me'`, `stream '/api/stream'` |
| `App.navs.resident` / `App.navs.admin` | menu tabs: `#/home`, `#/videos`, `#/numbers` (زنگ), `#/sos` (مسج) · `#/admin`, `#/admin/events`, `#/admin/numbers`, `#/admin/videos`, `#/admin/messages`. To change a route — do it from your own file. `App.setBadge('admin', 'messages', 3)` — the number on a tab |
| `App.api(method, path, body)` | → JSON; error — `ApiError {status, code, message}` (`status 0` — no network); the server answers `{"error": code, "message": text in Dari}` |
| `App.upload(file)` | image → `{url, type, size}` (`POST /api/upload {data_url, name}`) |
| `App.live.on(event, cb, {global})` | SSE `/api/stream` with reconnection; events `alert`, `alert_end`, `sos_case`, `sos_message`, `sos_location`; returns an unsubscribe function |
| `App.live.on('reconnect', reload)` | core event: the stream reopened after a drop (server restarted, network back, page restored from the back/forward cache) — **re-read the screen's data**. Last-Event-ID is kept for pages without events too (`?last_event_id=`): after a server restart missed events are re-sent. On `pagehide` the stream is closed, on `pageshow` it is opened again. `App.live.status`, `App.live.kick()` |
| `App.online` · `App.on('online'/'offline', cb)` | whether there is a network (per the browser); offline — a thin "بدون اتصال به اینترنت" strip at the top (`net.offline`, three languages); when the network is back, the stream reopens at once |
| `App.state` · `App.setUser(u)` · `App.setAdmin(a)` · `App.refreshMe()` | `{user, admin, location}`; on start `GET /api/me` → `{user, admin}` (or the user itself; 401 — a guest) |
| `App.location.get()` · `.pick()` · `.saved()` · `.save(loc)` · `.gps()` | browser GPS → else the saved one → else a pick on the map; `{lat, lon, source}` or `null` |
| `App.map(el, {center, zoom, markers, circle, onPick, fit})` | Leaflet + OpenStreetMap tiles ("© OpenStreetMap contributors"); `.setMarkers() .setCircle() .setView() .fit()` |
| `App.t(key, vars)` · `App.setLang('fa'|'ps'|'en')` · `App.lang` · `App.dir()` | strings; numbers in `vars` — in the language's digits; group dictionaries — from `App.config.groups` |
| `App.fmt` | `num(12)`→۱۲ · `num(34.555, 4)`→۳۴٫۵۵۵۰ · `digits` · `latin` · `date`→۱۱ جوزا ۱۴۰۳ · `dateNum`→۱۴۰۵/۰۲/۱۱ · `time`→۱۰:۳۳ (Kabul) · `dateTime` · `stamp`→۱۴۰۵/۰۲/۱۱ - ۱۰:۳۳ - قبل از ظهر · `ago`→۵ دقیقه پیش · `distance(12)`→۱۲ کیلومتر · `percent(67)`→۶۷٪ · `coord`→۳۴٫۵۵۵۰°N, ۶۹٫۲۰۷۰°E · `solar(d)`→`{y,m,d,H,M}` |
| `App.phone` | `normalize('۰۷۷۷ ۱۲۳ ۴۵۶')`→`+93777123456` (or `null`) · `display`→۰۷۷۷ ۱۲۳ ۴۵۶ · `tel`→`tel:+93…` · `local` · `isValid` |
| `App.toast(text, 'ok'|'error'|'info')` · `App.device.get()` | toast · `{battery, charging, signal: strong/medium/weak/none, online}` — `null` if the browser does not provide it |
| `App.html` · `App.esc` · `App.raw` · `App.formValues(form)` · `App.store` · `App.on/emit('lang'|'user'|'route'|'live')` | helpers |

## Components (`App.ui.*`, classes — in `css/components.css`)
`header({title, back, shield})` header 01/04 · `pageTitle({title, sub})` title 15–18 · `logo({kind: sos|mark|admin|mini|full})` (PANAH: shield with a shelter; `full` — with «پناه», PANAH and the motto; files `img/logo.svg`, favicon `img/icon.svg`) ·
`button({text, kind: primary|purple|outline|outline-accent|ghost|sos|danger, icon, sub, edge, small, go, href})` ·
`iconButton` · `addTile` "+ افزودن" 16/17 · `or()` "یا" · `card(html, {kind: light|pink|blue|green|muted})` · `skeleton(n)` 05 ·
`section({title, icon, optional, required, content})` section card 01 · `field({label, required, optional, icon, inputIcon,
end, options, rows, lg, loud, error})` · `fieldError(input, text)` · `tiles({name, items, cols, wide, check})` blood group and
health 01 · `badge(text, red|sos|gray|green|orange|purple)` · `banner({kind: ok|alert|danger, title, text, go})` 05/06 ·
`sectionTitle({title, badge, toggle})` 05 · `row({icon, title, sub, end, go})` 09 · `phone(p)` · `empty({icon, title, text,
action})` · `loading()` · `msg({side: out|in, name, text, status, time, checks, footer})` 11/13 · `msgLocation({lat, lon})` 13 ·
`chatbar({dock})` + `bindChatbar(el, onSend, onAttach)` 13 · `langSwitch()` 02 · `tabbar(kind, active)` · `sheet(html, {title,
onClose})`. Classes: `.theme-admin` (orange #FF6B00), `.screen--gray`, `.stack`, `.cluster`, `.spread`, `.muted`, `.ltr`,
`.input--lg`, `.map--tall`. Icons: `App.icon(name)`; the list — `App.icons` and the bottom of the demo page.

## Checking
```
python -m unittest app.tests.test_ui_core
python -m http.server 8090 -d app/web      → http://localhost:8090/dev/components.html
```
Decisions: menus run right to left (home is on the right; on the left in Figma — an inconsistency in the mockup);
"back" in the header is on the left, as in 01/04 (in English too); a number is shown as
"۰۷۷۷ ۱۲۳ ۴۵۶" — the Figma example has 11 digits, an Afghan mobile number has 10.
