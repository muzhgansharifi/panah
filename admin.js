/* Group "admin" — the PANAH (پناه) admin screens (design — Figma frames 14–18):
     #/admin/login (14) · #/admin (dashboard) · #/admin/events (list) · #/admin/events/new (15) · #/admin/numbers (16)
     #/admin/videos (17) · #/admin/messages (18) · #/admin/chat/:id (rescuer chat, styled like 13).
   Only App.* from js/core.js and js/ui.js; strings — i18n/<lang>/admin.json over common ones; styles — css/admin.css (adm-…).
   The number on the "پیام‌ها" tab — open cases with an unread resident message (read = chat opened). */
(function (App) {
  'use strict';
  const h = App.html;
  const ui = App.ui;
  const F = App.fmt;
  const t = (k, v) => App.t(k, v);
  const LOGIN = '#/admin/login';
  const HOME = '#/admin';
  const KABUL_MS = 4.5 * 3600 * 1000;          // Afghanistan: UTC+4:30 all year round
  const SIGNALS = ['strong', 'medium', 'weak', 'none'];
  const HEALTH = {
    epilepsy: 'health.epilepsy', blood_pressure: 'health.bloodPressure', diabetes: 'health.diabetes',
    disability: 'health.disability', heart: 'health.heart', drug_allergy: 'health.allergy', none: 'health.none',
  };
  const SMS_KIND = { otp: 'purple', alert: 'orange', sos: 'sos', other: 'gray' };

  // ------------------------------------------------------------------ helpers
  const alive = (el) => el.isConnected;      // the screen is still there after await
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const round6 = (x) => Math.round(Number(x) * 1e6) / 1e6;
  const isoZ = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
  /** A name in the screen's language: Pashto — *_ps, if present; otherwise Dari. */
  const loc = (fa, ps) => (App.lang === 'ps' && ps ? ps : fa || ps || '');
  /** "۴:۵۹" — minutes:seconds in the language's digits */
  const clock = (s) => {
    const n = Math.max(0, Math.ceil(Number(s) || 0));
    return F.digits(Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0'));
  };
  /** Kabul time for <input type="datetime-local">: '2026-11-06T10:30' */
  const kabulInput = (iso) => {
    const d = F.toDate(iso);
    return isNaN(d) ? '' : new Date(d.getTime() + KABUL_MS).toISOString().slice(0, 16);
  };
  /** '2026-11-06T10:30' (Kabul time) → UTC '2026-11-06T06:00:00Z' or null */
  const fromKabulInput = (v) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(v || ''));
    return m ? isoZ(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) - KABUL_MS) : null;
  };
  /** Message time: today — "۱۰:۳۳", earlier — with the date */
  const msgTime = (iso) => (Date.now() - F.toDate(iso).getTime() > 20 * 3600e3 ? F.dateTime(iso) : F.time(iso));
  const signalText = (s) => (SIGNALS.includes(s) ? t('device.signal.' + s) : String(s));
  function debounce(fn, ms = 300) {
    let tm = null;
    const d = (...a) => { clearTimeout(tm); tm = setTimeout(() => fn(...a), ms); };
    d.cancel = () => clearTimeout(tm);
    return d;
  }
  /** Where to return after signing in: admin screens only. */
  function safeNext(n) {
    const s = String(n || '');
    return s.startsWith('#/admin') && !s.startsWith(LOGIN) ? s : HOME;
  }
  /** A dictionary string with HTML inserted in place of {name}: tHtml('admin.otp.sentTo', 'phone', h`<bdi>…</bdi>`) */
  function tHtml(key, name, safe) {
    const parts = t(key, { [name]: '\u0001' }).split('\u0001');
    return h`${parts[0]}${safe}${parts.slice(1).join('')}`;
  }

  // server error → a string in the screen's language; no string of our own — the server's text (Dari)
  const ERR = {
    missing_login: () => t('admin.err.usernameRequired'),
    wrong_login: (d) => (d.attempts_left != null ? t('admin.err.wrongLogin', { n: d.attempts_left }) : t('admin.err.wrongUser')),
    locked: (d) => t('admin.err.locked', { time: clock(d.retry_after || 300) }),
    wrong_code: (d) => t('admin.err.wrongCode', { n: d.attempts_left || 0 }),
    expired: () => t('admin.err.expired'),
    bad_code: () => t('admin.err.badCode'),
    no_code: () => t('admin.err.noCode'),
    too_soon: (d) => t('admin.err.tooSoon', { n: d.retry_after || 45 }),
    too_many: (d) => t('admin.err.tooMany', { n: Math.max(1, Math.ceil((d.retry_after || 60) / 60)) }),
    no_phone: () => t('admin.err.noPhone'),
    already_ended: () => t('admin.err.alreadyEnded'),
    bad_type: () => t('admin.event.errType'),
    bad_time: () => t('admin.event.errTime'),
    location_required: () => t('admin.event.errPlace'),
    bad_location: () => t('admin.event.errPlace'),
    safe_place_required: () => t('admin.event.errSafe'),
    bad_number: () => t('admin.numbers.errNumber'),
    title_required: () => t('admin.form.errTitle'),
    bad_video_url: () => t('admin.videos.errUrl'),
    sos_closed: () => t('admin.chat.closedNote'),
  };
  function errText(e) {
    const f = e && ERR[e.code];
    if (f) return f(e.data && typeof e.data === 'object' ? e.data : {});
    return (e && e.message) || t('error.generic');
  }

  /** App.api; the admin session has ended (401 / 403 admin_required) — go to sign-in and come back here. */
  async function api(method, path, body) {
    try {
      return await App.api(method, path, body);
    } catch (e) {
      if (e.status === 401 || (e.status === 403 && e.code === 'admin_required')) {
        if (App.state.admin) App.setAdmin(null);
        const here = location.hash || HOME;
        if (!here.startsWith(LOGIN)) App.replace(LOGIN + '?next=' + encodeURIComponent(here));
      }
      throw e;
    }
  }

  async function logout() {
    try { await App.api('POST', '/api/auth/logout'); } catch (e) { /* the session has ended anyway */ }
    App.setAdmin(null);
    App.replace(LOGIN);
  }

  /** "Are you sure?" sheet → Promise<true|false> */
  function confirmBox(text, o = {}) {
    return new Promise((resolve) => {
      let yes = false;
      const s = ui.sheet(h`<p class="adm-confirm__text">${text}</p>
        <div class="adm-confirm__row">
          ${ui.button({ text: o.ok || t('common.confirm'), kind: o.danger ? 'danger' : 'primary', attrs: { 'data-ok': '' } })}
          ${ui.button({ text: t('common.cancel'), kind: 'outline', attrs: { 'data-cancel': '' } })}
        </div>`, { title: o.title || t('admin.confirm'), onClose: () => resolve(yes) });
      s.el.classList.add('theme-admin', 'adm-sheet');
      s.el.querySelector('[data-ok]').addEventListener('click', () => { yes = true; s.close(); });
      s.el.querySelector('[data-cancel]').addEventListener('click', () => s.close());
    });
  }

  /** Map sheet: the admin drops a pin → {lat, lon} or null. The resident's location (App.location) is untouched. */
  function pickOnMap(o = {}) {
    return new Promise((resolve) => {
      let chosen = null, pending = null, mapH = null;
      const s = ui.sheet(h`<p class="muted">${t('admin.event.pickHint')}</p>
        <div class="map map--pick" data-pick-map></div>
        <div class="stack stack--sm">
          ${ui.button({ text: t('loc.confirm'), icon: 'check', disabled: true, attrs: { 'data-ok': '' } })}
          ${ui.button({ text: t('common.cancel'), kind: 'outline', attrs: { 'data-cancel': '' } })}
        </div>`, { title: o.title, onClose: () => { if (mapH) mapH.destroy(); resolve(chosen); } });
      s.el.classList.add('theme-admin', 'adm-sheet');
      const ok = s.el.querySelector('[data-ok]');
      mapH = App.map(s.el.querySelector('[data-pick-map]'), { center: o.center || App.config.mapCenter, zoom: o.zoom || 14, scoped: false, markers: o.markers || [] });
      if (mapH) {
        mapH.map.on('click', (e) => {
          pending = { lat: round6(e.latlng.lat), lon: round6(e.latlng.lng) };
          mapH.setMarkers((o.markers || []).concat([{ lat: pending.lat, lon: pending.lon, color: 'green' }]));
          ok.disabled = false;
        });
      }
      ok.addEventListener('click', () => { chosen = pending; s.close(); });
      s.el.querySelector('[data-cancel]').addEventListener('click', () => s.close());
    });
  }

  /** Sheet with a form (16, 17). submit(values, form) → Promise; a field error — fieldErr(name, text) or a server code
      from fieldFor {code: field name}; anything else — as a line under the form. */
  const fieldErr = (field, message) => Object.assign(new Error(message), { field });
  function formSheet(o) {
    const s = ui.sheet(h`<form class="adm-sheet-form" novalidate autocomplete="off">${o.fields}
        <p class="adm-form__error" role="alert" hidden></p>
        ${ui.button({ text: t('common.save'), type: 'submit', icon: 'check' })}</form>`, { title: o.title });
    s.el.classList.add('theme-admin', 'adm-sheet');
    const form = s.el.querySelector('form');
    const box = form.querySelector('.adm-form__error');
    const btn = form.querySelector('[type="submit"]');
    const input = (name) => form.elements.namedItem(name);
    form.addEventListener('input', (e) => { if (e.target.classList.contains('input')) ui.fieldError(e.target, null); });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      box.hidden = true;
      btn.disabled = true;
      try {
        await o.submit(App.formValues(form), form);
        s.close();
      } catch (x) {
        const name = x && (x.field || (o.fieldFor || {})[x.code]);
        if (name && input(name)) { ui.fieldError(input(name), x.field ? x.message : errText(x)); input(name).focus(); }
        else { box.textContent = errText(x); box.hidden = false; }
      } finally { btn.disabled = false; }
    });
    return s;
  }

  // ------------------------------------------------------------------ read state and the number on the "پیام‌ها" tab
  const SEEN = 'admin.seen';                   // {case id: id of the resident's last read message}
  const seenMap = () => { const s = App.store.get(SEEN, {}); return s && typeof s === 'object' ? s : {}; };
  const isUnread = (card, seen) => !!(card.last_message && card.case.status === 'open' && card.last_message.id > (seen[card.case.id] || 0));
  function markSeen(caseId, msgId) {
    const s = seenMap();
    if (!msgId || (s[caseId] || 0) >= msgId) return;
    s[caseId] = msgId;
    App.store.set(SEEN, s);
    Watch.bump();
  }
  const Watch = {
    offs: null,
    timer: null,
    toasted: new Set(),                        // ids of cases already announced with a toast
    start() {
      if (this.offs) return;
      this.offs = [
        App.live.on('sos_case', (d) => {
          this.bump();
          const cur = App.current();
          if (d && d.is_new && d.case && !this.toasted.has(d.case.id)) {
            this.toasted.add(d.case.id);
            if (!(cur && cur.name === 'admin-messages')) {
              App.toast(t('admin.messages.newToast', { name: (d.user && d.user.name) || t('admin.messages.anon') }), 'info');
            }
          }
        }, { global: true }),
        App.live.on('sos_message', (d) => { if (d && d.message && d.message.sender === 'user') this.bump(); }, { global: true }),
      ];
      this.refresh();
    },
    stop() {
      if (this.offs) { this.offs.forEach((off) => off()); this.offs = null; }
      clearTimeout(this.timer);
      if (App.badges['admin.messages']) App.setBadge('admin', 'messages', 0);
    },
    bump() { clearTimeout(this.timer); this.timer = setTimeout(() => this.refresh(), 400); },
    async refresh() {
      if (!App.state.admin) return;
      try {
        const r = await App.api('GET', '/api/admin/sos?status=open');
        const cases = r.cases || [];
        const seen = seenMap();
        const open = new Set(cases.map((c) => String(c.case.id)));
        let pruned = false;
        Object.keys(seen).forEach((k) => { if (!open.has(k)) { delete seen[k]; pruned = true; } });
        if (pruned) App.store.set(SEEN, seen);
        App.setBadge('admin', 'messages', cases.filter((c) => isUnread(c, seen)).length);
      } catch (e) { /* no network or signed out — the number stays */ }
    },
  };
  App.on('route', () => { if (App.state.admin) Watch.start(); else Watch.stop(); });
  App.on('admin', (a) => { if (a) Watch.start(); else Watch.stop(); });

  // ================================================================== 14 · admin sign-in
  App.screen('admin-login', {
    route: LOGIN, theme: 'admin', title: 'admin.login.title',
    render(params, el) {
      const next = safeNext(params.next);
      if (App.state.admin) { App.replace(next); return undefined; }
      el.classList.add('adm', 'adm-login');
      let username = '';
      const timers = new Set();
      const every = (fn, ms) => { const id = setInterval(fn, ms); timers.add(id); return id; };
      const stop = (id) => { clearInterval(id); timers.delete(id); };
      el.innerHTML = String(h`<div class="adm-login__head">
          ${ui.logo({ kind: 'admin', size: 108 })}
          <h1 class="adm-login__title">${t('admin.login.title')}</h1>
          <p class="adm-login__sub" data-sub>${t('admin.login.subtitle')}</p>
        </div>
        <div class="adm-login__body" data-body></div>`);
      const body = el.querySelector('[data-body]');
      const sub = el.querySelector('[data-sub]');
      const show = (box, text) => { box.textContent = text || ''; box.hidden = !text; };
      const userField = () => ui.field({
        name: 'username', value: username, inputIcon: 'user', placeholder: t('admin.login.username'), autocomplete: 'username',
        attrs: { 'aria-label': t('admin.login.username'), autocapitalize: 'none', spellcheck: 'false', maxlength: '100' },
      });
      const or = h`<div class="or or--accent"><span>${t('common.or')}</span></div>`;
      function done(admin) {
        timers.forEach((id) => clearInterval(id));
        App.state.user = null;               // the resident's session (if any) is replaced by the admin's
        App.setAdmin(admin);
        App.replace(next);
      }

      // --- by password
      function passwordMode() {
        timers.forEach((id) => stop(id));
        sub.textContent = t('admin.login.subtitle');
        body.innerHTML = String(h`<form class="adm-login__form" novalidate data-form="password">
            ${userField()}
            ${ui.field({ name: 'password', type: 'password', inputIcon: 'lock', placeholder: t('admin.login.password'), autocomplete: 'current-password',
              end: ui.iconButton({ icon: 'eye-off', label: t('admin.login.showPassword'), attrs: { 'data-eye': '', 'aria-pressed': 'false' } }),
              attrs: { 'aria-label': t('admin.login.password'), maxlength: '200' } })}
            <p class="adm-login__error" role="alert" hidden></p>
            ${ui.button({ text: t('admin.login.submit'), type: 'submit', cls: 'adm-login__submit' })}
          </form>
          ${or}
          ${ui.button({ text: t('admin.login.otp'), kind: 'outline-accent', icon: 'lock', cls: 'adm-login__alt', attrs: { 'data-otp': '' } })}
          <button class="adm-login__forgot" type="button" data-forgot>${t('admin.login.forgotA')} <b>${t('admin.login.forgotB')}</b></button>`);
        const form = body.querySelector('form');
        const user = form.elements.namedItem('username');
        const pass = form.elements.namedItem('password');
        const box = form.querySelector('.adm-login__error');
        const btn = form.querySelector('[type="submit"]');
        const eye = form.querySelector('[data-eye]');
        let lock = null;
        eye.addEventListener('click', () => {
          const open = pass.type === 'password';
          pass.type = open ? 'text' : 'password';
          eye.setAttribute('aria-pressed', String(open));
          eye.innerHTML = String(App.icon(open ? 'eye' : 'eye-off'));
        });
        user.addEventListener('input', () => { username = user.value.trim(); ui.fieldError(user, null); });
        pass.addEventListener('input', () => ui.fieldError(pass, null));
        // 429 locked: a clear message and a countdown; the button stays disabled until the lock ends
        function locked(seconds) {
          let left = Math.max(1, Math.ceil(seconds));
          if (lock) stop(lock);
          btn.disabled = true;
          const tick = () => {
            if (left <= 0) { stop(lock); lock = null; btn.disabled = false; show(box, ''); return; }
            show(box, t('admin.err.locked', { time: clock(left) }));
            left -= 1;
          };
          tick();
          lock = every(tick, 1000);
        }
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          if (lock) return;
          show(box, '');
          username = user.value.trim();
          const password = pass.value;
          ui.fieldError(user, username ? null : t('admin.err.usernameRequired'));
          ui.fieldError(pass, password ? null : t('admin.err.passwordRequired'));
          if (!username) { user.focus(); return; }
          if (!password) { pass.focus(); return; }
          btn.disabled = true;
          let ok = false;
          try {
            const r = await App.api('POST', '/api/admin/login', { username, password });
            ok = true;
            done(r.admin);
          } catch (err) {
            if (!alive(el)) return;
            if (err.code === 'locked') { locked((err.data && err.data.retry_after) || 300); return; }
            show(box, errText(err));
            if (err.code === 'wrong_login') pass.select();
          } finally {
            if (!ok && !lock) btn.disabled = false;
          }
        });
        body.querySelector('[data-otp]').addEventListener('click', otpMode);
        body.querySelector('[data-forgot]').addEventListener('click', () => App.toast(t('admin.login.forgotToast'), 'info'));
      }

      // --- by one-time code (SMS to the admin's phone; test mode — the code is shown in a strip)
      function otpMode() {
        timers.forEach((id) => stop(id));
        sub.textContent = t('admin.otp.hint');
        body.innerHTML = String(h`<form class="adm-login__form" novalidate data-form="otp-send">
            ${userField()}
            <p class="adm-login__error" role="alert" hidden></p>
            ${ui.button({ text: t('admin.otp.send'), type: 'submit', icon: 'send' })}
          </form>
          <form class="adm-login__form" novalidate data-form="otp-verify" hidden>
            <p class="adm-otp__sent" data-sent hidden></p>
            <p class="adm-otp__test" data-test hidden></p>
            ${ui.field({ name: 'code', inputIcon: 'lock', placeholder: t('admin.otp.codePlaceholder'), inputmode: 'numeric', autocomplete: 'one-time-code', dir: 'ltr',
              cls: 'adm-otp__code', attrs: { maxlength: '6', 'aria-label': t('admin.otp.code') } })}
            <p class="adm-login__error" role="alert" hidden></p>
            ${ui.button({ text: t('admin.otp.verify'), type: 'submit' })}
            ${ui.button({ text: t('admin.otp.resend'), kind: 'ghost', attrs: { 'data-resend': '' } })}
          </form>
          ${or}
          ${ui.button({ text: t('admin.otp.back'), kind: 'outline-accent', icon: 'user', cls: 'adm-login__alt', attrs: { 'data-password': '' } })}`);
        const send = body.querySelector('[data-form="otp-send"]');
        const verify = body.querySelector('[data-form="otp-verify"]');
        const user = send.elements.namedItem('username');
        const code = verify.elements.namedItem('code');
        const resend = verify.querySelector('[data-resend]');
        const resendText = resend.querySelector('span');
        let wait = null;
        function countdown(sec) {
          let left = Math.max(0, Math.ceil(sec));
          if (wait) stop(wait);
          const tick = () => {
            if (left <= 0) { stop(wait); wait = null; resend.disabled = false; resendText.textContent = t('admin.otp.resend'); return; }
            resend.disabled = true;
            resendText.textContent = t('admin.otp.resendIn', { n: left });
            left -= 1;
          };
          tick();
          wait = every(tick, 1000);
        }
        async function ask(box, btn) {
          username = user.value.trim();
          if (!username) { ui.fieldError(user, t('admin.err.usernameRequired')); user.focus(); return; }
          ui.fieldError(user, null);
          show(box, '');
          btn.disabled = true;
          try {
            const r = await App.api('POST', '/api/admin/otp', { username });
            if (!alive(el)) return;
            send.hidden = true;
            verify.hidden = false;
            show(verify.querySelector('.adm-login__error'), '');
            // The code is never shown on screen, even if the server still sends test_code: knowing the username
            // must not be enough to sign in. Test mode — a hint where the SMS is (data/sms.log); a real SMS
            // provider (test_mode: false) — the masked number instead.
            const testMode = r.test_mode !== false;
            const sentLine = verify.querySelector('[data-sent]');
            sentLine.hidden = testMode || !r.phone;
            if (!sentLine.hidden) sentLine.innerHTML = String(tHtml('admin.otp.sentTo', 'phone', h`<bdi class="adm-ltr" dir="ltr">${F.digits(r.phone)}</bdi>`));
            const hint = verify.querySelector('[data-test]');
            hint.hidden = !testMode;
            hint.innerHTML = String(tHtml('admin.otp.testHint', 'path', h`<bdi class="adm-ltr" dir="ltr">data/sms.log</bdi>`));
            code.value = '';
            countdown(r.resend_in || 45);
            code.focus();
          } catch (err) {
            if (!alive(el)) return;
            show(box, errText(err));
            if (err.code === 'too_soon' && !verify.hidden) countdown((err.data && err.data.retry_after) || 45);
          } finally {
            if (btn !== resend || !wait) btn.disabled = false;
          }
        }
        user.addEventListener('input', () => ui.fieldError(user, null));
        code.addEventListener('input', () => ui.fieldError(code, null));
        send.addEventListener('submit', (e) => { e.preventDefault(); ask(send.querySelector('.adm-login__error'), send.querySelector('[type="submit"]')); });
        resend.addEventListener('click', () => ask(verify.querySelector('.adm-login__error'), resend));
        verify.addEventListener('submit', async (e) => {
          e.preventDefault();
          const box = verify.querySelector('.adm-login__error');
          show(box, '');
          const value = F.latin(code.value).replace(/\D/g, '');
          if (value.length !== 6) { ui.fieldError(code, t('admin.err.badCode')); code.focus(); return; }
          const btn = verify.querySelector('[type="submit"]');
          btn.disabled = true;
          let ok = false;
          try {
            const r = await App.api('POST', '/api/admin/otp/verify', { username, code: value });
            ok = true;
            done(r.admin);
          } catch (err) {
            if (alive(el)) { show(box, errText(err)); code.select(); }
          } finally {
            if (!ok) btn.disabled = false;
          }
        });
        body.querySelector('[data-password]').addEventListener('click', passwordMode);
        user.focus();
      }

      passwordMode();
      return () => timers.forEach((id) => clearInterval(id));
    },
  });

  // ================================================================== dashboard (not in Figma — styled like 15–18)
  function statTile(o) {
    const inner = h`<span class="adm-stat__icon">${App.icon(o.icon, { size: 22 })}</span>
      <b class="adm-stat__num">${o.num == null ? '—' : F.num(o.num)}</b><span class="adm-stat__label">${o.label}</span>`;
    return o.go
      ? h`<a class="adm-stat adm-stat--${o.cls}" href="${o.go}" data-go="${o.go}" data-stat="${o.cls}">${inner}</a>`
      : h`<div class="adm-stat adm-stat--${o.cls}" data-stat="${o.cls}">${inner}</div>`;
  }
  App.screen('admin-dashboard', {
    route: HOME, nav: 'admin', auth: 'admin', title: 'admin.dash.title',
    async render(params, el) {
      el.classList.add('adm', 'adm-dash');
      const me = App.state.admin || {};
      el.innerHTML = String(h`<div class="adm-head">
          <div class="brand-bar">${ui.brand()}</div>
          ${ui.pageTitle({ title: t('admin.dash.title'), sub: t('admin.dash.hello', { name: me.name || me.username || '' }) })}
          <button class="btn-icon adm-head__end" type="button" data-logout aria-label="${t('admin.dash.logout')}" title="${t('admin.dash.logout')}">${App.icon('logout')}</button>
        </div>
        <div class="adm-stats" data-stats>${ui.loading()}</div>
        ${ui.button({ text: t('admin.events.add'), icon: 'plus', go: '#/admin/events/new', cls: 'adm-dash__add' })}
        <section class="adm-panel">
          <h2 class="adm-panel__title">${App.icon('map', { size: 20 })}<span>${t('admin.dash.map')}</span></h2>
          <div class="map adm-dash__map" data-map></div>
          <div class="adm-legend">
            <span><i class="adm-legend__dot adm-legend__dot--quake"></i>${t('admin.dash.legendQuake')}</span>
            <span><i class="adm-legend__dot adm-legend__dot--flood"></i>${t('admin.dash.legendFlood')}</span>
            <span><i class="adm-legend__dot adm-legend__dot--sos"></i>${t('admin.dash.legendSos')}</span>
          </div>
          <p class="adm-note" data-map-empty hidden>${t('admin.dash.mapEmpty')}</p>
        </section>
        <section class="adm-panel" data-usgs hidden></section>
        <section class="adm-panel" data-sms></section>
        ${ui.langSwitch()}`);
      el.querySelector('[data-logout]').addEventListener('click', logout);
      const mapH = App.map(el.querySelector('[data-map]'), { center: App.config.mapCenter, zoom: 11 });
      const L = window.L;
      const zones = mapH && L ? L.layerGroup().addTo(mapH.map) : null;

      function drawStats(s) {
        el.querySelector('[data-stats]').innerHTML = String(h`
          ${statTile({ icon: 'alert', num: s.active_alerts, label: t('admin.dash.activeAlerts'), go: '#/admin/events', cls: 'alert' })}
          ${statTile({ icon: 'siren', num: s.sos ? s.sos.open : null, label: t('admin.dash.openSos'), go: '#/admin/messages', cls: 'sos' })}
          ${statTile({ icon: 'users', num: s.users, label: t('admin.dash.users'), cls: 'users' })}
          ${statTile({ icon: 'mail', num: s.sms ? s.sms.today : null, label: t('admin.dash.smsToday'), cls: 'sms' })}`);
      }
      function drawMap(alerts, cases) {
        if (!mapH || !zones) return;
        zones.clearLayers();
        const bounds = [];
        (alerts || []).forEach((a) => {
          const col = a.type === 'flood' ? '#1E6FD9' : '#E07F2A';
          const c = L.circle([a.lat, a.lon], { radius: (a.radius_km || 2) * 1000, color: col, weight: 2, fillColor: col, fillOpacity: 0.15 }).addTo(zones);
          bounds.push(c.getBounds());
        });
        const markers = (alerts || []).map((a) => ({
          lat: a.lat, lon: a.lon, color: a.type === 'flood' ? 'blue' : 'orange', label: `${loc(a.title_fa, a.title_ps)} — ${a.place || ''}`,
        })).concat((cases || []).filter((c) => c.case.lat != null && c.case.lon != null).map((c) => ({
          lat: c.case.lat, lon: c.case.lon, color: 'red', label: (c.user && c.user.name) || t('admin.messages.anon'),
          onClick: () => App.go('#/admin/chat/' + c.case.id),
        })));
        mapH.setMarkers(markers);
        markers.forEach((m) => bounds.push(L.latLngBounds([[m.lat, m.lon], [m.lat, m.lon]])));
        el.querySelector('[data-map-empty]').hidden = bounds.length > 0;
        if (bounds.length) {
          const all = bounds[0];
          bounds.slice(1).forEach((b) => all.extend(b));
          mapH.map.fitBounds(all, { padding: [28, 28], maxZoom: 14 });
        } else mapH.setView(App.config.mapCenter, 11);
      }
      function drawUsgs(u) {
        const box = el.querySelector('[data-usgs]');
        box.hidden = !u;
        if (!u) return;
        const state = !u.running ? ['usgsOff', 'gray'] : u.ok === false ? ['usgsError', 'red'] : ['usgsOn', 'green'];
        const ev = u.last_event;
        box.innerHTML = String(h`<div class="adm-panel__head">
            <h2 class="adm-panel__title">${App.icon('quake', { size: 20 })}<span>${t('admin.dash.usgs')}</span></h2>${ui.badge(t('admin.dash.' + state[0]), state[1])}</div>
          <p class="adm-note">${t('admin.dash.usgsEvery')}</p>
          <p class="adm-line">${App.icon('clock', { size: 16 })}<span>${u.last_poll ? t('admin.dash.lastCheck', { t: F.ago(u.last_poll) }) : t('admin.dash.never')}</span></p>
          ${u.ok === false && u.last_error ? h`<p class="adm-line adm-line--err">${App.icon('alert', { size: 16 })}<bdi>${u.last_error}</bdi></p>` : ''}
          <div class="adm-quake">${ev
            ? h`<span class="adm-quake__label">${t('admin.dash.lastQuake')}</span>
              ${ev.magnitude != null ? h`<b class="adm-quake__mag" dir="ltr">M${F.num(ev.magnitude, 1)}</b>` : ''}
              <bdi class="adm-quake__place">${ev.place || ''}</bdi>
              <span class="adm-quake__time">${F.dateTime(ev.starts_at)}</span>`
            : h`<span class="muted">${t('admin.dash.noQuake')}</span>`}</div>
          <p class="adm-note">${t('admin.dash.quakesTotal', { n: u.new_total || 0 })}</p>`);
      }
      function drawSms(r) {
        const items = (r && r.items) || [];
        const test = !r || r.test_mode !== false;
        el.querySelector('[data-sms]').innerHTML = String(h`<div class="adm-panel__head">
            <h2 class="adm-panel__title">${App.icon('mail', { size: 20 })}<span>${t('admin.dash.sms')}</span></h2>${test ? ui.badge(t('admin.dash.smsTest'), 'purple') : ''}</div>
          <p class="adm-note">${t('admin.dash.smsCounts', { today: (r && r.today) || 0, total: (r && r.total) || 0 })}</p>
          ${items.length ? h`<ul class="adm-sms">${items.map((s) => h`<li class="adm-sms__item">
              <div class="adm-sms__top">${ui.badge(t('admin.sms.kind.' + (SMS_KIND[s.kind] ? s.kind : 'other')), SMS_KIND[s.kind] || 'gray')}
                <span class="adm-sms__to">${App.phone.display(s.phone)}</span><time datetime="${s.created_at}">${F.ago(s.created_at)}</time></div>
              <p class="adm-sms__text" dir="rtl">${s.text}</p></li>`)}</ul>`
            : h`<p class="muted">${t('admin.dash.smsEmpty')}</p>`}
          ${test ? h`<p class="adm-note">${t('admin.dash.smsNote')}</p>` : ''}`);
      }
      const load = async () => {
        const extra = (path) => api('GET', path).catch(() => null);   // the dashboard shows even without them
        const [stats, al, sos, sms] = await Promise.all([
          api('GET', '/api/admin/stats'),
          extra('/api/admin/alerts?status=active'),
          extra('/api/admin/sos?status=open'),
          extra('/api/admin/sms?limit=6'),
        ]);
        if (!alive(el)) return;
        drawStats(stats);
        drawMap(al && al.alerts, sos && sos.cases);
        drawUsgs(stats.usgs);
        drawSms(sms);
      };
      await load();
      if (!alive(el)) return undefined;
      const again = debounce(() => load().catch(() => {}), 500);
      ['alert', 'alert_end', 'sos_case'].forEach((ev) => App.live.on(ev, again));
      const timer = setInterval(again, 30000);   // sign-in code SMS raise no events — hence every 30 s
      return () => { clearInterval(timer); again.cancel(); };
    },
  });

  // ================================================================== event list (not in Figma)
  function eventCard(a, isNew) {
    const active = a.status === 'active';
    // USGS history (first poll = 30 days of quakes) is "past" at once: its ended_at is the load time, not the real one
    const history = !active && a.source === 'usgs' && (!a.ended_at || a.ended_at === a.created_at);
    const title = loc(a.title_fa, a.title_ps) || t('alert.type.' + a.type);
    return h`<article class="adm-event${active ? ' is-active' : ''}${isNew ? ' is-new' : ''}" data-id="${a.id}">
      <div class="adm-event__top">
        <span class="adm-event__icon adm-event__icon--${a.type}">${App.icon(a.type === 'flood' ? 'flood' : 'quake')}</span>
        <div class="adm-event__head">
          <h3 class="adm-event__title">${title}${a.magnitude != null ? h` <span class="adm-event__mag" dir="ltr">M${F.num(a.magnitude, 1)}</span>` : ''}</h3>
          <p class="adm-event__place"><bdi>${a.place || F.coord(a.lat, a.lon)}</bdi></p>
        </div>
        ${ui.badge(t(active ? 'admin.events.statusActive' : history ? 'admin.events.usgsHistory' : 'admin.events.statusEnded'), active ? 'green' : 'gray')}
      </div>
      <ul class="adm-event__meta">
        <li>${App.icon('clock', { size: 16 })}<span>${t(history ? 'admin.events.when' : 'admin.events.start', { t: F.dateTime(a.starts_at) })}</span></li>
        ${!active && !history && a.ended_at ? h`<li>${App.icon('check', { size: 16 })}<span>${t('admin.events.endedAt', { t: F.dateTime(a.ended_at) })}</span></li>` : ''}
        ${history ? '' : h`<li>${App.icon('info', { size: 16 })}<span>${t('admin.events.source', { s: t('admin.events.source.' + (a.source === 'usgs' ? 'usgs' : 'admin')) })}</span></li>`}
        ${a.safe_place ? h`<li>${App.icon('shield', { size: 16 })}<span>${t('admin.events.safe', { p: a.safe_place })}</span></li>` : ''}
        ${a.source !== 'usgs' || a.notified ? h`<li class="adm-event__sent">${App.icon('users', { size: 16 })}<span>${t('admin.events.notified', { n: a.notified || 0, sms: a.sms || 0 })}</span></li>` : ''}
      </ul>
      ${a.image ? h`<img class="adm-event__img" src="${a.image}" alt="" loading="lazy">` : ''}
      ${a.url || active ? h`<div class="adm-event__foot">
        ${a.url ? h`<a class="adm-link" href="${a.url}" target="_blank" rel="noopener">${App.icon('globe', { size: 16 })}<span>${t('admin.events.usgsLink')}</span></a>` : ''}
        ${active ? ui.button({ text: t('admin.events.end'), kind: 'outline', small: true, block: false, cls: 'adm-event__end', attrs: { 'data-end': a.id } }) : ''}
      </div>` : ''}
    </article>`;
  }
  App.screen('admin-events', {
    route: '#/admin/events', nav: 'admin', auth: 'admin', title: 'admin.events.title',
    async render(params, el) {
      el.classList.add('adm', 'adm-events');
      const fresh = Number(params.new) || null;   // the just-published one is highlighted
      el.innerHTML = String(h`${ui.pageTitle({ title: t('admin.events.title'), sub: t('admin.events.sub') })}
        ${ui.addTile({ text: t('admin.events.add'), go: '#/admin/events/new', id: 'adm-add-event' })}
        <div class="adm-list" data-list>${ui.loading()}</div>`);
      const list = el.querySelector('[data-list]');
      let alerts = [];
      let pastShown = 20;
      function draw() {
        const act = alerts.filter((a) => a.status === 'active');
        const past = alerts.filter((a) => a.status !== 'active');
        list.innerHTML = String(h`
          <h2 class="adm-section">${t('admin.events.active')} ${ui.badge(F.num(act.length), act.length ? 'green' : 'gray')}</h2>
          ${act.length ? act.map((a) => eventCard(a, a.id === fresh)) : h`<p class="adm-empty">${t('admin.events.noActive')}</p>`}
          <h2 class="adm-section">${t('admin.events.past')} ${ui.badge(F.num(past.length), 'gray')}</h2>
          ${past.length ? past.slice(0, pastShown).map((a) => eventCard(a, a.id === fresh)) : h`<p class="adm-empty">${t('admin.events.noPast')}</p>`}
          ${past.length > pastShown ? ui.button({ text: t('admin.events.more'), kind: 'outline', small: true, attrs: { 'data-more': '' } }) : ''}`);
      }
      const load = async () => {
        const r = await api('GET', '/api/admin/alerts');
        alerts = r.alerts || [];
        if (alive(el)) draw();
      };
      list.addEventListener('click', async (e) => {
        if (e.target.closest('[data-more]')) { pastShown += 20; draw(); return; }
        const end = e.target.closest('[data-end]');
        if (!end) return;
        const id = Number(end.getAttribute('data-end'));
        if (!(await confirmBox(t('admin.events.endConfirm'), { ok: t('admin.events.end'), danger: true }))) return;
        end.disabled = true;
        try {
          await api('POST', `/api/admin/alerts/${id}/end`);
          App.toast(t('admin.events.endedToast'), 'ok');
        } catch (err) {
          App.toast(errText(err), 'error');
        }
        if (alive(el)) load().catch((err) => App.toast(errText(err), 'error'));
      });
      await load();
      if (!alive(el)) return undefined;
      if (fresh) {
        const card = list.querySelector(`[data-id="${fresh}"]`);
        if (card) card.scrollIntoView({ block: 'center' });
      }
      const again = debounce(() => load().catch(() => {}), 400);
      App.live.on('alert', again);
      App.live.on('alert_end', again);
      return again.cancel;
    },
  });

  // ================================================================== 15 · new event
  /** "Start time" sheet: quick options and a custom time (Kabul time) → UTC ISO or null */
  function pickTime(current) {
    return new Promise((resolve) => {
      let value = null;
      const quick = [0, 15, 30, 60, 180];
      const label = (m) => (m === 0 ? t('admin.event.now') : m < 60 ? t('fmt.minutesAgo', { n: m }) : t('fmt.hoursAgo', { n: m / 60 }));
      const s = ui.sheet(h`<div class="adm-chips">${quick.map((m) => h`<button class="adm-chip" type="button" data-ago="${m}">${App.icon('clock', { size: 16 })}<span>${label(m)}</span></button>`)}</div>
          <label class="field__label" for="adm-dt">${t('admin.event.custom')}</label>
          <input class="input" id="adm-dt" type="datetime-local" dir="ltr" value="${kabulInput(current || isoZ(Date.now()))}">
          <p class="adm-note" data-solar></p>
          ${ui.button({ text: t('common.confirm'), icon: 'check', attrs: { 'data-ok': '' } })}`,
      { title: t('admin.event.start'), onClose: () => resolve(value) });
      s.el.classList.add('theme-admin', 'adm-sheet');
      const input = s.el.querySelector('#adm-dt');
      const solar = s.el.querySelector('[data-solar]');
      const preview = () => { const v = fromKabulInput(input.value); solar.textContent = v ? `${F.dateTime(v)} (${t('admin.event.kabul')})` : ''; };
      input.addEventListener('input', preview);
      preview();
      s.el.querySelectorAll('[data-ago]').forEach((b) => b.addEventListener('click', () => {
        value = isoZ(Date.now() - Number(b.getAttribute('data-ago')) * 60000);
        s.close();
      }));
      s.el.querySelector('[data-ok]').addEventListener('click', () => { value = fromKabulInput(input.value); s.close(); });
    });
  }
  /** Place name for a point (GET /api/geo/name → cached Nominatim): after a pause, not at once; stale answers dropped */
  function placeNamer(el, ms = 900) {
    let timer = null, seq = 0;
    const f = (lat, lon, apply) => {
      clearTimeout(timer);
      const my = ++seq;
      timer = setTimeout(async () => {
        try {
          const r = await api('GET', `/api/geo/name?lat=${lat}&lon=${lon}&lang=${App.lang}`);
          if (my === seq && alive(el) && r && r.name) apply(r.name);
        } catch (e) { /* no name — the server will label the place itself */ }
      }, ms);
    };
    f.cancel = () => { clearTimeout(timer); seq += 1; };
    return f;
  }
  App.screen('admin-event-new', {
    route: '#/admin/events/new', nav: 'admin', auth: 'admin', title: 'admin.event.title',
    render(params, el) {
      el.classList.add('adm', 'adm-new');
      const st = { starts_at: null, lat: null, lon: null, placeAuto: false, safe_lat: null, safe_lon: null, safeAuto: false, safeId: null, image: null };
      const req = h`<span class="field__req" aria-hidden="true">*</span> `;
      el.innerHTML = String(h`${ui.pageTitle({ title: t('admin.event.title'), sub: t('admin.event.subtitle') })}
        <form class="adm-form" novalidate autocomplete="off">
          <label class="adm-image" data-image>
            <input class="adm-image__file" type="file" accept="image/png,image/jpeg,image/webp" data-file>
            <img class="adm-image__img" alt="" hidden data-image-img>
            <span class="adm-image__text" data-image-text>${t('admin.event.addImage')}</span>
            <button class="adm-image__del" type="button" data-image-del hidden aria-label="${t('admin.event.imageRemove')}">${App.icon('close', { size: 18 })}</button>
          </label>
          ${ui.field({ label: t('admin.event.type'), required: true, loud: true, lg: true, name: 'type', id: 'adm-type', placeholder: t('admin.event.typePlaceholder'),
            options: [{ value: 'earthquake', label: t('alert.type.earthquake') }, { value: 'flood', label: t('alert.type.flood') }] })}
          <div class="field field--loud">
            <label class="field__label" for="adm-time">${req}<span>${t('admin.event.start')}</span></label>
            <button class="input input--lg adm-pick" type="button" id="adm-time" data-time><span class="adm-pick__text" data-time-text>${t('admin.event.startPlaceholder')}</span>${App.icon('calendar')}</button>
            <p class="field__error" hidden></p>
          </div>
          <div class="field field--loud" data-where>
            <label class="field__label" for="adm-place">${req}<span>${t('admin.event.location')}</span></label>
            <div class="input-wrap adm-search">
              <input class="input input--lg" id="adm-place" name="place" placeholder="${t('admin.event.locationPlaceholder')}" enterkeyhint="search" maxlength="200">
              <span class="input-wrap__end"><button class="btn-icon" type="button" data-search aria-label="${t('common.search')}">${App.icon('search')}</button></span>
            </div>
            <div class="adm-results" data-results hidden></div>
            <div class="map adm-new__map" data-map></div>
            <p class="adm-new__point" data-point>${App.icon('pin', { size: 18 })}<span>${t('admin.event.mapHint')}</span></p>
            <p class="field__error" hidden></p>
          </div>
          <div class="field field--loud" data-safe>
            <label class="field__label" for="adm-safe">${req}<span>${t('admin.event.safePlace')}</span></label>
            <input class="input input--lg" id="adm-safe" name="safe_place" placeholder="${t('admin.event.safePlacePlaceholder')}" maxlength="200">
            <p class="adm-new__label">${t('admin.event.safeNear')}</p>
            <div class="adm-chips" data-chips></div>
            <p class="adm-new__point is-set" data-safe-point hidden>${App.icon('shield', { size: 18 })}<span></span></p>
            <p class="field__error" hidden></p>
          </div>
          <p class="adm-note adm-note--icon">${App.icon('info', { size: 18 })}<span>${t('admin.event.radiusNote')}</span></p>
          ${ui.button({ text: t('admin.event.publish'), type: 'submit', cls: 'adm-new__publish' })}
        </form>`);
      const form = el.querySelector('form');
      const typeSel = el.querySelector('#adm-type');
      const timeBtn = el.querySelector('[data-time]');
      const timeText = el.querySelector('[data-time-text]');
      const placeInput = el.querySelector('#adm-place');
      const searchBtn = el.querySelector('[data-search]');
      const results = el.querySelector('[data-results]');
      const pointLine = el.querySelector('[data-point]');
      const safeInput = el.querySelector('#adm-safe');
      const chips = el.querySelector('[data-chips]');
      const safeLine = el.querySelector('[data-safe-point]');
      const publish = form.querySelector('[type="submit"]');
      const namePlace = placeNamer(el);
      const nameSafe = placeNamer(el);
      let found = [];
      let places = [];

      // --- map: the event point (red) with a 2 km circle and the safe place point (green)
      const mapH = App.map(el.querySelector('[data-map]'), { center: App.config.mapCenter, zoom: 12 });
      if (mapH) mapH.map.on('click', (e) => setPoint(round6(e.latlng.lat), round6(e.latlng.lng), { fromMap: true }));
      function drawMap() {
        if (!mapH) return;
        const mk = [];
        if (st.lat != null) mk.push({ lat: st.lat, lon: st.lon, color: 'red', label: placeInput.value.trim() || t('admin.event.location') });
        if (st.safe_lat != null) mk.push({ lat: st.safe_lat, lon: st.safe_lon, color: 'green', label: safeInput.value.trim() || t('admin.event.safePlace') });
        mapH.setMarkers(mk);
        mapH.setCircle(st.lat != null ? { lat: st.lat, lon: st.lon, radius: 2000, color: 'orange' } : null);
      }
      function setPoint(lat, lon, o = {}) {
        st.lat = lat;
        st.lon = lon;
        if (o.name) { placeInput.value = o.name; st.placeAuto = true; namePlace.cancel(); }
        else if (o.fromMap && (!placeInput.value.trim() || st.placeAuto)) {
          namePlace(lat, lon, (name) => {
            if (st.lat === lat && st.lon === lon && (!placeInput.value.trim() || st.placeAuto)) { placeInput.value = name; st.placeAuto = true; drawMap(); }
          });
        }
        pointLine.classList.add('is-set');
        pointLine.querySelector('span').textContent = t('admin.event.point', { c: F.coord(lat, lon) });
        ui.fieldError(placeInput, null);
        drawMap();
        if (o.center && mapH) mapH.setView([lat, lon], 15);
        loadPlaces();
      }

      // --- address search: only on the button or Enter, at most once a second (Nominatim)
      let lastAsk = 0, asking = false;
      async function search() {
        const q = placeInput.value.trim();
        if (!q || asking) return;
        asking = true;
        searchBtn.disabled = true;
        try {
          const wait = 1100 - (Date.now() - lastAsk);
          if (wait > 0) await sleep(wait);
          lastAsk = Date.now();
          results.hidden = false;
          results.innerHTML = String(ui.loading());
          const r = await api('GET', `/api/geo/search?q=${encodeURIComponent(q)}&lang=${App.lang}`);
          if (!alive(el)) return;
          found = r.results || [];
          results.innerHTML = String(found.length
            ? h`${found.map((x, i) => h`<button class="adm-result" type="button" data-i="${i}">${App.icon('pin', { size: 18 })}<span class="adm-result__body"><b>${x.name}</b>${x.full && x.full !== x.name ? h`<small>${x.full}</small>` : ''}</span></button>`)}`
            : h`<p class="adm-result__none">${t(r.offline ? 'admin.event.searchOffline' : 'admin.event.searchEmpty')}</p>`);
        } catch (err) {
          if (alive(el)) results.innerHTML = String(h`<p class="adm-result__none">${errText(err)}</p>`);
        } finally {
          asking = false;
          searchBtn.disabled = false;
        }
      }
      searchBtn.addEventListener('click', search);
      placeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
      placeInput.addEventListener('input', () => { st.placeAuto = false; });
      results.addEventListener('click', (e) => {
        const b = e.target.closest('[data-i]');
        const x = b && found[Number(b.getAttribute('data-i'))];
        if (!x) return;
        results.hidden = true;
        setPoint(round6(x.lat), round6(x.lon), { name: x.name, center: true });
      });

      // --- safe place: the nearest known ones (GET /api/places), a custom name or a point on the map
      const loadPlaces = debounce(async () => {
        try {
          const q = st.lat != null ? `?lat=${st.lat}&lon=${st.lon}&limit=4` : '?limit=4';
          const r = await api('GET', '/api/places' + q);
          places = r.places || [];
        } catch (e) { /* no list — type a name or pick it on the map */ }
        if (alive(el)) drawChips();
      }, 200);
      function drawChips() {
        chips.innerHTML = String(h`${places.map((p) => h`<button class="adm-chip" type="button" data-place="${p.id}" aria-pressed="${st.safeId === p.id ? 'true' : 'false'}">${App.icon('shield', { size: 16 })}<span>${loc(p.name, p.name_ps)}</span>${p.distance_km != null ? h`<small>${F.distance(p.distance_km)}</small>` : ''}</button>`)}
          <button class="adm-chip adm-chip--map" type="button" data-safe-map>${App.icon('map', { size: 16 })}<span>${t('admin.event.safeOnMap')}</span></button>`);
      }
      function setSafe(lat, lon, o = {}) {
        st.safe_lat = lat;
        st.safe_lon = lon;
        st.safeId = o.id || null;
        if (o.name) { safeInput.value = o.name; st.safeAuto = true; nameSafe.cancel(); }
        else if (!safeInput.value.trim() || st.safeAuto) {
          safeInput.value = '';
          st.safeAuto = true;
          nameSafe(lat, lon, (name) => {
            if (st.safe_lat === lat && st.safe_lon === lon && (!safeInput.value.trim() || st.safeAuto)) { safeInput.value = name; drawMap(); }
          });
        }
        safeLine.hidden = false;
        safeLine.querySelector('span').textContent = t('admin.event.safePoint', { c: F.coord(lat, lon) });
        ui.fieldError(safeInput, null);
        drawChips();
        drawMap();
      }
      chips.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-place]');
        if (b) {
          const p = places.find((x) => String(x.id) === b.getAttribute('data-place'));
          if (p) setSafe(p.lat, p.lon, { name: loc(p.name, p.name_ps), id: p.id });
          return;
        }
        if (!e.target.closest('[data-safe-map]')) return;
        const center = st.safe_lat != null ? [st.safe_lat, st.safe_lon] : st.lat != null ? [st.lat, st.lon] : App.config.mapCenter;
        const p = await pickOnMap({ title: t('admin.event.safePickTitle'), center, markers: st.lat != null ? [{ lat: st.lat, lon: st.lon, color: 'red' }] : [] });
        if (p && alive(el)) setSafe(p.lat, p.lon);
      });
      safeInput.addEventListener('input', () => {
        st.safeAuto = false;
        ui.fieldError(safeInput, null);
        if (!safeInput.value.trim()) {
          st.safe_lat = null; st.safe_lon = null; st.safeId = null;
          safeLine.hidden = true;
          drawChips();
          drawMap();
        }
      });

      // --- start time
      timeBtn.addEventListener('click', async () => {
        const v = await pickTime(st.starts_at);
        if (!v || !alive(el)) return;
        st.starts_at = v;
        timeText.textContent = F.dateTime(v);      // Solar Hijri calendar, Kabul time
        timeBtn.classList.add('has-value');
        ui.fieldError(timeBtn, null);
      });
      typeSel.addEventListener('change', () => ui.fieldError(typeSel, null));

      // --- image (optional): POST /api/upload → its address goes into the alert
      const tile = el.querySelector('[data-image]');
      const file = el.querySelector('[data-file]');
      const img = el.querySelector('[data-image-img]');
      const imgText = el.querySelector('[data-image-text]');
      const del = el.querySelector('[data-image-del]');
      file.addEventListener('change', async () => {
        const f = file.files && file.files[0];
        if (!f) return;
        if (f.size > 3 * 1024 * 1024) { App.toast(t('admin.event.imageTooBig'), 'error'); file.value = ''; return; }
        tile.classList.add('is-busy');
        imgText.textContent = t('admin.event.imageUploading');
        try {
          const r = await App.upload(f);
          if (!alive(el)) return;
          st.image = r.url;
          img.src = r.url;
          img.hidden = false;
          del.hidden = false;
          tile.classList.add('has-image');
        } catch (err) {
          App.toast(errText(err), 'error');
        } finally {
          tile.classList.remove('is-busy');
          imgText.textContent = t('admin.event.addImage');
          file.value = '';
        }
      });
      del.addEventListener('click', (e) => {
        e.preventDefault();
        st.image = null;
        img.hidden = true;
        img.removeAttribute('src');
        del.hidden = true;
        tile.classList.remove('has-image');
      });

      // --- "انتشار رویداد": validate → POST /api/admin/alerts → result → list
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const type = typeSel.value;
        const place = placeInput.value.trim();
        const safe = safeInput.value.trim();
        const checks = [
          [typeSel, !!type, 'admin.event.errType'],
          [timeBtn, !!st.starts_at, 'admin.event.errTime'],
          [placeInput, st.lat != null, 'admin.event.errPlace'],
          [safeInput, !!safe || st.safe_lat != null, 'admin.event.errSafe'],
        ];
        let first = null;
        checks.forEach(([node, ok, key]) => { ui.fieldError(node, ok ? null : t(key)); if (!ok && !first) first = node; });
        if (first) { first.closest('.field').scrollIntoView({ block: 'center' }); first.focus({ preventScroll: true }); return; }
        const payload = { type, starts_at: st.starts_at, place, lat: st.lat, lon: st.lon, safe_place: safe, safe_lat: st.safe_lat, safe_lon: st.safe_lon };
        if (st.image) payload.image = st.image;
        publish.disabled = true;
        try {
          const r = await api('POST', '/api/admin/alerts', payload);
          App.toast(t('admin.event.published', { n: r.notified || 0, sms: r.sms || 0 }), { type: 'ok', ms: 7000 });
          App.go('#/admin/events?new=' + r.alert.id);
        } catch (err) {
          const target = { bad_type: typeSel, bad_time: timeBtn, location_required: placeInput, bad_location: placeInput, safe_place_required: safeInput }[err.code];
          if (target) { ui.fieldError(target, errText(err)); target.closest('.field').scrollIntoView({ block: 'center' }); }
          else App.toast(errText(err), 'error');
          if (alive(el)) publish.disabled = false;
        }
      });

      drawChips();
      loadPlaces();
      return () => { namePlace.cancel(); nameSafe.cancel(); loadPlaces.cancel(); };
    },
  });

  // ================================================================== 16 · emergency numbers
  const actions = (id, label) => h`<div class="adm-actions">
      <button class="adm-act adm-act--del" type="button" data-del="${id}" aria-label="${t('common.delete')}: ${label}">${App.icon('trash', { size: 22 })}</button>
      <button class="adm-act adm-act--edit" type="button" data-edit="${id}" aria-label="${t('common.edit')}: ${label}">${App.icon('edit', { size: 22 })}</button>
    </div>`;
  function numberForm(n, after) {
    formSheet({
      title: n ? t('admin.numbers.edit') : t('admin.numbers.add'),
      fields: h`${ui.field({ label: t('admin.numbers.number'), name: 'number', value: n ? n.number : '', required: true, dir: 'ltr', inputmode: 'tel', placeholder: '119', attrs: { maxlength: '20' } })}
        ${ui.field({ label: t('admin.numbers.titleFa'), name: 'title', value: n ? n.title : '', required: true, attrs: { maxlength: '200', lang: 'fa-AF' } })}
        ${ui.field({ label: t('admin.numbers.titlePs'), name: 'title_ps', value: n ? n.title_ps : '', attrs: { maxlength: '200', lang: 'ps-AF' } })}`,
      fieldFor: { bad_number: 'number', title_required: 'title' },
      async submit(v) {
        const number = F.latin(v.number).replace(/[\s\-()]/g, '');
        if (!/^\+?\d{2,15}$/.test(number)) throw fieldErr('number', t('admin.numbers.errNumber'));
        if (!v.title) throw fieldErr('title', t('admin.form.errTitle'));
        const body = { number, title: v.title, title_ps: v.title_ps || '' };
        if (n) await api('PUT', '/api/admin/numbers/' + n.id, body);
        else await api('POST', '/api/admin/numbers', body);
        App.toast(t('admin.form.saved'), 'ok');
        after();
      },
    });
  }
  App.screen('admin-numbers', {
    route: '#/admin/numbers', nav: 'admin', auth: 'admin', title: 'admin.numbers.title',
    async render(params, el) {
      el.classList.add('adm', 'adm-numbers');
      el.innerHTML = String(h`${ui.pageTitle({ title: t('admin.numbers.title') })}
        ${ui.addTile({ text: t('admin.numbers.add'), id: 'adm-add-number' })}
        <div class="adm-list" data-list>${ui.loading()}</div>`);
      const list = el.querySelector('[data-list]');
      let items = [];
      const draw = () => {
        list.innerHTML = String(items.length ? h`${items.map((n) => h`<article class="adm-num" data-id="${n.id}">
            ${actions(n.id, n.number)}
            <div class="adm-num__body"><b class="adm-num__num">${App.phone.display(n.number)}</b><span class="adm-num__title">${loc(n.title, n.title_ps)}</span></div>
          </article>`)}` : ui.empty({ icon: 'phone', title: t('admin.numbers.empty') }));
      };
      const load = async () => {
        const r = await api('GET', '/api/numbers');
        items = r.numbers || [];
        if (alive(el)) draw();
      };
      const reload = () => { if (alive(el)) load().catch((err) => App.toast(errText(err), 'error')); };
      el.querySelector('#adm-add-number').addEventListener('click', () => numberForm(null, reload));
      list.addEventListener('click', async (e) => {
        const ed = e.target.closest('[data-edit]');
        const dl = e.target.closest('[data-del]');
        if (!ed && !dl) return;
        const n = items.find((x) => String(x.id) === (ed ? ed.getAttribute('data-edit') : dl.getAttribute('data-del')));
        if (!n) return;
        if (ed) { numberForm(n, reload); return; }
        if (!(await confirmBox(t('admin.numbers.deleteConfirm', { n: App.phone.display(n.number) }), { ok: t('common.delete'), danger: true }))) return;
        try {
          await api('DELETE', '/api/admin/numbers/' + n.id);
          App.toast(t('admin.form.deleted'), 'ok');
        } catch (err) { App.toast(errText(err), 'error'); }
        reload();
      });
      await load();
    },
  });

  // ================================================================== 17 · training videos
  // a YouTube link (youtube.com, youtu.be) or a video file — like content.video_link on the server, roughly
  const VIDEO_URL = /^(?:https?:\/\/)?(?:(?:www\.|m\.|music\.)?(?:youtube\.com|youtube-nocookie\.com)\/\S+|youtu\.be\/\S+)$|^(?:https?:\/\/\S+|\/\S+)\.(?:mp4|webm|ogg|ogv|m4v)(?:[?#]\S*)?$/i;
  function videoForm(v, after) {
    formSheet({
      title: v ? t('admin.videos.edit') : t('admin.videos.add'),
      fields: h`${ui.field({ label: t('admin.videos.titleFa'), name: 'title', value: v ? v.title : '', required: true, attrs: { maxlength: '200', lang: 'fa-AF' } })}
        ${ui.field({ label: t('admin.videos.titlePs'), name: 'title_ps', value: v ? v.title_ps : '', attrs: { maxlength: '200', lang: 'ps-AF' } })}
        ${ui.field({ label: t('admin.videos.url'), name: 'url', value: v ? v.url : '', required: !v, type: 'url', dir: 'ltr', inputmode: 'url', placeholder: 'https://youtu.be/…', hint: t('admin.videos.urlHint'), attrs: { maxlength: '500' } })}`,
      fieldFor: { title_required: 'title', bad_video_url: 'url' },
      async submit(val) {
        if (!val.title) throw fieldErr('title', t('admin.form.errTitle'));
        const body = { title: val.title, title_ps: val.title_ps || '' };
        if (val.url) {
          if (!VIDEO_URL.test(val.url)) throw fieldErr('url', t('admin.videos.errUrl'));   // the server checks exactly
          body.url = val.url;
        } else if (!v || v.url) throw fieldErr('url', t('admin.videos.errUrl'));          // the link can change, not be erased
        if (v) await api('PUT', '/api/admin/videos/' + v.id, body);
        else await api('POST', '/api/admin/videos', body);
        App.toast(t('admin.form.saved'), 'ok');
        after();
      },
    });
  }
  function videoCard(v) {
    const title = loc(v.title, v.title_ps);
    const thumb = h`${v.youtube_id ? h`<img src="https://img.youtube.com/vi/${v.youtube_id}/mqdefault.jpg" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}<span class="adm-video__play">${App.icon('play', { size: 20 })}</span>`;
    return h`<article class="adm-video" data-id="${v.id}">
      <div class="adm-video__body">
        <h3 class="adm-video__title">${title}</h3>
        <p class="adm-video__date">${F.date(v.created_at)}${v.url ? '' : h` — <span class="adm-video__nolink">${t('admin.videos.noLink')}</span>`}</p>
        ${actions(v.id, title)}
      </div>
      ${v.url ? h`<a class="adm-video__thumb" href="${v.url}" target="_blank" rel="noopener" aria-label="${t('admin.videos.open')}: ${title}">${thumb}</a>`
        : h`<span class="adm-video__thumb" aria-hidden="true">${thumb}</span>`}
    </article>`;
  }
  App.screen('admin-videos', {
    route: '#/admin/videos', nav: 'admin', auth: 'admin', title: 'admin.videos.title',
    async render(params, el) {
      el.classList.add('adm', 'adm-wide', 'adm-videos');
      el.innerHTML = String(h`${ui.pageTitle({ title: t('admin.videos.title'), sub: t('admin.videos.subtitle') })}
        ${ui.addTile({ text: t('admin.videos.add'), id: 'adm-add-video' })}
        <div class="adm-list" data-list>${ui.loading()}</div>`);
      const list = el.querySelector('[data-list]');
      let items = [];
      const draw = () => {
        list.innerHTML = String(items.length ? h`${items.map(videoCard)}` : ui.empty({ icon: 'video', title: t('admin.videos.empty') }));
        // the YouTube preview did not load (no network) — a gray placeholder with ▶, as in 17
        list.querySelectorAll('.adm-video__thumb img').forEach((i) => i.addEventListener('error', () => i.remove()));
      };
      const load = async () => {
        const r = await api('GET', '/api/videos');
        items = r.videos || [];
        if (alive(el)) draw();
      };
      const reload = () => { if (alive(el)) load().catch((err) => App.toast(errText(err), 'error')); };
      el.querySelector('#adm-add-video').addEventListener('click', () => videoForm(null, reload));
      list.addEventListener('click', async (e) => {
        const ed = e.target.closest('[data-edit]');
        const dl = e.target.closest('[data-del]');
        if (!ed && !dl) return;
        const v = items.find((x) => String(x.id) === (ed ? ed.getAttribute('data-edit') : dl.getAttribute('data-del')));
        if (!v) return;
        if (ed) { videoForm(v, reload); return; }
        if (!(await confirmBox(t('admin.videos.deleteConfirm', { t: loc(v.title, v.title_ps) }), { ok: t('common.delete'), danger: true }))) return;
        try {
          await api('DELETE', '/api/admin/videos/' + v.id);
          App.toast(t('admin.form.deleted'), 'ok');
        } catch (err) { App.toast(errText(err), 'error'); }
        reload();
      });
      await load();
    },
  });

  // ================================================================== 18 · SOS cases
  /** A photo from the resident's chat (paperclip): a message whose whole text is a path /uploads/….png|jpg|webp */
  const PHOTO = /^\/uploads\/[\w.-]+\.(?:png|jpe?g|webp)$/i;
  const photoOf = (m) => (m && typeof m.text === 'string' && PHOTO.test(m.text.trim()) ? m.text.trim() : null);
  /** Photo enlarged — a sheet over the screen */
  function zoomPhoto(url) {
    const s = ui.sheet(h`<img class="adm-zoom" src="${url}" alt="${t('admin.chat.photo')}">
      <a class="adm-link" href="${url}" target="_blank" rel="noopener">${App.icon('image', { size: 16 })}<span>${t('admin.chat.photoOpen')}</span></a>`,
    { title: t('admin.chat.photo') });
    s.el.classList.add('theme-admin', 'adm-sheet');
  }
  /** Message text in the screen's language: standard (text_key) — from the dictionary, photo — "عکس", the rest — as written. */
  function msgText(m) {
    if (!m) return '';
    if (photoOf(m)) return '📷 ' + t('admin.chat.photo');
    if (m.text_key === 'sos_default') return t('sos.defaultText');
    if (m.text_key === 'sos_auto_received') return t('sos.autoReply');
    if (m.text_key === 'sos_auto_wait') {
      return [t('sos.teamReply'), t('sos.meanwhile'), '✓ ' + t('sos.tip.tracked'), '✓ ' + t('sos.tip.safePlace'),
        '✓ ' + t('sos.tip.battery'), t('sos.directNumber') + ' ' + F.digits('119')].join('\n');
    }
    return m.text || '';
  }
  function sosCard(c, seen) {
    const cs = c.case;
    const def = c.kind === 'default';
    const closed = cs.status !== 'open';
    const name = (c.user && c.user.name) || t('admin.messages.anon');
    const unread = isUnread(c, seen);
    const place = c.place_name || (cs.lat != null ? F.coord(cs.lat, cs.lon) : t('admin.messages.noPlace'));
    return h`<article class="adm-sos adm-sos--${def ? 'default' : 'custom'}${unread ? ' is-unread' : ''}${closed ? ' is-closed' : ''}" data-case="${cs.id}">
      <div class="adm-sos__top"><span class="adm-sos__badges">${ui.badge(def ? t('admin.messages.defaultSos') : t('admin.messages.custom'), def ? 'sos' : 'gray')}${closed ? ui.badge(t('admin.messages.closedMark'), 'gray') : ''}</span>
        <time class="adm-sos__time" datetime="${cs.created_at}">${F.ago(cs.created_at)}</time></div>
      <h2 class="adm-sos__name">${unread ? h`<span class="adm-dot" role="img" aria-label="${t('admin.messages.new')}"></span>` : ''}<span>${name}</span></h2>
      <p class="adm-sos__text">${msgText(c.last_message)}</p>
      <p class="adm-sos__place"><bdi>${place}</bdi></p>
      <div class="adm-sos__foot">${ui.button({ text: closed ? t('admin.messages.view') : def ? t('admin.messages.reply') : t('admin.messages.viewReply'),
        kind: closed ? 'outline-accent' : 'primary', go: '#/admin/chat/' + cs.id, small: true, block: false, cls: 'adm-sos__btn' })}</div>
    </article>`;
  }
  App.screen('admin-messages', {
    route: '#/admin/messages', nav: 'admin', auth: 'admin', title: 'admin.messages.title',
    async render(params, el) {
      el.classList.add('adm', 'adm-wide', 'adm-messages');
      const status = params.status === 'closed' ? 'closed' : 'open';
      el.innerHTML = String(h`${ui.pageTitle({ title: t('admin.messages.title') })}
        <nav class="adm-seg" aria-label="${t('admin.messages.title')}">
          <a class="adm-seg__item${status === 'open' ? ' is-on' : ''}" href="#/admin/messages" data-go="#/admin/messages"${status === 'open' ? h` aria-current="page"` : ''}>${t('admin.messages.open')}</a>
          <a class="adm-seg__item${status === 'closed' ? ' is-on' : ''}" href="#/admin/messages?status=closed" data-go="#/admin/messages?status=closed"${status === 'closed' ? h` aria-current="page"` : ''}>${t('admin.messages.closed')}</a>
        </nav>
        <div class="adm-list" data-list>${ui.loading()}</div>`);
      const list = el.querySelector('[data-list]');
      let cards = new Map();                   // case id → card, as in GET /api/admin/sos
      const load = async () => {
        const r = await api('GET', '/api/admin/sos?status=' + status);
        if (!alive(el)) return;
        const cases = r.cases || [];
        cards = new Map(cases.map((c) => [c.case.id, c]));
        const seen = seenMap();
        list.innerHTML = String(cases.length ? h`${cases.map((c) => sosCard(c, seen))}`
          : ui.empty({ icon: 'chat', title: t(status === 'open' ? 'admin.messages.empty' : 'admin.messages.emptyClosed') }));
      };
      await load();
      if (!alive(el)) return undefined;
      // new cases and residents' messages arrive by themselves, without a reload
      const again = debounce(() => load().catch(() => {}), 300);
      // 'sos_case' may repeat with the same id (the server labels the place in the background): update the card in place
      function upsert(card) {
        const id = card && card.case ? card.case.id : null;
        if (id == null) return;
        const old = cards.get(id);
        const node = list.querySelector(`.adm-sos[data-case="${id}"]`);
        const fits = (card.case.status === 'closed') === (status === 'closed');
        if (!old || !node) { if (fits) again(); return; }      // a new one — reload the list, in the server's order
        if (!fits) { cards.delete(id); node.remove(); if (!cards.size) again(); return; }
        const merged = Object.assign({}, old, card, {
          place_name: card.place_name || old.place_name || null,   // no label — the old one, until a new one arrives
          last_message: card.last_message || old.last_message,
        });
        cards.set(id, merged);
        node.outerHTML = String(sosCard(merged, seenMap()));
      }
      App.live.on('sos_case', upsert);
      App.live.on('sos_message', again);
      const timer = setInterval(again, 60000);   // so that "۵ دقیقه پیش" does not go stale
      return () => { clearInterval(timer); again.cancel(); };
    },
  });

  // ================================================================== rescuer chat (not in Figma — styled like 13)
  function personHtml(u) {
    const head = h`<h2 class="adm-panel__title">${App.icon('user', { size: 20 })}<span>${t('admin.chat.person')}</span></h2>`;
    if (!u) return h`${head}<p class="adm-note">${t('admin.chat.guest')}</p>`;
    const none = h`<span class="muted">${t('admin.chat.none')}</span>`;
    const blood = !u.blood_group ? none : u.blood_group === 'unknown' ? t('auth.signup.bloodUnknown') : h`<bdi class="adm-blood" dir="ltr">${u.blood_group}</bdi>`;
    const health = (u.health || []).length
      ? h`<span class="adm-tags">${u.health.map((x) => h`<span class="adm-tag${x === 'none' ? ' adm-tag--ok' : ''}">${HEALTH[x] ? t(HEALTH[x]) : x}</span>`)}</span>` : none;
    const contacts = (u.contacts || []).length
      ? h`<ul class="adm-contacts">${u.contacts.map((p) => h`<li><span>${p.name || ''}</span>${p.phone ? ui.phone(p.phone) : ''}</li>`)}</ul>` : none;
    return h`${head}<dl class="adm-facts">
      <div><dt>${t('admin.chat.phone')}</dt><dd>${u.phone ? h`<span class="adm-facts__phone">${ui.phone(u.phone)}<a class="adm-call" href="${App.phone.tel(u.phone)}" aria-label="${t('common.call')}">${App.icon('phone', { size: 18 })}</a></span>` : none}</dd></div>
      <div><dt>${t('admin.chat.blood')}</dt><dd>${blood}</dd></div>
      <div><dt>${t('admin.chat.health')}</dt><dd>${health}</dd></div>
      <div><dt>${t('admin.chat.contacts')}</dt><dd>${contacts}</dd></div>
    </dl>`;
  }
  function chatMsg(m, name) {
    const time = msgTime(m.created_at);
    const photo = photoOf(m);
    if (m.sender === 'user') {
      return h`<div class="adm-msg adm-msg--user${photo ? ' adm-msg--photo' : ''}" data-mid="${m.id}">${ui.msg({
        side: 'in', avatar: 'user', name, time, text: photo ? '' : msgText(m),
        footer: photo ? h`<button class="adm-photo" type="button" data-photo="${photo}" aria-label="${t('admin.chat.photo')}"><img src="${photo}" alt="${t('admin.chat.photo')}" loading="lazy"></button>` : '' })}</div>`;
    }
    const auto = m.sender === 'auto';
    return h`<div class="adm-msg adm-msg--${auto ? 'auto' : 'team'}" data-mid="${m.id}">${ui.msg({
      side: 'out', avatar: 'users', name: t(auto ? 'admin.chat.auto' : 'admin.chat.rescuer'), text: msgText(m), time, status: auto ? null : 'sent' })}</div>`;
  }
  App.screen('admin-chat', {
    route: '#/admin/chat/:id', auth: 'admin', title: 'admin.chat.messages',
    async render(params, el) {
      el.classList.add('adm', 'adm-chat');
      const d = await api('GET', '/api/admin/sos/' + encodeURIComponent(params.id));
      if (!alive(el)) return undefined;
      let c = d.case;
      const user = d.user;
      const track = (d.track || []).slice();
      const name = (user && user.name) || t('admin.messages.anon');
      el.innerHTML = String(h`${ui.header({ title: name, back: '#/admin/messages', shield: false })}
        <p class="adm-chat__sub"><span>${t('admin.chat.case', { id: c.id })}</span><span data-status></span></p>
        <p class="adm-chat__place" data-head-place hidden>${App.icon('pin-fill', { size: 16 })}<bdi></bdi></p>
        <section class="adm-panel" data-person>${personHtml(user)}</section>
        <section class="adm-panel">
          <h2 class="adm-panel__title">${App.icon('pin', { size: 20 })}<span>${t('admin.chat.where')}</span></h2>
          <div class="map adm-chat__map" data-map hidden></div>
          <p class="adm-note" data-noloc hidden>${t('admin.chat.noLocation')}</p>
          <div class="adm-where" data-where></div>
        </section>
        <div class="adm-chat__tools" data-tools>${ui.button({ text: t('admin.chat.close'), kind: 'outline', small: true, block: false, icon: 'check', cls: 'adm-chat__close', attrs: { 'data-close': '' } })}</div>
        <h2 class="adm-section">${t('admin.chat.messages')}</h2>
        <div class="chat adm-chat__list" data-msgs></div>
        <div class="adm-chat__bar" data-bar></div>`);
      const mapEl = el.querySelector('[data-map]');
      const noloc = el.querySelector('[data-noloc]');
      const where = el.querySelector('[data-where]');
      const msgs = el.querySelector('[data-msgs]');
      const bar = el.querySelector('[data-bar]');
      let mapH = null, line = null;
      msgs.addEventListener('click', (e) => { const p = e.target.closest('[data-photo]'); if (p) zoomPhoto(p.getAttribute('data-photo')); });

      // --- the resident's location and track (a point every 30 s — the sos_location event)
      function ensureMap(lat, lon) {
        if (mapH) return;
        mapEl.hidden = false;
        noloc.hidden = true;
        mapH = App.map(mapEl, { center: [lat, lon], zoom: 15 });
        if (!mapH || !window.L) return;
        line = window.L.polyline(track.map((p) => [p.lat, p.lon]), { color: '#1E6FD9', weight: 3, opacity: 0.8 }).addTo(mapH.map);
        if (track.length > 1) mapH.map.fitBounds(line.getBounds().extend([lat, lon]), { padding: [30, 30], maxZoom: 16 });
      }
      const headPlace = el.querySelector('[data-head-place]');
      function drawWhere() {
        headPlace.hidden = !c.place_name;                 // header: who and where
        headPlace.querySelector('bdi').textContent = c.place_name || '';
        if (c.lat == null || c.lon == null) { noloc.hidden = false; where.innerHTML = ''; return; }
        ensureMap(c.lat, c.lon);
        if (mapH) mapH.setMarkers([{ lat: c.lat, lon: c.lon, color: 'red', label: name }]);
        const last = track.length ? track[track.length - 1] : null;
        const at = (last && last.at) || c.updated_at;
        where.innerHTML = String(h`${c.place_name ? h`<span class="adm-where__place">${App.icon('pin-fill', { size: 16 })}<bdi>${c.place_name}</bdi></span>` : ''}
          <span class="adm-where__coord">${F.coord(c.lat, c.lon)}</span>
          <span>${App.icon('refresh', { size: 16 })}${t('admin.chat.updated', { t: F.ago(at) })}</span>
          ${c.battery != null ? h`<span>${App.icon('battery', { size: 16 })}${t('device.battery', { n: F.percent(c.battery) })}</span>` : ''}
          ${c.signal ? h`<span>${App.icon('signal', { size: 16 })}${t('device.signal', { s: signalText(c.signal) })}</span>` : ''}`);
      }

      // --- messages: the resident on the left, rescuers and the auto-reply on the right (like own messages in 13)
      const have = new Set();
      const nearBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 200;
      const toBottom = () => window.scrollTo(0, document.documentElement.scrollHeight);
      function addMsgs(list, scroll) {
        const fresh = (list || []).filter((m) => m && m.id != null && !have.has(m.id));
        if (!fresh.length) return;
        fresh.forEach((m) => have.add(m.id));
        msgs.insertAdjacentHTML('beforeend', String(h`${fresh.map((m) => chatMsg(m, name))}`));
        const mine = fresh.filter((m) => m.sender === 'user').map((m) => m.id);
        if (mine.length) markSeen(c.id, Math.max(...mine));
        if (scroll) toBottom();
      }

      // --- open: the reply bar; closed — a note
      function drawStatus() {
        const open = c.status === 'open';
        el.querySelector('[data-status]').innerHTML = String(ui.badge(t(open ? 'admin.messages.open' : 'admin.messages.closed'), open ? 'green' : 'gray'));
        el.querySelector('[data-tools]').hidden = !open;
        if (open && !bar.querySelector('.chatbar')) {
          bar.innerHTML = String(ui.chatbar({ dock: true, attach: false, placeholder: t('admin.chat.reply') }));
          ui.bindChatbar(bar, async (text) => {
            try {
              const r = await api('POST', `/api/admin/sos/${c.id}/messages`, { text });
              if (r.case) c = r.case;
              addMsgs([r.message], true);
            } catch (err) {
              if (err.code === 'sos_closed') { c = Object.assign({}, c, { status: 'closed' }); drawStatus(); }
              throw Object.assign(new Error(errText(err)), { code: err.code });
            }
          });
        } else if (!open) {
          bar.innerHTML = String(h`<p class="adm-chat__closed">${App.icon('check-circle', { size: 18 })}<span>${t('admin.chat.closedNote')}</span></p>`);
        }
      }
      el.querySelector('[data-close]').addEventListener('click', async () => {
        if (!(await confirmBox(t('admin.chat.closeConfirm'), { ok: t('admin.chat.close'), danger: true }))) return;
        try {
          const r = await api('POST', `/api/admin/sos/${c.id}/close`);
          c = r.case;
          drawStatus();
          App.toast(t('admin.chat.closedToast'), 'ok');
          Watch.bump();
        } catch (err) { App.toast(errText(err), 'error'); }
      });

      drawWhere();
      drawStatus();
      addMsgs(d.messages, false);
      App.live.on('sos_message', (x) => { if (x && Number(x.case_id) === c.id) addMsgs([x.message], nearBottom()); });
      App.live.on('sos_location', (x) => {
        if (!x || Number(x.case_id) !== c.id || x.lat == null) return;
        track.push({ lat: x.lat, lon: x.lon, at: x.at });
        c = Object.assign({}, c, {
          lat: x.lat, lon: x.lon, place_name: x.place_name || c.place_name, updated_at: x.at || c.updated_at,
          battery: x.battery != null ? x.battery : c.battery, signal: x.signal || c.signal,
        });
        const first = !mapH;
        drawWhere();
        if (line && !first) line.addLatLng([x.lat, x.lon]);
        if (mapH) mapH.map.panTo([x.lat, x.lon]);
      });
      App.live.on('sos_case', (x) => {
        if (!x || !x.case || x.case.id !== c.id) return;
        // a repeat with the same id: first without the place label, then with it — do not lose the old label
        c = Object.assign({}, c, x.case, { place_name: x.case.place_name || x.place_name || c.place_name });
        drawStatus();
        drawWhere();
      });
      const timer = setInterval(drawWhere, 30000);   // so that "به‌روزرسانی: … پیش" does not go stale
      return () => clearInterval(timer);
    },
  });
})(window.App);
