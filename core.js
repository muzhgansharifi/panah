/* PANAH (پناه) ui-core — the global App: navigation, API, live events, strings, formats, phone, location, map.
   ES2020, no build step. Contract and examples: app/web/README.md. Also loads in node (for tests): without a DOM
   App.t, App.fmt, App.phone and App.html still work. */
(function (root) {
  'use strict';
  const App = root.App || {};
  root.App = App;
  const hasDom = typeof root.document !== 'undefined';

  // ---------------------------------------------------------------- settings
  App.config = Object.assign({
    home: '#/home',               // empty address and "home"
    login: '#/login',             // where auth:'user' screens go when nobody is signed in
    adminHome: '#/admin',
    adminLogin: '#/admin/login',  // where auth:'admin' screens go when no admin is signed in
    me: '/api/me',                // GET → {user, admin} (or the user itself); 401 — nobody is signed in
    stream: '/api/stream',        // SSE
    langs: ['fa', 'ps', 'en'],
    groups: ['auth', 'home', 'sos', 'admin'], // per-group dictionaries: i18n/<lang>/<group>.json
    tiles: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    mapCenter: [34.5281, 69.1723], // Kabul
    mapZoom: 12,
    storagePrefix: 'panah.',
  }, App.config || {});

  // ---------------------------------------------------------------- small event bus
  const bus = new Map();
  App.on = function (name, cb) {
    if (!bus.has(name)) bus.set(name, new Set());
    bus.get(name).add(cb);
    return () => bus.get(name).delete(cb);
  };
  App.emit = function (name, data) {
    (bus.get(name) || []).forEach((cb) => { try { cb(data); } catch (e) { console.error(e); } });
  };

  // ---------------------------------------------------------------- safe HTML
  class Safe { constructor(s) { this.s = s; } toString() { return this.s; } }
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  App.esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ESC[c]);
  App.raw = (s) => new Safe(String(s == null ? '' : s));
  App.isSafe = (v) => v instanceof Safe;
  function piece(v) {
    if (v == null || v === false || v === true) return '';
    if (v instanceof Safe) return v.s;
    if (Array.isArray(v)) return v.map(piece).join('');
    return App.esc(v);
  }
  /** Template: App.html`<p>${text}</p>` — every interpolated value is escaped, except App.html/App.ui/App.icon. */
  App.html = function (strings, ...vals) {
    let out = strings[0];
    for (let i = 0; i < vals.length; i++) out += piece(vals[i]) + strings[i + 1];
    return new Safe(out);
  };

  // ---------------------------------------------------------------- storage (localStorage)
  App.store = {
    get(key, dflt = null) {
      if (!hasDom) return dflt;
      try { const v = root.localStorage.getItem(App.config.storagePrefix + key); return v == null ? dflt : JSON.parse(v); }
      catch (e) { return dflt; }
    },
    set(key, val) { if (hasDom) try { root.localStorage.setItem(App.config.storagePrefix + key, JSON.stringify(val)); } catch (e) { /* private mode */ } },
    del(key) { if (hasDom) try { root.localStorage.removeItem(App.config.storagePrefix + key); } catch (e) { /* */ } },
  };

  // ---------------------------------------------------------------- paths to our own files
  App.base = (function () {
    try { const s = hasDom && root.document.currentScript; return s ? new URL('../', s.src).href : ''; }
    catch (e) { return ''; }
  })();
  App.url = (p) => App.base + String(p).replace(/^\//, '');

  // ---------------------------------------------------------------- strings: App.t
  const dicts = {};
  const LANG_TAG = { fa: 'fa-AF', ps: 'ps-AF', en: 'en' };
  App.i18n = {
    dicts,
    missing: new Set(),
    add(lang, obj) { dicts[lang] = Object.assign(dicts[lang] || {}, obj); },
    /** Common i18n/<lang>.json, then group dictionaries i18n/<lang>/<group>.json: a group key overrides a common one;
        missing file — silently skipped; broken JSON — an error in the console, then skipped. */
    async load(lang) {
      if (dicts[lang]) return dicts[lang];
      const res = await fetch(App.url('i18n/' + lang + '.json'), { cache: 'no-cache' });
      if (!res.ok) throw new Error('i18n ' + lang + ': ' + res.status);
      const merged = await res.json();
      const parts = await Promise.all((App.config.groups || []).map(async (g) => {
        let r;
        try { r = await fetch(App.url(`i18n/${lang}/${g}.json`), { cache: 'no-cache' }); } catch (e) { return null; }
        if (!r.ok) return null;
        try { return await r.json(); } catch (e) { console.error(`i18n/${lang}/${g}.json: ${e.message}`); return null; }
      }));
      parts.forEach((p) => { if (p && typeof p === 'object') Object.assign(merged, p); });
      App.i18n.add(lang, merged);
      return dicts[lang];
    },
  };
  const savedLang = App.store.get('lang');
  App.lang = App.config.langs.includes(savedLang) ? savedLang : 'fa';
  App.dir = (lang = App.lang) => (lang === 'en' ? 'ltr' : 'rtl');

  /** App.t('home.distance', {d: '۱۲ کیلومتر'}); empty translation → Dari; no key → the key. Numbers in the language's digits. */
  App.t = function (key, vars) {
    let s = (dicts[App.lang] || {})[key];
    if (s == null || s === '') s = (dicts.fa || {})[key];
    if (s == null || s === '') { App.i18n.missing.add(key); s = key; }
    if (vars) {
      s = s.replace(/\{(\w+)\}/g, (m, k) => {
        if (!(k in vars)) return m;
        const v = vars[k];
        return typeof v === 'number' ? App.fmt.num(v) : String(v == null ? '' : v);
      });
    }
    return s;
  };

  function applyLang() {
    if (!hasDom) return;
    const el = root.document.documentElement;
    el.lang = LANG_TAG[App.lang] || App.lang;
    el.dir = App.dir();
  }
  App.setLang = async function (lang) {
    if (!App.config.langs.includes(lang)) return;
    try { await App.i18n.load(lang); } catch (e) { console.error(e); return; }
    App.lang = lang;
    App.store.set('lang', lang);
    applyLang();
    App.emit('lang', lang);
    if (current) App.refresh();
  };

  // ---------------------------------------------------------------- formats: App.fmt
  const FA = '۰۱۲۳۴۵۶۷۸۹';
  const fmt = {};
  App.fmt = fmt;
  fmt.digits = (s, lang = App.lang) => (lang === 'en' ? String(s) : String(s).replace(/[0-9]/g, (d) => FA[d]));
  fmt.latin = (s) => String(s == null ? '' : s)
    .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06F0))
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));
  /** A number in the language's digits: 12 → "۱۲", (34.555, 4) → "۳۴٫۵۵۵۰". */
  fmt.num = function (n, decimals) {
    const x = Number(n);
    if (!Number.isFinite(x)) return '';
    let s = decimals == null ? String(Math.round(x * 1e6) / 1e6) : x.toFixed(decimals);
    if (App.lang !== 'en') s = s.replace('.', '٫').replace('-', '−');
    return fmt.digits(s);
  };
  function toDate(d) {
    if (d instanceof Date) return d;
    if (typeof d === 'number') return new Date(d);
    if (typeof d === 'string') {
      // server time is UTC ISO 8601; no zone means UTC
      if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(d)) d = d.replace(' ', 'T') + 'Z';
      return new Date(d);
    }
    return new Date(NaN);
  }
  fmt.toDate = toDate;
  let partsFmt = null;
  /** Date parts in the Solar Hijri calendar, Kabul time: {y, m, d, H, M}. */
  fmt.solar = function (d) {
    const date = toDate(d);
    if (isNaN(date)) return null;
    partsFmt = partsFmt || new Intl.DateTimeFormat('fa-AF-u-ca-persian-nu-latn', {
      timeZone: 'Asia/Kabul', year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: '2-digit', hourCycle: 'h23',
    });
    const p = {};
    for (const x of partsFmt.formatToParts(date)) p[x.type] = x.value;
    return { y: +fmt.latin(p.year), m: +fmt.latin(p.month), d: +fmt.latin(p.day), H: +fmt.latin(p.hour) % 24, M: +fmt.latin(p.minute) };
  };
  const pad = (n) => String(n).padStart(2, '0');
  /** «۱۱ جوزا ۱۴۰۳» */
  fmt.date = (d) => { const s = fmt.solar(d); return s ? App.t('fmt.date', { d: s.d, month: App.t('month.' + s.m), y: fmt.digits(s.y) }) : ''; };
  /** "۱۴۰۵/۰۲/۱۱" (as in 10) */
  fmt.dateNum = (d) => { const s = fmt.solar(d); return s ? fmt.digits(`${s.y}/${pad(s.m)}/${pad(s.d)}`) : ''; };
  /** "۱۰:۳۳" — 24-hour clock, Kabul */
  fmt.time = (d) => { const s = fmt.solar(d); return s ? fmt.digits(`${pad(s.H)}:${pad(s.M)}`) : ''; };
  /** «۱۱ جوزا ۱۴۰۳، ۱۰:۳۳» */
  fmt.dateTime = (d) => (fmt.solar(d) ? App.t('fmt.dateTime', { date: fmt.date(d), time: fmt.time(d) }) : '');
  /** "۱۴۰۵/۰۲/۱۱ - ۱۰:۳۳ - قبل از ظهر" (SOS header in 10) */
  fmt.stamp = function (d) {
    const s = fmt.solar(d);
    if (!s) return '';
    const h12 = s.H % 12 === 0 ? 12 : s.H % 12;
    return `${fmt.dateNum(d)} - ${fmt.digits(`${h12}:${pad(s.M)}`)} - ${App.t(s.H < 12 ? 'fmt.am' : 'fmt.pm')}`;
  };
  /** "همین حالا", "۵ دقیقه پیش", …; older than a week — the date. */
  fmt.ago = function (d, now = Date.now()) {
    const date = toDate(d);
    if (isNaN(date)) return '';
    const sec = Math.max(0, (now - date.getTime()) / 1000);
    if (sec < 60) return App.t('fmt.now');
    if (sec < 3600) return App.t('fmt.minutesAgo', { n: Math.floor(sec / 60) });
    if (sec < 86400) return App.t('fmt.hoursAgo', { n: Math.floor(sec / 3600) });
    if (sec < 7 * 86400) return App.t('fmt.daysAgo', { n: Math.floor(sec / 86400) });
    return fmt.date(date);
  };
  /** Distance in km → "۱۲ کیلومتر", "۱٫۵ کیلومتر", "۳۵۰ متر". */
  fmt.distance = function (km) {
    const x = Number(km);
    if (!Number.isFinite(x)) return '';
    if (x < 1) return App.t('fmt.m', { n: Math.max(10, Math.round(x * 100) * 10) });
    if (x < 10) return App.t('fmt.km', { n: Math.round(x * 10) / 10 });
    return App.t('fmt.km', { n: Math.round(x) });
  };
  /** «۶۷٪» */
  fmt.percent = (n) => App.t('fmt.percent', { n: Math.round(Number(n)) });
  // invisible LRI…PDI marks: digit groups and Latin text are not reordered inside a Dari line
  const ISO = (s) => '⁦' + s + '⁩';
  /** "۳۴٫۵۵۵۰°N, ۶۹٫۲۰۷۰°E" (as in 13) */
  fmt.coord = function (lat, lon) {
    const a = Number(lat), b = Number(lon);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return '';
    return ISO(`${fmt.num(Math.abs(a), 4)}°${a >= 0 ? 'N' : 'S'}, ${fmt.num(Math.abs(b), 4)}°${b >= 0 ? 'E' : 'W'}`);
  };

  // ---------------------------------------------------------------- phone: App.phone
  App.phone = {
    /** 07XXXXXXXX / 7XXXXXXXX / +937… / 00937… / Dari digits, spaces and dashes → "+937XXXXXXXX"; otherwise null. */
    normalize(input) {
      if (input == null) return null;
      let s = fmt.latin(input).replace(/[\s\-().‌‎‏⁦-⁩]/g, '');
      if (s.startsWith('00')) s = '+' + s.slice(2);
      if (s.startsWith('+93')) s = s.slice(3);
      else if (s.startsWith('93') && s.length === 11) s = s.slice(2);
      if (s.startsWith('0')) s = s.slice(1);
      return /^7\d{8}$/.test(s) ? '+93' + s : null;
    },
    isValid(input) { return App.phone.normalize(input) !== null; },
    /** "0777123456" (Latin digits) */
    local(input) { const n = App.phone.normalize(input); return n ? '0' + n.slice(3) : null; },
    /** "۰۷۷۷ ۱۲۳ ۴۵۶"; short numbers (119) as is, in the language's digits. Wrapped in LRI…PDI. */
    display(input) {
      const n = App.phone.normalize(input);
      if (!n) {
        const s = fmt.latin(input).replace(/\s/g, '');
        return /^\+?\d{2,15}$/.test(s) ? ISO(fmt.digits(s)) : String(input == null ? '' : input);
      }
      const l = '0' + n.slice(3);
      return ISO(fmt.digits(`${l.slice(0, 4)} ${l.slice(4, 7)} ${l.slice(7)}`));
    },
    /** link for a call: "tel:+93777123456", "tel:119" */
    tel(input) {
      const n = App.phone.normalize(input);
      if (n) return 'tel:' + n;
      const s = fmt.latin(input).replace(/[^\d+]/g, '');
      return s ? 'tel:' + s : '';
    },
  };

  // ---------------------------------------------------------------- state
  App.state = {
    user: null,    // the resident, once signed in
    admin: null,   // the admin, once signed in
    location: App.store.get('location'), // {lat, lon, source, at}
  };
  App.setUser = function (user) { App.state.user = user || null; App.emit('user', App.state.user); if (App.live) App.live.restart(); };
  App.setAdmin = function (admin) { App.state.admin = admin || null; App.emit('admin', App.state.admin); if (App.live) App.live.restart(); };
  /** Ask the server who is signed in. No server or 401 — a guest; never throws. */
  App.refreshMe = async function () {
    try {
      const r = await App.api('GET', App.config.me, null, { timeout: 5000 });
      if (r && typeof r === 'object' && ('user' in r || 'admin' in r)) {
        App.state.user = r.user || null; App.state.admin = r.admin || null;
      } else {
        App.state.user = r && r.id != null ? r : null; App.state.admin = null;
      }
    } catch (e) {
      if (e.status === 401 || e.status === 403) { App.state.user = null; App.state.admin = null; }
    }
    App.emit('user', App.state.user);
    return App.state;
  };

  // ---------------------------------------------------------------- server clock (from the Date header)
  let clockSkew = 0;
  function serverNow() { return Date.now() + clockSkew; }
  function noteServerDate(res) {
    try {
      const t = Date.parse(res.headers.get('Date'));
      if (!isNaN(t)) clockSkew = t + 500 - Date.now(); // Date is accurate to the second
    } catch (e) { /* no header */ }
  }
  App.serverNow = serverNow;

  // ---------------------------------------------------------------- API
  class ApiError extends Error {
    constructor(status, code, message, data) { super(message); this.name = 'ApiError'; this.status = status; this.code = code; this.data = data; }
  }
  App.ApiError = ApiError;
  function errorFrom(status, data) {
    const d = data && typeof data === 'object' ? data : {};
    const e = d.error && typeof d.error === 'object' ? d.error : d;
    const code = e.code || (typeof d.error === 'string' ? d.error : null) || e.reason || ('http_' + status);
    const fallback = status === 401 ? 'error.auth' : status === 403 ? 'error.forbidden' : status === 404 ? 'error.notFound'
      : status >= 500 ? 'error.server' : 'error.generic';
    return new ApiError(status, code, e.message || e.text || e.detail || App.t(fallback), data);
  }
  /** App.api('POST', '/api/sos', {...}) → JSON; error → ApiError {status, code, message}. status 0 — no network. */
  App.api = async function (method, path, body, opts = {}) {
    const init = { method: method.toUpperCase(), credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body != null) {
      const binary = (typeof FormData !== 'undefined' && body instanceof FormData) || (typeof Blob !== 'undefined' && body instanceof Blob);
      if (binary) init.body = body;
      else { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    }
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), opts.timeout || 15000) : null;
    if (ctrl) init.signal = ctrl.signal;
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      const timeout = e && e.name === 'AbortError';
      throw new ApiError(0, timeout ? 'timeout' : 'network', App.t(timeout ? 'error.timeout' : 'error.network'));
    } finally { if (timer) clearTimeout(timer); }
    noteServerDate(res);
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
    if (!res.ok) throw errorFrom(res.status, data);
    return data == null ? {} : data;
  };

  /** Image to the server: File → {url, type, size} (POST /api/upload {data_url, name}; PNG/JPG/WebP up to 3 MB). */
  App.upload = function (file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => App.api('POST', '/api/upload', { data_url: r.result, name: file.name || 'image' }).then(resolve, reject);
      r.onerror = () => reject(new ApiError(0, 'read', App.t('error.generic')));
      r.readAsDataURL(file);
    });
  };

  // ---------------------------------------------------------------- live events: App.live (SSE)
  /* Drops: each time the stream (re)opens after a drop, subscribers get their own 'reconnect' event —
     the screen re-reads its data (something may have slipped past). Last-Event-ID is kept for a new
     EventSource object as well (the ?last_event_id= parameter, understood by server/live.py); for a page with no
     events yet it is the server time when the stream last worked: a server id = its start time in ms + a counter,
     so after a server restart missed events are re-sent and old ones are not repeated. Back/forward cache: on
     pagehide the stream closes (it does not hold one of the 6 connections to the server), on pageshow it reopens. */
  App.live = (function () {
    const subs = new Map();      // event → Set(cb)
    const LOCAL = new Set(['reconnect']); // the core's own events; the server never sends them
    let es = null, attached = new Set(), timer = null, wait = 1000;
    let opened = false, broken = false, paused = false;
    let lastId = '', goodAt = 0;
    const live = { status: 'idle' };
    function setStatus(s) { if (live.status !== s) { live.status = s; App.emit('live', s); } }
    function deliver(ev, data) {
      (subs.get(ev) || []).forEach((cb) => { try { cb(data); } catch (e) { console.error(e); } });
    }
    function dispatch(ev, m) {
      if (m.lastEventId) lastId = m.lastEventId;
      goodAt = serverNow();
      let data = m.data;
      try { data = JSON.parse(m.data); } catch (e) { /* not JSON — pass the text on */ }
      deliver(ev, data);
    }
    function attach(ev) {
      if (!es || attached.has(ev) || LOCAL.has(ev)) return;
      attached.add(ev);
      es.addEventListener(ev, (m) => dispatch(ev, m));
    }
    function url() {
      const id = lastId || String((goodAt || serverNow()) - 1000);
      const s = App.config.stream;
      return s + (s.includes('?') ? '&' : '?') + 'last_event_id=' + encodeURIComponent(id);
    }
    function connect() {
      if (es || paused || !hasDom || typeof root.EventSource === 'undefined' || subs.size === 0) return;
      setStatus(opened ? 'reconnecting' : 'connecting');
      es = new root.EventSource(url(), { withCredentials: true });
      attached = new Set();
      es.onopen = () => {
        const again = opened || broken;
        wait = 1000; opened = true; broken = false; goodAt = serverNow();
        setStatus('open');
        if (again) deliver('reconnect', { lastEventId: lastId || null });
      };
      es.onerror = () => {
        broken = true;
        if (es && es.readyState === 2) { // closed for good (404, not SSE) — reconnect ourselves, pausing up to 30 s
          es.close(); es = null; setStatus('reconnecting');
          clearTimeout(timer); timer = setTimeout(connect, wait); wait = Math.min(wait * 2, 30000);
        } else setStatus('reconnecting');  // the browser reconnects by itself (Last-Event-ID goes in the header)
      };
      subs.forEach((_, ev) => attach(ev));
    }
    function close() {
      if (es) { if (es.readyState === 1) goodAt = serverNow(); es.close(); es = null; broken = true; }
      clearTimeout(timer);
    }
    /** App.live.on('alert', data => …) → unsubscribe function. 'reconnect' — the stream reopened after a drop: re-read
        the data. A subscription made on a screen is removed when leaving it; an app-wide one — {global: true}. */
    live.on = function (ev, cb, opts = {}) {
      if (!subs.has(ev)) subs.set(ev, new Set());
      subs.get(ev).add(cb);
      if (es) attach(ev); else connect();
      const off = () => { const s = subs.get(ev); if (s) s.delete(cb); };
      if (!opts.global && current) current.cleanups.push(off);
      return off;
    };
    live.restart = function () { close(); wait = 1000; connect(); };
    live.stop = function () { close(); setStatus('idle'); };
    /** Network is back — do not wait for the browser's pause, open the stream right away. */
    live.kick = function () { if (!es || es.readyState !== 1) { close(); wait = 1000; connect(); } };
    live.lastEventId = () => lastId;
    if (hasDom) {
      root.addEventListener('pagehide', () => { paused = true; close(); setStatus('paused'); });
      root.addEventListener('pageshow', () => { if (paused) { paused = false; connect(); } });
    }
    return live;
  })();

  // ---------------------------------------------------------------- screens and navigation
  const screens = [];
  App.screens = screens;
  let current = null;           // {screen, params, el, cleanups, path}
  const history = [];           // our own addresses, so that "back" knows whether there is anywhere to go
  function compile(route) {
    const path = String(route).replace(/^#/, '').split('?')[0];
    const keys = [];
    const src = path.split('/').map((seg) => {
      if (seg.startsWith(':')) { keys.push(seg.slice(1)); return '([^/]+)'; }
      return seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('/');
    return { re: new RegExp('^' + src + '/?$'), keys };
  }
  /** App.screen('alert', {route: '#/alert/:id', nav: 'resident'|'admin'|null, auth: 'user'|'admin'|null,
        tab: 'home', title: 'string key', render(params, el) {…; return () => cleanup}}) */
  App.screen = function (name, opts) {
    if (!opts || !opts.route || typeof opts.render !== 'function') throw new Error('App.screen(' + name + '): needs route and render');
    const s = Object.assign({ nav: null, auth: null }, opts, { name }, compile(opts.route));
    const i = screens.findIndex((x) => x.name === name);
    if (i >= 0) screens[i] = s; else screens.push(s);
    if (started && current && !current.screen) route(); // the screen was registered after start
    return s;
  };
  function match(path) {
    let best = null;
    for (const s of screens) {
      const m = s.re.exec(path);
      if (m && (!best || s.keys.length < best.s.keys.length)) best = { s, m };
    }
    if (!best) return null;
    const params = {};
    best.s.keys.forEach((k, i) => { params[k] = decodeURIComponent(best.m[i + 1]); });
    return { screen: best.s, params };
  }
  function parseHash(hash) {
    const raw = String(hash || '').replace(/^#/, '');
    const q = raw.indexOf('?');
    const path = (q >= 0 ? raw.slice(0, q) : raw) || '/';
    const query = {};
    if (q >= 0) new URLSearchParams(raw.slice(q + 1)).forEach((v, k) => { query[k] = v; });
    return { path, query };
  }
  App.go = function (route) {
    const r = String(route).startsWith('#') ? route : '#' + route;
    if (root.location.hash === r) App.refresh(); else root.location.hash = r;
  };
  App.replace = function (route) {
    const r = String(route).startsWith('#') ? route : '#' + route;
    root.history.replaceState(null, '', r);
    if (history.length) history[history.length - 1] = r; else history.push(r);
    routeNow(r);
  };
  /** Back through the app's history; if there is nowhere to go — to fallback (or home). */
  App.back = function (fallback) {
    if (history.length > 1) root.history.back();
    else App.replace(fallback || (current && current.screen && current.screen.nav === 'admin' ? App.config.adminHome : App.config.home));
  };
  App.refresh = function () { if (started) routeNow(root.location.hash); };
  App.current = () => (current && current.screen ? { name: current.screen.name, params: current.params, path: current.path } : null);
  /** Cleanup when leaving a screen (timers, subscriptions). */
  App.onLeave = function (fn) { if (current) current.cleanups.push(fn); };

  function leave() {
    if (!current) return;
    current.cleanups.splice(0).forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
  }
  function route() {
    const hash = root.location.hash;
    const prev = history[history.length - 2];
    if (prev === hash) history.pop(); else if (history[history.length - 1] !== hash) history.push(hash);
    if (history.length > 50) history.shift();
    routeNow(hash);
  }
  function routeNow(hash) {
    const { path, query } = parseHash(hash);
    if (path === '/' || path === '') { App.replace(App.config.home); return; }
    const m = match(path);
    if (m) {
      const s = m.screen;
      const here = '#' + path + (Object.keys(query).length ? '?' + new URLSearchParams(query) : '');
      if (s.auth === 'user' && !App.state.user) { App.replace(App.config.login + '?next=' + encodeURIComponent(here)); return; }
      if (s.auth === 'admin' && !App.state.admin) { App.replace(App.config.adminLogin + '?next=' + encodeURIComponent(here)); return; }
    }
    show(m, Object.assign({}, query, m ? m.params : {}), path);
  }
  function show(m, params, path) {
    leave();
    const app = root.document.getElementById('app');
    const s = m && m.screen;
    const el = root.document.createElement('section');
    el.className = 'screen';
    current = { screen: s, params, el, cleanups: [], path };
    app.textContent = '';
    app.appendChild(el);
    const navKind = s ? s.nav : null;
    app.classList.toggle('has-nav', !!navKind);
    app.classList.toggle('theme-admin', !!s && (s.theme === 'admin' || s.nav === 'admin' || s.auth === 'admin'));
    if (navKind && App.ui && App.ui.tabbar) {
      app.insertAdjacentHTML('beforeend', String(App.ui.tabbar(navKind, s.tab || activeTab(navKind, path))));
    }
    root.scrollTo(0, 0);
    if (!s) {
      el.dataset.screen = 'not-found';
      el.innerHTML = String(App.ui ? App.ui.empty({ icon: 'search', title: App.t('common.notFound'), action: { text: App.t('common.goHome'), go: App.config.home } }) : App.t('common.notFound'));
      root.document.title = App.t('app.brand');
      return;
    }
    el.dataset.screen = s.name;
    root.document.title = s.title && s.route !== App.config.home ? App.t(s.title) + ' — ' + App.t('app.brand') : App.t('app.brand');
    const mine = current;
    const fail = (err) => {
      if (err instanceof ApiError) console.warn(err); else console.error(err); // the screen shows an ApiError itself
      if (current !== mine) return;
      el.innerHTML = String(App.ui.errorState(err));
    };
    try {
      const r = s.render(params, el);
      if (typeof r === 'function') mine.cleanups.push(r);
      else if (r && typeof r.then === 'function') {
        if (!el.firstChild && App.ui) el.innerHTML = String(App.ui.loading());
        r.then((fn) => { if (typeof fn === 'function') { if (current === mine) mine.cleanups.push(fn); else fn(); } }, fail);
      }
    } catch (err) { fail(err); }
    App.emit('route', App.current());
  }
  function activeTab(kind, path) {
    let best = null;
    for (const t of App.navs[kind] || []) {
      const tp = t.route.replace(/^#/, '');
      if ((path === tp || path.startsWith(tp + '/')) && (!best || tp.length > best.len)) best = { id: t.id, len: tp.length };
    }
    return best && best.id;
  }

  // menus: tab routes are a contract for screen groups (change them from your own file: App.navs.resident[3].route = …)
  App.navs = {
    resident: [
      { id: 'home', route: '#/home', icon: 'home', label: 'nav.home' },
      { id: 'videos', route: '#/videos', icon: 'play-circle', label: 'nav.videos' },
      { id: 'call', route: '#/numbers', icon: 'phone', label: 'nav.call' },
      { id: 'message', route: '#/sos', icon: 'chat', label: 'nav.message' },
    ],
    admin: [
      { id: 'dashboard', route: '#/admin', icon: 'home', label: 'nav.admin.dashboard' },
      { id: 'events', route: '#/admin/events', icon: 'folder', label: 'nav.admin.events' },
      { id: 'numbers', route: '#/admin/numbers', icon: 'phone', label: 'nav.admin.numbers' },
      { id: 'videos', route: '#/admin/videos', icon: 'play', label: 'nav.admin.videos' },
      { id: 'messages', route: '#/admin/messages', icon: 'mail', label: 'nav.admin.messages' },
    ],
  };
  App.badges = {}; // 'admin.messages' → the number on the tab
  App.setBadge = function (nav, id, n) {
    App.badges[nav + '.' + id] = n;
    if (!hasDom) return;
    const b = root.document.querySelector(`.tabbar--${nav} [data-tab="${id}"] .tabbar__badge`);
    if (b) { b.textContent = n ? App.fmt.num(n) : ''; b.hidden = !n; }
  };

  // ---------------------------------------------------------------- start
  let started = false;
  let readyPromise = null;
  /** Strings and language — for any page (dev/components.html too). */
  App.ready = function () {
    if (!readyPromise) {
      readyPromise = (async () => {
        try { await App.i18n.load('fa'); } catch (e) { console.error(e); }
        if (App.lang !== 'fa') { try { await App.i18n.load(App.lang); } catch (e) { console.error(e); App.lang = 'fa'; } }
        applyLang();
        netbar();
      })();
    }
    return readyPromise;
  };
  /** Full app start (index.html): strings → who is signed in → the first screen. */
  App.start = async function (opts) {
    if (opts) Object.assign(App.config, opts);
    await App.ready();
    await App.refreshMe();
    started = true;
    root.addEventListener('hashchange', route);
    route();
  };

  // clicks on data-go / data-back / data-lang — for HTML strings without their own handlers
  if (hasDom) {
    root.document.addEventListener('click', (e) => {
      const t = e.target.closest && e.target.closest('[data-go],[data-back],[data-lang]');
      if (!t) return;
      e.preventDefault();
      if (t.hasAttribute('data-lang')) App.setLang(t.getAttribute('data-lang'));
      else if (t.closest('[data-demo-inert]')) return; // demo page: links go nowhere
      else if (t.hasAttribute('data-go')) App.go(t.getAttribute('data-go'));
      else App.back(t.getAttribute('data-back') || undefined);
    });
    applyLang();
  }

  // ---------------------------------------------------------------- toast
  /** App.toast('ذخیره شد', 'ok'|'error'|'info' | {type, ms}) */
  App.toast = function (text, opts) {
    if (!hasDom) return;
    const o = typeof opts === 'string' ? { type: opts } : (opts || {});
    let host = root.document.querySelector('.toasts');
    if (!host) { host = root.document.createElement('div'); host.className = 'toasts'; host.setAttribute('aria-live', 'polite'); root.document.body.appendChild(host); }
    const el = root.document.createElement('div');
    el.className = 'toast toast--' + (o.type || 'info');
    el.setAttribute('role', o.type === 'error' ? 'alert' : 'status');
    el.textContent = text;
    host.appendChild(el);
    const ms = o.ms || (o.type === 'error' ? 5000 : 3000);
    setTimeout(() => { el.classList.add('is-out'); setTimeout(() => el.remove(), 300); }, ms);
    return el;
  };

  // ---------------------------------------------------------------- network: App.online, core events 'online' / 'offline'
  App.online = hasDom && typeof root.navigator.onLine === 'boolean' ? root.navigator.onLine : true;
  /** A thin "بدون اتصال به اینترنت" strip at the top of the column while there is no network. */
  function netbar() {
    if (!hasDom || !root.document.body) return;
    let bar = root.document.querySelector('.netbar');
    if (App.online) {
      if (bar) bar.remove();
      root.document.body.classList.remove('is-offline');
      return;
    }
    if (!bar) {
      bar = root.document.createElement('div');
      bar.className = 'netbar';
      bar.setAttribute('role', 'status');
      bar.setAttribute('aria-live', 'polite');
      root.document.body.prepend(bar);
    }
    const s = App.t('net.offline'); // dictionary not loaded (opened offline) — Dari
    bar.innerHTML = String(App.html`<span class="netbar__dot" aria-hidden="true"></span><span>${s === 'net.offline' ? 'بدون اتصال به اینترنت' : s}</span>`);
    root.document.body.classList.add('is-offline');
  }
  function setOnline(v) {
    if (App.online === v) return;
    App.online = v;
    netbar();
    App.emit(v ? 'online' : 'offline', v);
    if (v) App.live.kick(); // reopen the stream now, do not wait for the browser's pause
  }
  if (hasDom) {
    root.addEventListener('online', () => setOnline(true));
    root.addEventListener('offline', () => setOnline(false));
    App.on('lang', () => { if (!App.online) netbar(); });
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', netbar); else netbar();
  }

  // ---------------------------------------------------------------- device: battery and network
  App.device = {
    /** → {battery: 0–100|null, charging: bool|null, signal: 'strong'|'medium'|'weak'|'none'|null, online, network} */
    async get() {
      const nav = hasDom ? root.navigator : {};
      const out = { battery: null, charging: null, signal: null, online: typeof nav.onLine === 'boolean' ? nav.onLine : null, network: null };
      try {
        if (nav.getBattery) { const b = await nav.getBattery(); out.battery = Math.round(b.level * 100); out.charging = !!b.charging; }
      } catch (e) { /* the browser does not allow it */ }
      const c = nav.connection;
      if (c && c.effectiveType) {
        out.network = c.effectiveType;
        out.signal = { '4g': 'strong', '3g': 'medium', '2g': 'weak', 'slow-2g': 'weak' }[c.effectiveType] || null;
      }
      if (out.online === false) out.signal = 'none';
      return out;
    },
    signalText(signal) { return signal ? App.t('device.signal.' + signal) : App.t('device.unknown'); },
  };

  // ---------------------------------------------------------------- location: App.location
  App.location = {
    saved() { return App.store.get('location'); },
    save(loc) {
      const l = { lat: Number(loc.lat), lon: Number(loc.lon), source: loc.source || 'picked', at: new Date().toISOString() };
      if (loc.accuracy != null) l.accuracy = Math.round(loc.accuracy);
      App.store.set('location', l);
      App.state.location = l;
      App.emit('location', l);
      return l;
    },
    /** Browser GPS only (works on localhost and https). */
    gps(timeout = 8000) {
      return new Promise((resolve, reject) => {
        const g = hasDom && root.navigator.geolocation;
        if (!g || root.isSecureContext === false) { reject(new Error('no-gps')); return; }
        g.getCurrentPosition(
          (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy, source: 'gps' }),
          (e) => reject(e), { enableHighAccuracy: true, timeout, maximumAge: 60000 });
      });
    },
    /** GPS → else saved → else a pick on the map. → {lat, lon, source: 'gps'|'saved'|'picked'} or null (refused). */
    async get(opts = {}) {
      try { return App.location.save(await App.location.gps(opts.timeout)); } catch (e) { /* fall through */ }
      const s = App.location.saved();
      if (s) return Object.assign({}, s, { source: 'saved' });
      if (opts.pick === false) return null;
      return App.location.pick();
    },
    /** Map window: the resident drops a pin. → {lat, lon, source:'picked'} or null. */
    pick(opts = {}) {
      return new Promise((resolve) => {
        const start = opts.center || (App.state.location ? [App.state.location.lat, App.state.location.lon] : App.config.mapCenter);
        let chosen = null, mapH = null;
        const sheet = App.ui.sheet(App.html`
          <p class="muted">${App.t('loc.pickHint')}</p>
          <div class="map map--pick" data-pick-map></div>
          <div class="stack stack--sm">
            <button class="btn btn--primary" type="button" data-pick-ok disabled>${App.icon('check')}<span>${App.t('loc.confirm')}</span></button>
            <button class="btn btn--outline" type="button" data-pick-cancel>${App.t('common.cancel')}</button>
          </div>`, { title: App.t('loc.pickTitle'), onClose: () => { if (mapH) mapH.destroy(); resolve(chosen ? App.location.save(chosen) : null); } });
        const ok = sheet.el.querySelector('[data-pick-ok]');
        mapH = App.map(sheet.el.querySelector('[data-pick-map]'), {
          center: start, zoom: 13, scoped: false,
          onPick(p) { chosen = { lat: p.lat, lon: p.lon, source: 'picked' }; ok.disabled = false; },
        });
        ok.addEventListener('click', () => sheet.close());
        sheet.el.querySelector('[data-pick-cancel]').addEventListener('click', () => { chosen = null; sheet.close(); });
      });
    },
  };

  // ---------------------------------------------------------------- map: App.map (Leaflet + OpenStreetMap)
  const PIN = { accent: '#F04A0C', red: '#E5372B', green: '#22A447', blue: '#1E6FD9', purple: '#7649C4', orange: '#E07F2A' };
  function pinIcon(color) {
    const c = PIN[color] || color || PIN.accent;
    return root.L.divIcon({
      className: 'map-pin', iconSize: [30, 40], iconAnchor: [15, 39], popupAnchor: [0, -34],
      html: `<svg viewBox="0 0 30 40" width="30" height="40" aria-hidden="true"><path d="M15 39s12-12.6 12-23A12 12 0 0 0 3 16c0 10.4 12 23 12 23z" fill="${c}" stroke="#fff" stroke-width="2"/><circle cx="15" cy="16" r="4.6" fill="#fff"/></svg>`,
    });
  }
  /** App.map(el, {center:[lat,lon], zoom, markers:[{lat, lon, color, label}], circle:{lat, lon, radius(m), color}, onPick({lat, lon})})
      → {map, setMarkers, setCircle, setView, fit, destroy}. Cleaned up automatically when leaving the screen. */
  App.map = function (el, opts = {}) {
    if (!hasDom || !root.L) { if (el) el.innerHTML = String(App.html`<div class="map__fallback">${App.t('map.unavailable')}</div>`); return null; }
    const L = root.L;
    el.classList.add('map');
    el.setAttribute('dir', 'ltr');
    const map = L.map(el, { zoomControl: true, attributionControl: true })
      .setView(opts.center || App.config.mapCenter, opts.zoom || App.config.mapZoom);
    map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
    L.tileLayer(App.config.tiles, {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
    }).addTo(map);
    const markers = L.layerGroup().addTo(map);
    // removed: after destroy() nothing touches the map (fast screen changes used to hit a removed map)
    let circle = null, pickMarker = null, removed = false, sizeTimer = 0;
    const h = {
      map,
      setMarkers(list) {
        if (removed) return h;
        markers.clearLayers();
        (list || []).forEach((m) => {
          const mk = L.marker([m.lat, m.lon], { icon: pinIcon(m.color), title: m.label || '' }).addTo(markers);
          if (m.label) mk.bindPopup(App.esc(m.label));
          if (m.onClick) mk.on('click', () => m.onClick(m));
        });
        return h;
      },
      setCircle(c) {
        if (removed) return h;
        if (circle) { circle.remove(); circle = null; }
        if (c) {
          const col = PIN[c.color] || c.color || PIN.orange;
          circle = L.circle([c.lat, c.lon], { radius: c.radius || 2000, color: col, weight: 2, fillColor: col, fillOpacity: 0.15 }).addTo(map);
        }
        return h;
      },
      setView(center, zoom) { if (!removed) map.setView(center, zoom || map.getZoom()); return h; },
      fit() {
        if (removed) return h;
        const b = [];
        markers.eachLayer((l) => b.push(l.getLatLng()));
        if (circle) { const cb = circle.getBounds(); b.push(cb.getNorthEast(), cb.getSouthWest()); }
        if (b.length === 1) map.setView(b[0], 14); else if (b.length) map.fitBounds(L.latLngBounds(b), { padding: [24, 24] });
        return h;
      },
      /** Removes the map once (safe to call again); cancels the deferred resize first. */
      destroy() {
        if (removed) return;
        removed = true;
        clearTimeout(sizeTimer);
        map._animatingZoom = false; // Leaflet 1.9.4: a zoom transition ending after remove() would move the removed panes
        map.remove();
      },
      get removed() { return removed; },
    };
    if (opts.markers) h.setMarkers(opts.markers);
    if (opts.circle) h.setCircle(opts.circle);
    if (opts.onPick) {
      map.on('click', (e) => {
        if (removed) return;
        const p = { lat: Math.round(e.latlng.lat * 1e6) / 1e6, lon: Math.round(e.latlng.lng * 1e6) / 1e6 };
        if (pickMarker) pickMarker.setLatLng(e.latlng); else pickMarker = L.marker(e.latlng, { icon: pinIcon('accent') }).addTo(map);
        opts.onPick(p);
      });
    }
    if (opts.fit) h.fit();
    sizeTimer = setTimeout(() => { sizeTimer = 0; if (!removed) map.invalidateSize(); }, 0);
    if (opts.scoped !== false && current) current.cleanups.push(() => h.destroy());
    return h;
  };

  // ---------------------------------------------------------------- forms
  /** Form values: {name: value}; several checkboxes with the same name → an array. */
  App.formValues = function (form) {
    const out = {};
    const multi = new Set();
    form.querySelectorAll('input[type=checkbox][name]').forEach((i) => {
      if (form.querySelectorAll(`input[type=checkbox][name="${i.name}"]`).length > 1) multi.add(i.name);
    });
    multi.forEach((n) => { out[n] = []; });
    new FormData(form).forEach((v, k) => {
      if (multi.has(k)) out[k].push(v); else out[k] = typeof v === 'string' ? v.trim() : v;
    });
    return out;
  };
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module !== 'undefined' && module.exports) module.exports = globalThis.App;
