/* Group "home" — resident screens: home 05/06, alert with map, search 09, videos (like 17) and numbers (07 + 08 + 16).
   Routes: #/home, #/alert/:id, #/search, #/videos, #/video/:id, #/numbers. Built on the core's App.*
   (app/web/README.md); strings — i18n/<lang>/home.json and common dictionaries; own screen classes — hm-* (css/home.css).
   Data comes only from the server: GET /api/alerts, /api/alerts/{id}, /api/search, /api/videos, /api/numbers;
   the signed-in resident's location — POST /api/me/location at most once every 5 minutes.
   Poor network: live events (alert / alert_end) are laid over the last loaded data, the answer to an older request
   is dropped (by request number) — an ended alert does not pop back up; the stream reconnected or the network is back —
   we re-read. Offline: the latest alerts, numbers and videos come from localStorage, marked "آخرین به‌روزرسانی". */
(function (App) {
  'use strict';
  const h = App.html;
  const t = (k, v) => App.t(k, v);

  const LOC_EVERY = 5 * 60 * 1000;  // the signed-in resident's location goes to the server at most once every 5 minutes
  const PAST_FIRST = 4;             // 4 past alerts shown at once (like the 4 cards in 05/06), the rest behind the arrow
  const YT_THUMB = 'https://img.youtube.com/vi/';
  const YT_EMBED = 'https://www.youtube-nocookie.com/embed/';
  const OSM = 'https://www.openstreetmap.org/';
  const DOT = { red: '#E5372B', blue: '#1E6FD9', green: '#22A447' }; // like the App.map pins

  // ---------------------------------------------------------------- helpers
  const num = (x) => (x == null || x === '' || !Number.isFinite(Number(x)) ? null : Number(x));
  /** "۱۲ کیلو متر", "۱٫۵ کیلو متر", "۳۵۰ متر" — as in 06 ("به دوری ۱۲ کیلو متر") */
  function dist(km) {
    const x = num(km);
    if (x == null) return '';
    if (x < 1) return t('home.m', { n: Math.max(10, Math.round(x * 100) * 10) });
    return t('home.km', { n: x < 10 ? Math.round(x * 10) / 10 : Math.round(x) });
  }
  const away = (km) => (num(km) == null ? '' : t('home.distance', { d: dist(km) }));
  const isFlood = (a) => a && a.type === 'flood';
  const alertTitle = (a) => t(isFlood(a) ? 'home.alertFlood' : 'home.alertQuake');
  const typeName = (a) => t(isFlood(a) ? 'alert.type.flood' : 'alert.type.earthquake');
  const typeIcon = (a) => (isFlood(a) ? 'flood' : 'quake');
  /** a name in the interface language: Pashto if present, otherwise Dari (the server stores no English names) */
  const byLang = (fa, ps) => (App.lang === 'ps' && ps ? ps : fa) || '';
  const pt = (lat, lon) => (num(lat) == null || num(lon) == null ? null : { lat: Number(lat), lon: Number(lon) });
  const savedPoint = () => { const s = App.location.saved(); return s ? pt(s.lat, s.lon) : null; };
  // full precision: the server decides "inside" for exactly the point the client used (no rounding of the query)
  const qs = (p) => (p ? `lat=${encodeURIComponent(String(p.lat))}&lon=${encodeURIComponent(String(p.lon))}` : '');
  const moved = (a, b) => (!a || !b ? !!a !== !!b : Math.abs(a.lat - b.lat) > 0.0003 || Math.abs(a.lon - b.lon) > 0.0003);
  const ll = (p) => `${Number(p.lat).toFixed(5)},${Number(p.lon).toFixed(5)}`;
  /** Walking route on openstreetmap.org (new tab): from the resident (if the location is known) to the point */
  const routeUrl = (from, to) => `${OSM}directions?engine=fossgis_osrm_foot&route=${encodeURIComponent((from ? ll(from) : '') + ';' + ll(to))}`;
  const placeUrl = (p) => `${OSM}?mlat=${Number(p.lat).toFixed(5)}&mlon=${Number(p.lon).toFixed(5)}#map=17/${Number(p.lat).toFixed(5)}/${Number(p.lon).toFixed(5)}`;
  const ext = (href, inner, cls) => h`<a class="${cls}" href="${href}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
  const routeLink = (from, to) => ext(routeUrl(from, to), h`${App.icon('map', { size: 18 })}<span>${t('home.alert.route')}</span>`, 'hm-route');

  // Geofence rule — the same as the server's (geo.distance_km + alerts.EDGE_KM): haversine on a sphere of
  // R = 6371.0088 km, inside = unrounded distance <= radius_km + 1 mm. The distance is rounded only for display
  // (dist()); rounding it before the comparison made 2 000.0–2 000.5 m count as inside although the server did not
  // notify those residents (experiment 02).
  const EARTH_RADIUS_KM = 6371.0088;
  const EDGE_KM = 1e-6;
  const DEFAULT_RADIUS_KM = 2;
  /** Great-circle distance in km between {lat, lon} points (haversine, like the server's geo.distance_km). */
  function km(a, b) {
    const r = Math.PI / 180;
    const h1 = Math.sin(((b.lat - a.lat) * r) / 2) ** 2
      + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(((b.lon - a.lon) * r) / 2) ** 2;
    return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h1)));
  }
  /** Alert (e.g. from a live event) → with distance_km and inside for the resident at `me`. What the server sent
      wins: its inside and distance_km are kept; only what is missing is computed here, by the server's rule. */
  function withDistance(x, me) {
    const a = Object.assign({ status: 'active' }, x);
    const p = pt(a.lat, a.lon);
    const d = me && p ? km(me, p) : null;          // unrounded
    if (num(a.distance_km) == null) a.distance_km = d;
    if (typeof a.inside !== 'boolean') {
      const r = num(a.radius_km) != null ? num(a.radius_km) : DEFAULT_RADIUS_KM;
      a.inside = d != null && d <= r + EDGE_KM;
    }
    return a;
  }
  // for tests and experiments (app/tests/test_screens_home.py): the client's geofence rule
  App.homeGeo = Object.freeze({ km, withDistance, EARTH_RADIUS_KM, EDGE_KM });
  const byNear = (list) => list.sort((x, y) => (x.distance_km == null) - (y.distance_km == null)
    || (x.distance_km || 0) - (y.distance_km || 0) || String(y.starts_at).localeCompare(String(x.starts_at)));

  // ---------------------------------------------------------------- offline and on a poor network
  const CACHE = 'home.cache.';      // App.store: alerts, numbers, videos, alert (details by id, the last 10)
  const SLOW_MS = 1200;             // a slower answer — show the saved copy; the fresh one replaces it by itself
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  /** is there a network — the core's App.online (navigator.onLine and the 'online' / 'offline' events) */
  const isOnline = () => App.online !== false;
  /** a network error or no answer from the server (ApiError status 0), not a refusal by the server */
  const isNetErr = (e) => !!e && e.status === 0;
  const readCache = (key) => { const c = App.store.get(CACHE + key); return c && c.data ? c : null; };
  const writeCache = (key, data, at) => App.store.set(CACHE + key, { at: at || new Date().toISOString(), data });
  /** "آخرین به‌روزرسانی: …" — the saved copy is shown, not fresh data from the server */
  const stamp = (at) => h`<p class="hm-stale" role="status">${App.icon('clock', { size: 16 })}<span>${t('home.lastUpdate', { t: App.fmt.dateTime(at) })}</span></p>`;
  /** Connection: cb('online' | 'offline') — core events; cb('reconnect') — /api/stream reopened after a drop
      (App.live 'reconnect'): something may have slipped past — re-read. Subscriptions end when leaving the screen. */
  function watchNet(cb) {
    const offs = [App.on('online', () => cb('online')), App.on('offline', () => cb('offline'))];
    App.live.on('reconnect', () => cb('reconnect'));
    App.onLeave(() => offs.forEach((off) => off()));
  }
  /** First show: offline — the saved copy at once; online — wait for the answer up to SLOW_MS, then the saved copy
      (fresh data replaces it). Nothing saved — wait for the answer; error → the core's error screen ("دوباره کوشش کنید"). */
  async function firstShow(load, saved, showSaved, isShown) {
    if (saved && !isOnline()) showSaved();
    const first = load();
    if (isShown()) { first.catch(() => {}); return; }
    if (!saved) { await first; return; }
    const r = await Promise.race([first.then(() => 'ok', () => 'fail'), sleep(SLOW_MS).then(() => 'slow')]);
    if (r !== 'ok' && !isShown()) showSaved();
    first.catch(() => {});
  }
  /** List screen (numbers, videos): GET path → draw(data, null) and into the cache; offline — draw(saved, its time). */
  function listScreen(key, path, draw) {
    let alive = true;
    let fresh = false;
    let shown = false;
    App.onLeave(() => { alive = false; });
    const saved = readCache(key);
    const showSaved = () => { if (alive && saved) { shown = true; draw(saved.data, saved.at); } };
    async function load() {
      try {
        const data = await App.api('GET', path);
        writeCache(key, data);
        if (alive) { fresh = true; shown = true; draw(data, null); }
        return data;
      } catch (e) {
        if (!alive) return undefined;
        if (isNetErr(e) && saved) { if (!shown) showSaved(); return null; }
        throw e;
      }
    }
    watchNet((ev) => { if (alive && ev === 'online' && !fresh) load().catch(() => {}); });
    return firstShow(load, saved, showSaved, () => shown);
  }

  /** Header with ← as in 08: the arrow on the left (in English too), the title centered */
  const top = (o = {}) => h`<header class="hm-top">
      <span class="hm-top__x" aria-hidden="true"></span>
      ${o.title ? h`<h1 class="hm-top__title">${o.title}</h1>` : o.brand ? h`<span class="hm-top__title">${App.ui.brand()}</span>` : h`<span></span>`}
      <button class="btn-icon hm-top__back" type="button" data-back="${o.back || App.config.home}" aria-label="${t('common.back')}">${App.icon('arrow-left', { size: 26 })}</button>
    </header>`;

  /** Location for distances: GPS → saved → null. The map picker does not open by itself — only by the button on home
      ("موقعیت خود را روی نقشه انتخاب کنید"), otherwise a map sheet would pop up over home. → {lat, lon, source} | null */
  const locate = () => App.location.get({ pick: false, timeout: 8000 });
  /** Signed-in resident's location → POST /api/me/location, at most every 5 minutes; fresh only (GPS or picked). */
  async function sendLocation(loc) {
    if (!App.state.user || !loc || !['gps', 'picked'].includes(loc.source)) return;
    const last = App.store.get('home.locSent');
    if (last && Date.now() - last.at < LOC_EVERY && last.user === App.state.user.id) return;
    App.store.set('home.locSent', { at: Date.now(), user: App.state.user.id });
    const body = { lat: Number(loc.lat), lon: Number(loc.lon) };
    if (num(loc.accuracy) != null) body.accuracy = Number(loc.accuracy);
    try { await App.api('POST', '/api/me/location', body); } catch (e) { App.store.del('home.locSent'); }
  }

  // ---------------------------------------------------------------- my location: GPS, address search or the map
  // A phone opening the app over plain http (Wi-Fi, laptop as server) gets no GPS from the browser; then the
  // resident finds the place by address (GET /api/geo/search, OpenStreetMap Nominatim on the server) or taps the map.
  let geoPermission = 'prompt';   // 'denied' once the person (or the browser) refused location access
  try {
    window.navigator.permissions.query({ name: 'geolocation' }).then((st) => {
      geoPermission = st.state;
      st.onchange = () => { geoPermission = st.state; };
    }, () => {});
  } catch (e) { /* no Permissions API: keep offering GPS where the browser has it */ }
  const gpsPossible = () => !!(window.isSecureContext && window.navigator && window.navigator.geolocation) && geoPermission !== 'denied';
  /** Address search box with result rows (choose(place) on a row); bindPlaceSearch() makes it work. */
  const placeSearch = () => h`<div class="hm-locbox">
      <form class="hm-locsearch" role="search">
        <input class="input" type="search" enterkeyhint="search" autocomplete="off" placeholder="${t('home.loc.search')}" aria-label="${t('home.loc.search')}">
        <button class="btn btn--primary btn--sm btn--inline" type="submit">${App.icon('search', { size: 20 })}<span>${t('common.search')}</span></button>
      </form>
      <div class="hm-locresults" aria-live="polite"></div>
    </div>`;
  function bindPlaceSearch(root, choose) {
    const box = root.querySelector('.hm-locbox');
    if (!box) return;
    const input = box.querySelector('input');
    const out = box.querySelector('.hm-locresults');
    const msg = (text) => { out.innerHTML = String(h`<p class="hm-locmsg">${text}</p>`); };
    let seq = 0;
    box.querySelector('form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (!q) { input.focus(); return; }
      const my = ++seq;
      out.innerHTML = String(App.ui.loading());
      let r;
      try {
        r = await App.api('GET', `/api/geo/search?q=${encodeURIComponent(q)}&lang=${encodeURIComponent(App.lang)}`);
      } catch (err) {
        if (my === seq) msg(isNetErr(err) ? t('home.loc.offline') : err.message);
        return;
      }
      if (my !== seq) return;
      const list = (r.results || []).filter((x) => pt(x.lat, x.lon));
      if (!list.length) { msg(t(r.offline ? 'home.loc.offline' : 'home.loc.noResults')); return; }
      out.innerHTML = String(h`${list.map((x, i) => h`<button class="hm-locrow" type="button" data-i="${i}">
          ${App.icon('pin', { size: 20 })}<span class="hm-locrow__text"><span class="hm-locrow__name"><bdi>${x.name}</bdi></span>${x.full ? h`<span class="hm-locrow__full"><bdi>${x.full}</bdi></span>` : ''}</span>
        </button>`)}`);
      out.querySelectorAll('.hm-locrow').forEach((b) => b.addEventListener('click', () => {
        const x = list[Number(b.dataset.i)];
        choose({ lat: Number(x.lat), lon: Number(x.lon), source: 'picked' });
      }));
    });
  }
  /** Browser GPS → choose(place); refused or impossible → a toast, the search and the map stay. */
  function useGps(choose) {
    App.location.gps().then(choose, () => App.toast(t('loc.denied'), 'error'));
  }
  /** Sheet "my location": GPS (when the browser can give it), address search above the map, or a tap on the map.
      → the saved place {lat, lon, source} or null (cancelled). */
  function pickMyPlace() {
    return new Promise((resolve) => {
      let chosen = null;
      let mapH = null;
      const cur = savedPoint();
      const sheet = App.ui.sheet(h`
        ${gpsPossible() ? App.ui.button({ text: t('loc.useCurrent'), kind: 'outline', icon: 'locate', small: true, attrs: { 'data-hm-gps': '1' } }) : ''}
        ${placeSearch()}
        <p class="muted small">${t('loc.pickHint')}</p>
        <div class="map map--pick" data-hm-pickmap></div>
        <div class="stack stack--sm">
          <button class="btn btn--primary" type="button" data-hm-ok disabled>${App.icon('check')}<span>${t('loc.confirm')}</span></button>
          <button class="btn btn--outline" type="button" data-hm-cancel>${t('common.cancel')}</button>
        </div>`, { title: t('home.loc.title'), onClose: () => { if (mapH) mapH.destroy(); resolve(chosen ? App.location.save(chosen) : null); } });
      const finish = (loc) => { chosen = loc; sheet.close(); };
      const ok = sheet.el.querySelector('[data-hm-ok]');
      bindPlaceSearch(sheet.el, finish);
      const gps = sheet.el.querySelector('[data-hm-gps]');
      if (gps) gps.addEventListener('click', () => useGps(finish));
      mapH = App.map(sheet.el.querySelector('[data-hm-pickmap]'), {
        center: cur ? [cur.lat, cur.lon] : App.config.mapCenter, zoom: 13, scoped: false,
        onPick(q) { chosen = { lat: q.lat, lon: q.lon, source: 'picked' }; ok.disabled = false; },
      });
      ok.addEventListener('click', () => sheet.close());
      sheet.el.querySelector('[data-hm-cancel]').addEventListener('click', () => { chosen = null; sheet.close(); });
    });
  }

  // ---------------------------------------------------------------- home (05/06)
  function banner(a) {
    if (!a) return App.ui.banner({ kind: 'ok', title: t('home.noAlert'), text: t('home.niceDay') });
    const inside = !!a.inside && a.status === 'active';
    return App.ui.banner({
      kind: inside ? 'danger' : 'alert',
      title: alertTitle(a),
      text: inside ? t('home.inDanger') : (away(a.distance_km) || a.place || ''),
      go: '#/alert/' + a.id,
    });
  }
  /** Past alert card — in place of the gray placeholder of 05/06 (same size and color) */
  const pastCard = (a) => h`<a class="card card--muted hm-past__card" href="#/alert/${a.id}" data-go="#/alert/${a.id}" data-alert="${a.id}">
      <span class="hm-past__icon hm-past__icon--${isFlood(a) ? 'flood' : 'quake'}">${App.icon(typeIcon(a), { size: 26 })}</span>
      <span class="hm-past__body">
        <span class="hm-past__head"><strong class="hm-past__title">${typeName(a)}</strong>${num(a.magnitude) != null ? h`<span class="hm-mag">${t('home.magnitude', { m: App.fmt.num(a.magnitude, 1) })}</span>` : ''}</span>
        ${a.place ? h`<span class="hm-past__place"><bdi>${a.place}</bdi></span>` : ''}
        <span class="hm-past__meta">${App.icon('calendar', { size: 16 })}<span>${App.fmt.dateTime(a.starts_at)}</span></span>
        ${num(a.distance_km) != null ? h`<span class="hm-past__meta">${App.icon('pin', { size: 16 })}<span>${away(a.distance_km)}</span></span>` : ''}
      </span>
    </a>`;

  App.screen('home', {
    route: '#/home', nav: 'resident', tab: 'home', title: 'nav.home',
    async render(params, el) {
      let alive = true;
      App.onLeave(() => { alive = false; });
      el.classList.add('hm-home');
      const st = { p: savedPoint(), base: null, at: null, stale: false, open: false };
      // Live events go over the last loaded data: on a slow network an answer requested before the event arrives
      // after it and must not overwrite it (experiment 10: an ended alert popped up on the banner again).
      const added = new Map();   // id → an alert from an 'alert' event that the loaded data does not have yet
      const ended = new Map();   // id → ended by an 'alert_end' event: an ended alert never becomes active again
      let sent = 0;              // number of the last /api/alerts request
      let applied = 0;           // number of the request whose answer is shown; answers to older ones are dropped
      const me = () => st.p || (st.base && st.base.point ? pt(st.base.point.lat, st.base.point.lon) : null);

      function view() {
        const b = st.base || {};
        const active = (b.active || []).filter((a) => !ended.has(String(a.id)));
        added.forEach((a, id) => { if (!ended.has(id) && !active.some((y) => String(y.id) === id)) active.push(withDistance(a, me())); });
        const past = (b.past || []).filter((a) => !added.has(String(a.id)) || ended.has(String(a.id)));
        ended.forEach((a, id) => { if (!past.some((y) => String(y.id) === id)) past.push(a); });
        past.sort((x, y) => String(y.starts_at).localeCompare(String(x.starts_at)) || y.id - x.id);
        return { active: byNear(active), past, point: b.point || null };
      }
      function draw() {
        const v = view();
        const shown = st.open ? v.past : v.past.slice(0, PAST_FIRST);
        const noPlace = !v.point;   // no location in the browser or on the server — no distances
        el.innerHTML = String(h`
          <div class="hm-bar">
            ${App.ui.iconButton({ icon: 'user', label: App.state.user ? t('home.profile') : t('auth.login'), go: App.state.user ? '#/profile' : App.config.login, cls: 'hm-bar__btn' })}
            ${App.ui.brand()}
            ${App.ui.iconButton({ icon: 'search', label: t('common.search'), go: '#/search', cls: 'hm-bar__btn' })}
          </div>
          ${banner(v.active[0])}
          ${st.stale && st.at ? stamp(st.at) : ''}
          ${noPlace ? h`<div class="card hm-locask" data-hm-locask>
            <p class="hm-locask__title">${App.icon('locate', { size: 22 })}<span>${t('home.loc.unknown')}</span></p>
            <p class="hm-locask__why">${t('home.loc.why')}</p>
            ${placeSearch()}
            <div class="hm-locask__btns">
              ${gpsPossible() ? App.ui.button({ text: t('loc.useCurrent'), kind: 'outline', icon: 'locate', small: true, block: false, attrs: { 'data-hm-gps': '1' } }) : ''}
              ${App.ui.button({ text: t('home.loc.onMap'), kind: 'outline', icon: 'map', small: true, block: false, attrs: { 'data-hm-pick': '1' } })}
            </div>
          </div>` : ''}
          ${App.ui.sectionTitle({ title: t('home.pastAlerts'), badge: v.past.length || null, toggle: v.past.length > PAST_FIRST, open: st.open })}
          ${v.past.length ? h`<div class="hm-past">${shown.map(pastCard)}</div>` : App.ui.empty({ icon: 'bell', title: t('home.noPast') })}`);
        if (!st.stale && st.base) writeCache('alerts', v, st.at);   // what the resident saw — for offline use
        const tg = el.querySelector('.section-title__toggle');
        if (tg) tg.addEventListener('click', () => { st.open = !st.open; draw(); });
        if (noPlace) {
          bindPlaceSearch(el, choosePlace);
          const gps = el.querySelector('[data-hm-gps]');
          if (gps) gps.addEventListener('click', () => useGps(choosePlace));
          el.querySelector('[data-hm-pick]').addEventListener('click', () => pickMyPlace().then((loc) => { if (loc) choosePlace(loc); }));
        }
      }
      /** The resident found their place (search result, GPS or the map): save it and recount the distances. */
      function choosePlace(loc) {
        if (!alive || !loc) return;
        const saved = App.location.save(loc);
        sendLocation(saved);
        st.p = pt(saved.lat, saved.lon);
        App.toast(t('home.loc.saved'), 'ok');
        load().catch(() => {});
      }
      const showSaved = (c) => { st.base = c.data; st.at = c.at; st.stale = true; draw(); };
      async function load() {
        const n = ++sent;
        const q = qs(st.p);
        let data;
        try {
          data = await App.api('GET', '/api/alerts' + (q ? '?' + q : ''));
        } catch (e) {
          if (!alive || n < applied) return undefined;
          if (!isNetErr(e)) throw e;
          if (st.base) { if (!st.stale) { st.stale = true; draw(); } return null; }   // have data — show it marked
          const c = readCache('alerts');
          if (!c) throw e;
          showSaved(c);
          return null;
        }
        if (!alive || n < applied) return undefined;   // stale answer: the answer to a newer request is already shown
        applied = n;
        st.base = data; st.at = new Date().toISOString(); st.stale = false;
        const known = new Set([...(data.active || []), ...(data.past || [])].map((y) => String(y.id)));
        added.forEach((a, id) => { if (known.has(id)) added.delete(id); });   // the server has it — event not needed
        draw();
        return data;
      }
      async function refreshPlace() {
        const loc = await locate();
        if (!alive || !loc) return;
        sendLocation(loc);
        const p = pt(loc.lat, loc.lon);
        if (moved(p, st.p)) { st.p = p; await load().catch(() => {}); }
      }

      const saved = readCache('alerts');
      await firstShow(load, saved, () => showSaved(saved), () => !!st.base);
      if (!alive) return undefined;
      // live updates: banner and list change at once on an event (distance computed here), then sync with the server
      App.live.on('alert', (x) => {
        if (!alive || !x || x.id == null) return;
        const id = String(x.id);
        if (ended.has(id)) return;   // already ended — a late event will not bring it back
        added.set(id, x);
        st.at = new Date().toISOString();
        draw();
        const a = withDistance(x, me());
        App.toast(t('home.newAlert', { title: alertTitle(a) + (a.place ? ' — ' + a.place : '') }), a.inside ? 'error' : 'info');
        load().catch(() => {});
      });
      App.live.on('alert_end', (x) => {
        if (!alive || !x || x.id == null) return;
        const id = String(x.id);
        const was = view().active.find((y) => String(y.id) === id) || {};
        ended.set(id, Object.assign({}, was, x, { status: 'ended', inside: false }));
        st.at = new Date().toISOString();
        draw();
        App.toast(t('home.alertEnded', { title: alertTitle(x) }), 'ok');
        load().catch(() => {});
      });
      watchNet((ev) => {
        if (!alive) return;
        if (ev === 'offline') { if (st.base && !st.stale) { st.stale = true; draw(); } return; }
        load().catch(() => {});   // network back or the stream reconnected — re-read the banner and the list
      });
      refreshPlace().catch(() => {});
      const timer = setInterval(() => refreshPlace().catch(() => {}), LOC_EVERY);
      return () => clearInterval(timer);
    },
  });

  // ---------------------------------------------------------------- alert with map (not in Figma — like 05 and 15)
  /** the last 10 opened alerts — for offline; not among them — from the saved home (without safe places) */
  function saveAlert(r) {
    if (!r || !r.alert) return;
    const all = App.store.get(CACHE + 'alert') || {};
    all[r.alert.id] = { at: new Date().toISOString(), data: r };
    const keep = Object.keys(all).sort((a, b) => String(all[b].at).localeCompare(String(all[a].at))).slice(0, 10);
    App.store.set(CACHE + 'alert', Object.fromEntries(keep.map((k) => [k, all[k]])));
  }
  function savedAlert(id) {
    const all = App.store.get(CACHE + 'alert') || {};
    if (all[id] && all[id].data && all[id].data.alert) return all[id];
    const c = readCache('alerts');
    const a = c && [...(c.data.active || []), ...(c.data.past || [])].find((y) => String(y.id) === String(id));
    return a ? { at: c.at, data: { alert: a, safe_places: [], point: c.data.point || null } } : null;
  }

  App.screen('home-alert', {
    route: '#/alert/:id', nav: 'resident', tab: 'home', title: 'home.alert.screen',
    async render(params, el) {
      let alive = true;
      App.onLeave(() => { alive = false; });
      el.classList.add('hm-alert');
      let p = savedPoint();
      let mapH = null;
      let first = true;
      let sent = 0;              // request numbers: an answer to an older one is dropped
      let applied = 0;
      let endedByEvent = false;  // 'alert_end' arrived — the alert is over, whatever a late answer says
      let shown = null;          // the answer shown {alert, safe_places, point}
      let staleAt = null;        // a saved copy is shown (offline) — its time
      let freshAt = null;

      const row = (icon, label, value, extra) => h`<div class="hm-info__row">
          <span class="hm-info__icon">${App.icon(icon, { size: 22 })}</span>
          <div class="hm-info__body"><span class="hm-info__label">${label}</span><span class="hm-info__value"><bdi>${value}</bdi></span>${extra || ''}</div>
        </div>`;

      function parts(r) {
        const a = r.alert;
        const me = r.point ? pt(r.point.lat, r.point.lon) : null;
        const active = a.status === 'active';
        const inside = active && !!a.inside;
        const kind = !active ? 'ok hm-banner--ended' : inside ? 'danger' : 'alert';
        const text = !active ? t('home.alert.ended') : inside ? t('home.inDanger') : (away(a.distance_km) || '');
        const safe = pt(a.safe_lat, a.safe_lon);
        const places = (r.safe_places || []).slice(0, 3);
        const info = h`
          ${row(typeIcon(a), t('home.alert.type'), typeName(a))}
          ${row('clock', t('home.alert.time'), App.fmt.dateTime(a.starts_at))}
          ${row('pin', t('home.alert.place'), a.place || App.fmt.coord(a.lat, a.lon))}
          ${me ? row('locate', t('home.alert.distance'), dist(a.distance_km), h`<span class="hm-info__sub">${t('home.alert.radius', { d: dist(a.radius_km) })}</span>`)
            : row('locate', t('home.alert.radiusLabel'), dist(a.radius_km))}
          ${a.safe_place || safe ? row('shield', t('home.alert.safePlace'), a.safe_place || App.fmt.coord(safe.lat, safe.lon), safe ? routeLink(me, safe) : '') : ''}
          ${num(a.magnitude) != null ? row('quake', t('home.alert.magnitude'), App.fmt.num(a.magnitude, 1),
            num(a.depth_km) != null ? h`<span class="hm-info__sub">${t('home.alert.depth', { d: dist(a.depth_km) })}</span>` : '') : ''}
          ${row('info', t('home.alert.source'), t(a.source === 'usgs' ? 'home.alert.sourceUsgs' : 'home.alert.sourceAdmin'),
            a.url ? ext(a.url, h`<span>${t('home.alert.usgs')}</span>`, 'hm-link') : '')}`;
        const placesHtml = places.length ? h`
          <h2 class="hm-h2">${t('home.alert.safePlaces')}</h2>
          <div class="card hm-places">${places.map((s) => h`<div class="hm-place">
              <span class="hm-place__icon">${App.icon('pin-fill', { size: 22 })}</span>
              <div class="hm-place__body"><span class="hm-place__name"><bdi>${byLang(s.name, s.name_ps)}</bdi></span>
                <span class="hm-place__sub"><bdi>${s.address || ''}</bdi>${s.address && me ? ' — ' : ''}${me ? away(s.distance_km) : ''}</span></div>
              ${routeLink(me, s)}
            </div>`)}</div>` : '';
        const markers = [{ lat: a.lat, lon: a.lon, color: 'red', label: alertTitle(a) + (a.place ? ' — ' + a.place : '') }];
        if (safe) markers.push({ lat: safe.lat, lon: safe.lon, color: 'green', label: t('home.alert.safePlace') + ': ' + (a.safe_place || '') });
        places.forEach((s) => markers.push({ lat: s.lat, lon: s.lon, color: 'green', label: byLang(s.name, s.name_ps) }));
        return {
          a, me, markers, info, placesHtml,
          banner: App.ui.banner({ kind, title: alertTitle(a), text }),
          circle: { lat: a.lat, lon: a.lon, radius: (num(a.radius_km) || 2) * 1000, color: inside ? 'red' : 'orange' },
        };
      }

      function draw(r) {
        const x = parts(r);
        const note = staleAt ? stamp(staleAt) : '';
        if (first) {
          first = false;
          el.innerHTML = String(h`
            ${top({ title: t('home.alert.screen'), back: App.config.home })}
            <div data-part="note">${note}</div>
            <div data-part="banner">${x.banner}</div>
            <div class="hm-mapwrap" dir="ltr">
              <div class="hm-map" data-part="map"></div>
              <button class="hm-mapbtn" type="button" data-hm-myplace></button>
            </div>
            <div class="hm-legend">
              <span><i class="hm-legend__dot" style="background:${DOT.red}"></i>${t('home.alert.point')}</span>
              <span><i class="hm-legend__dot hm-legend__dot--me"></i>${t('home.alert.you')}</span>
              <span><i class="hm-legend__dot" style="background:${DOT.green}"></i>${t('home.alert.safe')}</span>
            </div>
            <div class="card hm-info" data-part="info">${x.info}</div>
            <div data-part="places">${x.placesHtml}</div>
            ${x.a.image ? h`<figure class="hm-figure"><img src="${x.a.image}" alt="${t('home.alert.image')}" loading="lazy"></figure>` : ''}`);
          mapH = App.map(el.querySelector('[data-part="map"]'), { center: [x.a.lat, x.a.lon], zoom: 13, markers: x.markers, circle: x.circle });
          el.querySelector('[data-hm-myplace]').addEventListener('click', () => pickMyPlace().then((loc) => {
            if (!alive || !loc) return;
            sendLocation(loc);
            p = pt(loc.lat, loc.lon);
            App.toast(t('home.loc.saved'), 'ok');
            load().catch(() => {});
          }));
          showMe(x, true);
          return;
        }
        el.querySelector('[data-part="note"]').innerHTML = String(note);
        el.querySelector('[data-part="banner"]').innerHTML = String(x.banner);
        el.querySelector('[data-part="info"]').innerHTML = String(x.info);
        el.querySelector('[data-part="places"]').innerHTML = String(x.placesHtml);
        if (mapH) mapH.setMarkers(x.markers).setCircle(x.circle);
        showMe(x, false);
      }
      let meMk = null;
      /** "You" on the map: a blue dot with a halo and a permanent label «شما» (not a pin like the others); no
          location — a clear «پیدا کردن موقعیت من» button right on the map, otherwise a small "change" button. */
      function showMe(x, fit) {
        const btn = el.querySelector('[data-hm-myplace]');
        btn.className = 'hm-mapbtn' + (x.me ? '' : ' hm-mapbtn--big');
        btn.setAttribute('aria-label', t(x.me ? 'home.loc.change' : 'home.loc.findMine'));
        // the button inherits dir="ltr" (its inset is resolved by its own direction); the text is isolated
        btn.innerHTML = String(h`${App.icon('locate', { size: 22 })}${x.me ? '' : h`<bdi dir="${App.dir()}">${t('home.loc.findMine')}</bdi>`}`);
        if (!mapH || !window.L) return;
        if (meMk) { meMk.remove(); meMk = null; }
        if (x.me) {
          meMk = window.L.marker([x.me.lat, x.me.lon], {
            icon: window.L.divIcon({ className: 'hm-me', iconSize: [22, 22], iconAnchor: [11, 11],
              html: String(h`<span class="hm-me__halo"></span><span class="hm-me__dot"></span><span class="hm-me__label">${t('home.alert.you')}</span>`) }),
            zIndexOffset: 1000, keyboard: false, title: t('home.alert.you'),
          }).addTo(mapH.map);
        }
        if (fit) {
          const pts = x.markers.map((m) => [m.lat, m.lon]);
          if (x.me) pts.push([x.me.lat, x.me.lon]);
          const b = window.L.latLngBounds(pts);
          const c = window.L.circle([x.circle.lat, x.circle.lon], { radius: x.circle.radius }).addTo(mapH.map);
          b.extend(c.getBounds());
          c.remove();
          mapH.map.fitBounds(b, { padding: [28, 28] });
        }
      }
      /** ended by an event — show it as ended */
      const fix = (r) => (endedByEvent && r.alert.status !== 'ended'
        ? Object.assign({}, r, { alert: Object.assign({}, r.alert, { status: 'ended', inside: false }) }) : r);
      const show = (r) => { shown = r; draw(fix(r)); };
      async function load() {
        const n = ++sent;
        const q = qs(p);
        let r;
        try {
          r = await App.api('GET', `/api/alerts/${encodeURIComponent(params.id)}${q ? '?' + q : ''}`);
        } catch (e) {
          if (!alive || n < applied) return undefined;
          if (!isNetErr(e)) throw e;
          if (shown) { if (!staleAt) { staleAt = freshAt || new Date().toISOString(); show(shown); } return null; }
          const c = savedAlert(params.id);
          if (!c) throw e;
          staleAt = c.at; show(c.data);
          return null;
        }
        if (!alive || n < applied) return undefined;   // stale answer
        applied = n; staleAt = null; freshAt = new Date().toISOString();
        saveAlert(r);
        show(r);
        return r;
      }

      const saved = savedAlert(params.id);
      await firstShow(load, saved, () => { staleAt = saved.at; show(saved.data); }, () => !!shown);
      if (!alive) return;
      App.live.on('alert_end', (x) => {
        if (!alive || !x || String(x.id) !== String(params.id)) return;
        endedByEvent = true;
        if (shown) show(shown);
        App.toast(t('home.alertEnded', { title: alertTitle(x) }), 'ok');
        load().catch(() => {});
      });
      watchNet((ev) => {
        if (!alive) return;
        if (ev === 'offline') { if (shown && !staleAt) { staleAt = freshAt || new Date().toISOString(); show(shown); } return; }
        load().catch(() => {});
      });
      locate().then((loc) => {
        if (!alive || !loc) return;
        sendLocation(loc);
        const np = pt(loc.lat, loc.lon);
        if (moved(np, p)) { p = np; load().catch(() => {}); }
      }).catch(() => {});
    },
  });

  // ---------------------------------------------------------------- search (09)
  const sRow = (o) => {
    const inner = h`<span class="row__icon">${App.icon(o.icon)}</span><span class="row__body"><span class="row__title"><bdi>${o.title}</bdi></span>${o.sub ? h`<span class="row__sub"><bdi>${o.sub}</bdi></span>` : ''}</span>`;
    if (o.go) return h`<a class="row hm-row" href="${o.go}" data-go="${o.go}">${inner}</a>`;
    if (o.ext) return h`<a class="row hm-row" href="${o.href}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
    return h`<a class="row hm-row" href="${o.href}">${inner}</a>`;
  };
  const group = (kind, title, rows) => (rows.length ? h`<section class="hm-group" data-group="${kind}"><h2 class="hm-group__title">${title}</h2>${rows}</section>` : '');
  function results(r) {
    const html = [
      group('alerts', t('home.search.alerts'), (r.alerts || []).map((a) => sRow({
        icon: typeIcon(a), title: alertTitle(a), go: '#/alert/' + a.id,
        sub: [a.place, App.fmt.date(a.starts_at)].filter(Boolean).join(' — ') }))),
      group('videos', t('home.search.videos'), (r.videos || []).map((v) => sRow({
        icon: 'play-circle', title: byLang(v.title, v.title_ps), sub: App.fmt.date(v.created_at), go: '#/video/' + v.id }))),
      group('numbers', t('home.search.numbers'), (r.numbers || []).map((n) => sRow({
        icon: 'phone', title: App.phone.display(n.number), sub: byLang(n.title, n.title_ps), href: App.phone.tel(n.number) }))),
      group('places', t('home.search.places'), (r.places || []).map((s) => sRow({
        icon: 'pin', title: byLang(s.name, s.name_ps), ext: true, href: placeUrl(s),
        sub: [s.address, away(s.distance_km)].filter(Boolean).join(' — ') }))),
    ].filter((x) => x !== '');
    return html.length ? h`${html}` : App.ui.empty({ icon: 'search', title: t('search.empty') });
  }

  App.screen('home-search', {
    route: '#/search', nav: null, title: 'common.search',
    render(params, el) {
      el.classList.add('hm-search');
      el.innerHTML = String(h`
        <div class="hm-sbar" role="search" dir="ltr">
          <button class="hm-sbar__btn" type="button" data-back="${App.config.home}" aria-label="${t('common.back')}">${App.icon('arrow-left')}</button>
          <input class="hm-sbar__input" type="text" inputmode="search" enterkeyhint="search" autocomplete="off" dir="${App.dir()}"
            placeholder="${t('search.placeholder')}" aria-label="${t('common.search')}" value="${params.q || ''}">
          <button class="hm-sbar__btn hm-sbar__clear" type="button" aria-label="${t('home.search.clear')}">${App.icon('close')}</button>
        </div>
        <div class="hm-results" aria-live="polite"></div>`);
      const input = el.querySelector('.hm-sbar__input');
      const out = el.querySelector('.hm-results');
      const hint = () => h`<p class="hm-hint">${t('home.search.hint')}</p>`;
      let timer = null;
      let seq = 0;
      async function run(raw) {
        const q = String(raw || '').trim();
        const my = ++seq;
        try { history.replaceState(null, '', q ? '#/search?q=' + encodeURIComponent(q) : '#/search'); } catch (e) { /* */ }
        if (!q) { out.innerHTML = String(hint()); return; }
        out.innerHTML = String(App.ui.loading());
        const p = savedPoint();
        let r;
        try {
          r = await App.api('GET', '/api/search?q=' + encodeURIComponent(q) + (p ? '&' + qs(p) : ''));
        } catch (e) {
          if (my === seq) out.innerHTML = String(App.ui.empty({ icon: 'alert', title: e.message }));
          return;
        }
        if (my === seq) out.innerHTML = String(results(r));
      }
      input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => run(input.value), 300); });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(timer); run(input.value); } });
      el.querySelector('.hm-sbar__clear').addEventListener('click', () => { input.value = ''; clearTimeout(timer); run(''); input.focus(); });
      run(params.q || '');
      if (!params.q) setTimeout(() => { try { input.focus(); } catch (e) { /* */ } }, 50);
      return () => clearTimeout(timer);
    },
  });

  // ---------------------------------------------------------------- videos (not in Figma — cards like 17, no ✎ or 🗑)
  const play = (size = 18) => h`<span class="hm-play" aria-hidden="true">${App.icon('play', { size })}</span>`;
  const thumb = (v) => h`<span class="hm-thumb">${v.youtube_id ? h`<img src="${YT_THUMB}${v.youtube_id}/hqdefault.jpg" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}${play()}</span>`;
  const vCard = (v) => h`<a class="hm-vcard" href="#/video/${v.id}" data-go="#/video/${v.id}" data-video="${v.id}">
      <span class="hm-vcard__body"><span class="hm-vcard__title"><bdi>${byLang(v.title, v.title_ps)}</bdi></span><span class="hm-vcard__date">${App.fmt.date(v.created_at)}</span></span>
      ${thumb(v)}
    </a>`;
  /** the YouTube preview did not load (no network) — a gray card with ▶ remains */
  const thumbFallback = (el) => el.querySelectorAll('.hm-thumb img').forEach((img) => img.addEventListener('error', () => img.remove()));

  App.screen('home-videos', {
    route: '#/videos', nav: 'resident', tab: 'videos', title: 'home.videos.title',
    render(params, el) {
      el.classList.add('hm-videos');
      return listScreen('videos', '/api/videos', (r, at) => {
        const list = r.videos || [];
        el.innerHTML = String(h`
          <div class="brand-bar">${App.ui.brand()}</div>
          ${App.ui.pageTitle({ title: t('home.videos.title'), sub: t('home.videos.sub') })}
          ${at ? stamp(at) : ''}
          ${list.length ? h`<div class="hm-vlist">${list.map(vCard)}</div>` : App.ui.empty({ icon: 'video', title: t('home.videos.empty') })}`);
        thumbFallback(el);
      });
    },
  });

  App.screen('home-video', {
    route: '#/video/:id', nav: 'resident', tab: 'videos', title: 'nav.videos',
    render(params, el) {
      el.classList.add('hm-video');
      return listScreen('videos', '/api/videos', (r, at) => drawVideo(el, (r.videos || []).find((x) => String(x.id) === String(params.id)), at));
    },
  });
  /** Player: a youtube-nocookie iframe, <video> for a file, no link — "ویدیو موجود نیست". A fresh list after
      the saved one does not recreate the player (the video does not restart) — only the mark changes. */
  function drawVideo(el, v, at) {
    const note = at ? stamp(at) : '';
    if (v && el.dataset.video === String(v.id) && el.querySelector('.hm-player')) {
      el.querySelector('[data-part="note"]').innerHTML = String(note);
      return;
    }
    el.dataset.video = v ? String(v.id) : '';
    if (!v) {   // no such video (the admin deleted it) — not an app error: an empty state with a link to the list
      el.innerHTML = String(h`${top({ title: t('nav.videos'), back: '#/videos' })}
        ${App.ui.empty({ icon: 'video', title: t('home.video.notFound'), action: { text: t('home.videos.title'), go: '#/videos' } })}`);
      return;
    }
    const title = byLang(v.title, v.title_ps);
    let player;
    if (v.kind === 'youtube' && v.youtube_id) {
      player = h`<div class="hm-player"><iframe src="${YT_EMBED}${v.youtube_id}?rel=0&hl=${App.lang}" title="${title}" loading="lazy"
        allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></div>`;
    } else if (v.kind === 'file' && v.url) {
      player = h`<div class="hm-player"><video src="${v.url}" controls playsinline preload="metadata" aria-label="${t('home.video.play')}"></video></div>`;
    } else {
      player = h`<div class="hm-player hm-player--none">${play(30)}<span>${t('home.video.missing')}</span></div>`;
    }
    el.innerHTML = String(h`
      ${top({ title: t('nav.videos'), back: '#/videos' })}
      <div data-part="note">${note}</div>
      ${player}
      <h2 class="hm-video__title"><bdi>${title}</bdi></h2>
      <p class="hm-video__meta">${App.icon('calendar', { size: 16 })}<span>${App.fmt.date(v.created_at)}</span></p>
      ${v.source && v.source !== 'admin' ? h`<p class="hm-video__meta">${App.icon('info', { size: 16 })}<span>${t('home.video.source', { s: '⁨' + v.source + '⁩' })}</span></p>` : ''}`);
  }

  // ---------------------------------------------------------------- numbers (07 — text, 08 — header with ←, 16 — cards)
  const numCard = (n) => h`<a class="hm-num" href="${App.phone.tel(n.number)}" aria-label="${t('home.numbers.call', { n: App.fmt.digits(n.number) })}">
      <span class="hm-num__body"><span class="hm-num__n">${App.phone.display(n.number)}</span><span class="hm-num__t"><bdi>${byLang(n.title, n.title_ps)}</bdi></span></span>
      <span class="hm-num__call" aria-hidden="true">${App.icon('phone', { size: 24 })}</span>
    </a>`;

  App.screen('home-numbers', {
    route: '#/numbers', nav: 'resident', tab: 'call', title: 'home.numbers.title',
    render(params, el) {
      el.classList.add('hm-numbers');
      // numbers matter most offline: the last list is kept in the browser, a call (tel:) works without the internet
      return listScreen('numbers', '/api/numbers', (r, at) => {
        const list = r.numbers || [];
        el.innerHTML = String(h`
          ${top({ back: App.config.home, brand: true })}
          <h1 class="sr-only">${t('home.numbers.title')}</h1>
          ${at ? stamp(at) : ''}
          <p class="hm-lead">${t('home.callNumbers')}</p>
          <hr class="hm-divider">
          ${list.length ? h`<div class="hm-nums">${list.map(numCard)}</div>` : App.ui.empty({ icon: 'phone', title: t('home.numbers.empty') })}`);
      });
    },
  });

  // ---------------------------------------------------------------- a new alert on any resident screen
  // Home has its banner; on every other resident screen (videos, numbers, search, alert, profile, SOS…) a new alert
  // shows a notice at the top of the column: «هشدار سیلاب — به دوری ۱۲ کیلو متر», red «شما در منطقه خطر هستید»
  // inside the radius; tap → #/alert/:id; the phone vibrates if it can. Never on admin screens. Global subscription:
  // it lives for the whole app, not for one screen.
  let flash = null;   // the notice on screen: {el, id, inside}
  const onAdminScreen = () => {
    const c = App.current();
    const app = document.getElementById('app');
    return (!!c && /^\/admin(\/|$)/.test(c.path || '')) || !!(app && app.classList.contains('theme-admin'));
  };
  const onScreen = (name) => { const c = App.current(); return !!c && c.name === name; };
  function hideFlash(id) {
    if (flash && (id == null || String(flash.id) === String(id))) { flash.el.remove(); flash = null; }
  }
  /** Vibrate only after the person has touched the page: Chrome blocks (and logs) vibration before that. */
  function vibrate(inside) {
    const nav = window.navigator;
    if (!nav || typeof nav.vibrate !== 'function') return;
    if (nav.userActivation && !nav.userActivation.hasBeenActive) return;
    try { nav.vibrate(inside ? [400, 150, 400, 150, 400] : [250, 120, 250]); } catch (e) { /* not allowed here */ }
  }
  function showFlash(a) {
    const inside = !!a.inside && a.status !== 'ended';
    // a "you are in the danger zone" notice is not replaced by a farther alert
    if (flash && flash.inside && !inside && String(flash.id) !== String(a.id)) return;
    hideFlash();
    const go = '#/alert/' + a.id;
    const el = document.createElement('div');
    el.className = 'hm-flash hm-flash--' + (inside ? 'danger' : 'alert');
    el.setAttribute('role', 'alert');
    el.dataset.alert = String(a.id);
    el.innerHTML = String(h`<a class="hm-flash__body" href="${go}" data-go="${go}">
        <span class="hm-flash__icon">${App.icon(typeIcon(a), { size: 24 })}</span>
        <span class="hm-flash__text"><strong>${alertTitle(a)}</strong><span><bdi>${inside ? t('home.inDanger') : (away(a.distance_km) || a.place || '')}</bdi></span></span>
      </a>
      <button class="hm-flash__close" type="button" aria-label="${t('common.close')}">${App.icon('close', { size: 20 })}</button>`);
    el.querySelector('.hm-flash__body').addEventListener('click', () => hideFlash());
    el.querySelector('.hm-flash__close').addEventListener('click', () => hideFlash());
    document.body.appendChild(el);
    flash = { el, id: a.id, inside };
  }
  async function onAnyAlert(x) {
    if (!x || x.id == null || onAdminScreen()) return;
    const me = savedPoint();
    const a = withDistance(x, me);   // at once, by the server's rule; the server's answer below wins
    vibrate(a.inside);
    if (onScreen('home')) return;    // home shows it in its banner and a toast
    showFlash(a);
    try {   // the server's view of this resident: a signed-in resident without a point — their stored place
      const r = await App.api('GET', `/api/alerts/${encodeURIComponent(x.id)}${me ? '?' + qs(me) : ''}`);
      if (!flash || String(flash.id) !== String(x.id) || !r || !r.alert) return;
      if (r.alert.status !== 'active') hideFlash(x.id); else showFlash(withDistance(r.alert, me));
    } catch (e) { /* no network: keep the notice from the event */ }
  }
  let notifying = false;
  App.on('route', (cur) => {
    if (!notifying) {   // after App.start (first screen): the session and the server clock are known
      notifying = true;
      App.live.on('alert', onAnyAlert, { global: true });
      App.live.on('alert_end', (x) => { if (x) hideFlash(x.id); }, { global: true });
    }
    // home shows the alert itself; on the alert's own screen the notice is not needed either
    if (flash && cur && (cur.name === 'home' || (cur.name === 'home-alert' && String(cur.params.id) === String(flash.id)))) hideFlash();
    if (flash && onAdminScreen()) hideFlash();
  });
})(window.App);
