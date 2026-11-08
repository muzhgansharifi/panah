/* Group "auth" — the PANAH (پناه) sign-in screens (design — Figma frames 02, 03, 04, 01; see app/web/README.md):
     #/login   (02) number → SMS code → #/verify; "حساب جدید بسازید" → #/signup; SOS without sign-in → #/sos/now; languages.
     #/signup  (03) quick sign-up: number + location (App.location.get) → code → #/verify; "رد کردن" → #/home as a guest.
     #/verify  (04) 6 code cells, resend timer, "تأیید" → a new resident → #/profile?new=1, otherwise → #/home (or ?next=).
     #/profile (01) profile: new — "ساختن حساب"; signed in — editing and "خروج".
   Server — app/server/auth.py: POST /api/auth/code {phone} → {phone, expires_in, resend_in, test_code};
   POST /api/auth/verify {phone, code} → {user, is_new}; GET/PUT /api/me; POST /api/me/location; POST /api/auth/logout;
   address — GET /api/geo/search?q= → {results, offline}, GET /api/geo/name?lat&lon → {name}.
   SMS — test mode: the code (test_code) is shown on 04 in a strip "حالت آزمایشی — کد: …".
   Start: empty address and the resident is not signed in → #/login (signed in — the core goes to #/home). */
(function (App) {
  'use strict';
  if (!App || !App.screen || !App.ui) return;
  const h = App.html;
  const t = (k, v) => App.t(k, v);
  const ui = App.ui;
  const LANG_TAG = { fa: 'fa-AF', ps: 'ps-AF', en: 'en' };
  const OTHER = { fa: 'ps', ps: 'fa' }; // second line of a pair on 03: Dari ↔ Pashto (none in English)
  const MAX_CONTACTS = 10;              // same as auth.MAX_CONTACTS on the server
  const BLOOD = ['A+', 'B+', 'AB+', 'O+', 'A-', 'B-', 'AB-', 'O-'];
  const HEALTH = [['epilepsy', 'health.epilepsy'], ['blood_pressure', 'health.bloodPressure'], ['diabetes', 'health.diabetes'],
    ['disability', 'health.disability'], ['heart', 'health.heart'], ['drug_allergy', 'health.allergy'], ['none', 'health.none']];

  // ---------------------------------------------------------------- sign-in screen icons (filled, as in screenshots)
  const svg = (body, size) => App.raw(`<svg class="ic" viewBox="0 0 48 48" width="${size}" height="${size}" aria-hidden="true">${body}</svg>`);
  const ICON = {
    phone: (s = 44) => svg('<rect x="13" y="4" width="22" height="40" rx="5" fill="currentColor"/><rect x="16.5" y="9.5" width="15" height="26" rx="1.6" fill="#fff"/><circle cx="24" cy="39.6" r="1.9" fill="#fff"/>', s),
    sms: (s = 54) => svg('<rect x="8" y="12" width="21" height="33" rx="4.5" fill="currentColor"/><rect x="11.5" y="17" width="14" height="21" rx="1.5" fill="#fff"/><circle cx="18.5" cy="41.2" r="1.7" fill="#fff"/>'
      + '<path d="M26 3h17a3 3 0 0 1 3 3v11a3 3 0 0 1-3 3h-8l-6 5v-5h-3a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3z" fill="currentColor" stroke="#fff" stroke-width="1.6"/><path d="M30 9h10M30 14h7" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>', s),
    lock: (s = 26) => svg('<path d="M16 21v-5a8 8 0 0 1 16 0v5" fill="none" stroke="currentColor" stroke-width="4.5"/><rect x="10" y="20" width="28" height="23" rx="5" fill="currentColor"/><circle cx="24" cy="30" r="3.2" fill="#fff"/><path d="M24 31v5" stroke="#fff" stroke-width="3" stroke-linecap="round"/>', s),
  };

  // ---------------------------------------------------------------- waiting for the code: number, timer, test code
  // In sessionStorage (this tab only): survives a reload of 04 and "ویرایش شماره".
  const PENDING = 'panah.auth.pending';
  let memPending = null;
  const pending = {
    get() {
      let p = memPending;
      try { p = JSON.parse(root().sessionStorage.getItem(PENDING)) || p; } catch (e) { /* private mode */ }
      return p && p.phone ? p : null;
    },
    set(p) { memPending = p; try { root().sessionStorage.setItem(PENDING, JSON.stringify(p)); } catch (e) { /* */ } },
    clear() { memPending = null; try { root().sessionStorage.removeItem(PENDING); } catch (e) { /* */ } },
  };
  function root() { return typeof window !== 'undefined' ? window : {}; }
  let draftPhone = null; // what was typed on 02/03 — not lost when the language changes

  /** Where to go after signing in: only our own "#/…" address, not the sign-in screens. */
  function safeNext(v) {
    if (typeof v !== 'string' || !/^#\/[^\s<>"'`\\]*$/.test(v)) return null;
    return /^#\/(login|signup|verify)(?:[/?]|$)/.test(v) ? null : v;
  }
  const withNext = (route, next) => (next ? route + (route.includes('?') ? '&' : '?') + 'next=' + encodeURIComponent(next) : route);

  /** "0777 123 456" in the language's digits — for the input field (no invisible direction marks). */
  function phoneInput(phone) {
    const l = App.phone.local(phone);
    return l ? App.fmt.digits(`${l.slice(0, 4)} ${l.slice(4, 7)} ${l.slice(7)}`) : '';
  }

  /** A "busy" button: disabled, with a spinner. */
  function busy(btn, on) {
    if (!btn) return;
    btn.disabled = !!on;
    btn.classList.toggle('is-busy', !!on);
    btn.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  /** Sign-in server error → clear text in the screen's language (the server sends Dari only). */
  function authError(err) {
    const d = (err && err.data && typeof err.data === 'object') ? err.data : {};
    switch (err && err.code) {
      case 'bad_phone': return t('error.phone');
      case 'wrong_code': return d.attempts_left > 0 ? t('auth.err.wrongCode', { n: d.attempts_left }) : t('auth.err.burned');
      case 'expired': return t('auth.err.expired');
      case 'too_soon': return t('auth.err.tooSoon', { n: Number(d.retry_after) || 45 });
      case 'too_many': return t('auth.err.tooMany', { n: Math.max(1, Math.ceil((Number(d.retry_after) || 3600) / 60)) });
      case 'bad_code': return t('auth.err.badCode');
      case 'no_code': return t('auth.err.noCode');
      default: return (err && err.message) || t('error.generic');
    }
  }

  /** POST /api/auth/code → remember the pending code. extra: {from, next, loc}. */
  async function requestCode(phone, extra) {
    const r = await App.api('POST', '/api/auth/code', { phone });
    const now = Date.now();
    const p = Object.assign({}, extra, {
      phone: r.phone || phone,
      resendAt: now + (Number(r.resend_in) || 45) * 1000,
      expiresAt: now + (Number(r.expires_in) || 300) * 1000,
      testCode: r.test_code || null,
    });
    pending.set(p);
    return p;
  }

  /** 02/03: number → code → #/verify. Same number, code still fresh (too_soon) — to 04 with the previous code. */
  async function sendCode(phone, extra) {
    try {
      await requestCode(phone, extra);
    } catch (err) {
      const p = pending.get();
      if (!(err.code === 'too_soon' && p && p.phone === phone && p.expiresAt > Date.now())) throw err;
      const wait = Number(err.data && err.data.retry_after) || 0;
      pending.set(Object.assign(p, extra, { resendAt: Date.now() + wait * 1000 }));
    }
    App.go(withNext('#/verify', extra.next));
  }

  // ---------------------------------------------------------------- Dari ↔ Pashto pairs (03)
  function tIn(lang, key) {
    const s = (App.i18n.dicts[lang] || {})[key];
    return s == null || s === '' ? null : s;
  }
  function other(key) {
    const o = OTHER[App.lang];
    const s = o ? tIn(o, key) : null;
    return s && s !== t(key) ? s : null;
  }
  /** Two lines: the screen's language, with the other language below it (as in screenshot 03). */
  function pair(key) {
    const s = other(key);
    return h`<span class="pair"><span lang="${LANG_TAG[App.lang]}">${t(key)}</span>${s ? h`<span lang="${LANG_TAG[OTHER[App.lang]]}">${s}</span>` : ''}</span>`;
  }
  /** "شروع / پیل" — both languages on one line. */
  function slash(key) { const s = other(key); return s ? `${t(key)} / ${s}` : t(key); }
  async function loadPairs() {
    const o = OTHER[App.lang];
    if (o) { try { await App.i18n.load(o); } catch (e) { /* without the second line */ } }
  }

  // ---------------------------------------------------------------- shared pieces
  /** Header with the logo (02). */
  function hero() {
    return h`<header class="auth-hero">
      ${ui.logo({ kind: 'sos', size: 96, label: t('app.name') })}
      <h1 class="auth-hero__title">${t('app.title')}</h1>
      <p class="auth-hero__tag" dir="ltr" lang="en">${t('app.tagline')}</p>
    </header>`;
  }
  /** Number field: icon on the left, digits after it (02, 03), error under the field — App.ui.fieldError. */
  function phoneField(o) {
    return h`<div class="field auth-field">
      <div class="input-wrap auth-phone" dir="ltr">
        <span class="input-wrap__icon">${App.icon('mobile')}</span>
        <input class="input" id="${o.id}" type="tel" inputmode="tel" dir="ltr" maxlength="20" autocomplete="${o.autocomplete || 'tel-national'}" placeholder="${o.placeholder || t('auth.phonePlaceholder')}" value="${o.value || ''}"${o.label ? h` aria-label="${o.label}"` : ''}>
      </div>
      <p class="field__error" role="alert" hidden></p>
    </div>`;
  }

  // ---------------------------------------------------------------- 02 sign-in
  App.screen('login', {
    route: '#/login',
    title: 'auth.login',
    render(params, el) {
      const next = safeNext(params.next);
      const p = pending.get();
      const value = draftPhone != null ? draftPhone : (p ? phoneInput(p.phone) : '');
      el.classList.add('auth-screen');
      el.innerHTML = String(h`
        <div class="auth auth--login">
          ${hero()}
          <form class="card auth-card" novalidate>
            <label class="auth-label" for="au-login-phone">${App.icon('user', { size: 24 })}<span>${t('auth.phone')}</span></label>
            ${phoneField({ id: 'au-login-phone', value })}
            ${ui.button({ text: t('auth.login'), type: 'submit', id: 'au-login-submit' })}
            ${ui.or()}
            ${ui.button({ text: t('auth.newAccount'), kind: 'outline', icon: 'user-plus', edge: true, go: withNext('#/signup', next), id: 'au-login-new' })}
            ${ui.button({ text: t('auth.sosNoLogin'), kind: 'sos', go: '#/sos/now', id: 'au-login-sos' })}
          </form>
          <div class="auth-langs" dir="ltr">${ui.langSwitch()}</div>
        </div>`);
      const form = el.querySelector('form');
      const input = el.querySelector('#au-login-phone');
      const btn = el.querySelector('#au-login-submit');
      input.addEventListener('input', () => { draftPhone = input.value; ui.fieldError(input, null); });
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const phone = App.phone.normalize(input.value);
        if (!phone) { ui.fieldError(input, t('error.phone')); input.focus(); return; }
        ui.fieldError(input, null);
        busy(btn, true);
        try {
          await sendCode(phone, { from: '#/login', next });
        } catch (err) {
          ui.fieldError(input, authError(err));
        } finally { busy(btn, false); }
      });
    },
  });

  // ---------------------------------------------------------------- 03 quick sign-up
  App.screen('signup', {
    route: '#/signup',
    title: 'auth.quick.title',
    async render(params, el) {
      const next = safeNext(params.next);
      await loadPairs();
      const p = pending.get();
      const value = draftPhone != null ? draftPhone : (p ? phoneInput(p.phone) : '');
      const skipEn = t('auth.quick.skipHintEn') !== t('auth.quick.skipHint') ? t('auth.quick.skipHintEn') : '';
      el.classList.add('auth-screen');
      el.innerHTML = String(h`
        <div class="auth auth--signup">
          <header class="auth-hero auth-hero--signup">
            ${ui.logo({ kind: 'sos', size: 100, label: t('app.name') })}
            <h1 class="auth-hero__pair">${pair('auth.quick.title')}</h1>
          </header>
          <form class="auth auth-quick" novalidate>
            <div class="card auth-card">
              <div class="auth-intro">
                <label class="auth-intro__text" for="au-quick-phone">${pair('auth.quick.enterPhone')}</label>
                <span class="auth-bubble">${ICON.phone(42)}</span>
              </div>
              ${phoneField({ id: 'au-quick-phone', value, placeholder: t('auth.quick.phoneMask') })}
              <div class="auth-note auth-note--pink">${pair('auth.quick.autoLocation')}${App.icon('pin-fill', { size: 28 })}</div>
            </div>
            <button class="btn btn--primary btn--tall auth-start" type="submit" id="au-quick-start">
              <span class="btn__text"><span>${slash('auth.quick.start')}</span><small dir="ltr" lang="en">${t('auth.quick.oneTapEn')}</small></span>
              <span class="btn__check">${App.icon('check', { size: 20, stroke: 3 })}</span>
            </button>
            <p class="auth-status" role="status" hidden></p>
            <a class="btn btn--outline btn--tall auth-skip" href="${App.config.home}" data-go="${App.config.home}" id="au-quick-skip">
              <span class="btn__text"><span>${slash('auth.quick.skip')}</span><small>${t('auth.quick.skipHint')}</small>${skipEn ? h`<small dir="ltr" lang="en">${skipEn}</small>` : ''}</span>
              ${App.icon('skip', { size: 30, stroke: 3 })}
            </a>
            <div class="auth-note auth-note--green">${pair('auth.quick.safe')}${ICON.lock(30)}</div>
          </form>
        </div>`);
      const form = el.querySelector('form');
      const input = el.querySelector('#au-quick-phone');
      const btn = el.querySelector('#au-quick-start');
      const status = el.querySelector('.auth-status');
      input.addEventListener('input', () => { draftPhone = input.value; ui.fieldError(input, null); });
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const phone = App.phone.normalize(input.value);
        if (!phone) { ui.fieldError(input, t('error.phone')); input.focus(); return; }
        ui.fieldError(input, null);
        busy(btn, true);
        status.textContent = t('auth.quick.locating');
        status.hidden = false;
        let loc = null;
        try { loc = await App.location.get(); } catch (err) { loc = null; } // GPS → saved → point on the map; refused — no location
        status.hidden = true;
        if (!el.isConnected) return; // left the screen while we waited for the location
        try {
          await sendCode(phone, { from: '#/signup', next, loc: loc ? { lat: loc.lat, lon: loc.lon, accuracy: loc.accuracy } : null });
        } catch (err) {
          ui.fieldError(input, authError(err));
        } finally { busy(btn, false); }
      });
    },
  });

  // ---------------------------------------------------------------- 04 code from the SMS
  App.screen('verify', {
    route: '#/verify',
    title: 'auth.verify.title',
    render(params, el) {
      let p = pending.get();
      if (!p) { App.replace(withNext(App.config.login, safeNext(params.next))); return undefined; }
      const next = safeNext(params.next) || safeNext(p.next);
      el.classList.add('auth-screen', 'screen--gray');
      el.innerHTML = String(h`
        <div class="auth auth--verify">
          ${ui.header({ title: t('auth.verify.title'), back: p.from || App.config.login })}
          <div class="card auth-sent">
            <div class="auth-sent__text">
              <p>${t('auth.verify.codeSentTo')}</p>
              <p class="auth-sent__phone">${App.phone.display(p.phone)}</p>
              <p>${t('auth.verify.codeSent')}</p>
            </div>
            <span class="auth-bubble auth-bubble--lg">${ICON.sms(54)}</span>
          </div>
          <form class="card auth-code" id="au-code-form" novalidate>
            <div class="auth-code__head">
              <label class="auth-code__label" for="au-otp-1">${t('auth.verify.enterCode')}</label>
              <span class="auth-bubble auth-bubble--purple">${ICON.lock(26)}</span>
            </div>
            <div class="otp" dir="ltr" role="group" aria-label="${t('auth.verify.enterCode')}">
              ${[1, 2, 3, 4, 5, 6].map((i) => h`<input class="otp__cell" id="au-otp-${i}" type="text" inputmode="numeric" autocomplete="${i === 1 ? 'one-time-code' : 'off'}" aria-label="${t('auth.verify.digit', { n: i })}">`)}
            </div>
            <p class="auth-code__error" role="alert" hidden></p>
          </form>
          <div class="auth-test" role="note" hidden></div>
          <div class="auth-timer" aria-live="polite">${App.icon('clock', { size: 26 })}<span class="auth-timer__text"></span></div>
          ${ui.button({ text: t('auth.verify.resend'), kind: 'outline', icon: 'refresh', edge: true, id: 'au-resend' })}
          ${ui.button({ text: t('auth.verify.confirm'), kind: 'purple', icon: 'check', type: 'submit', id: 'au-confirm', attrs: { form: 'au-code-form' } })}
          ${ui.button({ text: t('auth.verify.editPhone'), kind: 'outline', icon: 'edit', edge: true, id: 'au-edit' })}
        </div>`);
      const form = el.querySelector('#au-code-form');
      const box = el.querySelector('.otp');
      const cells = [...el.querySelectorAll('.otp__cell')];
      const errEl = el.querySelector('.auth-code__error');
      const testEl = el.querySelector('.auth-test');
      const timerBox = el.querySelector('.auth-timer');
      const timerText = el.querySelector('.auth-timer__text');
      const resendBtn = el.querySelector('#au-resend');
      const confirmBtn = el.querySelector('#au-confirm');

      const showErr = (text) => { errEl.textContent = text || ''; errEl.hidden = !text; box.classList.toggle('is-invalid', !!text); };
      const code = () => cells.map((c) => App.fmt.latin(c.value)).join('');
      const clearCells = () => cells.forEach((c) => { c.value = ''; c.dataset.v = ''; });
      function renderTest() {
        if (!p.testCode) { testEl.hidden = true; return; }
        const [a, b] = t('auth.verify.testCode').split('{code}');
        testEl.innerHTML = String(h`${App.icon('info', { size: 22 })}<span>${a}<b class="auth-test__code" dir="ltr">${App.fmt.digits(p.testCode)}</b>${b || ''}</span>`);
        testEl.hidden = false;
      }
      function tick() {
        const left = Math.max(0, Math.ceil((p.resendAt - Date.now()) / 1000));
        timerText.textContent = left > 0 ? t('auth.verify.resendIn', { n: left }) : t('auth.verify.canResend');
        timerBox.classList.toggle('is-done', left === 0);
      }

      // code cells: Dari and Latin digits, paste from the clipboard, auto-advance, backspace to the previous cell
      const show = (d) => App.fmt.digits(d);
      function fill(from, digits) {
        let i = from;
        for (const d of digits) { if (i >= cells.length) break; cells[i].value = show(d); cells[i].dataset.v = d; i++; }
        const empty = cells.findIndex((c) => !c.value);
        cells[empty >= 0 ? empty : cells.length - 1].focus();
      }
      cells.forEach((c, i) => {
        c.addEventListener('input', () => {
          let d = App.fmt.latin(c.value).replace(/\D/g, '');
          const prev = c.dataset.v || '';
          if (prev && d.length === 2) d = d.startsWith(prev) ? d.slice(1) : d.slice(0, 1); // typing over a digit
          showErr(null);
          if (!d) { c.value = ''; c.dataset.v = ''; return; }
          if (d.length > 1) { fill(i, d.slice(0, cells.length - i)); return; } // code autofill from the SMS
          c.value = show(d); c.dataset.v = d;
          if (i < cells.length - 1) cells[i + 1].focus();
        });
        c.addEventListener('keydown', (e) => {
          if (e.key === 'Backspace' && !c.value && i > 0) { e.preventDefault(); cells[i - 1].value = ''; cells[i - 1].dataset.v = ''; cells[i - 1].focus(); }
          else if (e.key === 'ArrowLeft' && i > 0) { e.preventDefault(); cells[i - 1].focus(); }
          else if (e.key === 'ArrowRight' && i < cells.length - 1) { e.preventDefault(); cells[i + 1].focus(); }
        });
        c.addEventListener('paste', (e) => {
          e.preventDefault();
          const text = (e.clipboardData || root().clipboardData || { getData: () => '' }).getData('text');
          const d = App.fmt.latin(text).replace(/\D/g, '');
          if (!d) return;
          showErr(null);
          if (d.length >= cells.length) { clearCells(); fill(0, d.slice(0, cells.length)); } else fill(i, d);
        });
        c.addEventListener('focus', () => c.select());
      });

      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const value = code();
        if (!/^\d{6}$/.test(value)) { showErr(t('auth.err.incomplete')); (cells.find((c) => !c.value) || cells[0]).focus(); return; }
        busy(confirmBtn, true);
        try {
          const r = await App.api('POST', '/api/auth/verify', { phone: p.phone, code: value });
          pending.clear();
          draftPhone = null;
          App.setUser(r.user || null);
          if (p.loc && Number.isFinite(Number(p.loc.lat))) { // location from 03 → saved on the server (alerts within 2 km)
            App.api('POST', '/api/me/location', { lat: p.loc.lat, lon: p.loc.lon, accuracy: p.loc.accuracy }).catch(() => {});
          }
          App.toast(t('auth.verify.welcome'), 'ok');
          App.replace(r.is_new ? withNext('#/profile?new=1', next) : (next || App.config.home));
        } catch (err) {
          showErr(authError(err));
          if (err.code === 'wrong_code' || err.code === 'bad_code') { clearCells(); cells[0].focus(); }
          busy(confirmBtn, false);
        }
      });

      resendBtn.addEventListener('click', async () => {
        const left = Math.ceil((p.resendAt - Date.now()) / 1000);
        if (left > 0) { showErr(t('auth.err.tooSoon', { n: left })); return; }
        busy(resendBtn, true);
        try {
          p = await requestCode(p.phone, { from: p.from, next: p.next, loc: p.loc });
          clearCells(); showErr(null); renderTest(); tick();
          App.toast(t('auth.verify.resent'), 'ok');
          cells[0].focus();
        } catch (err) {
          const wait = Number(err.data && err.data.retry_after);
          if (err.code === 'too_soon' && wait) { p.resendAt = Date.now() + wait * 1000; pending.set(p); tick(); }
          showErr(authError(err));
        } finally { busy(resendBtn, false); }
      });
      el.querySelector('#au-edit').addEventListener('click', () => App.back(p.from || App.config.login));

      renderTest();
      tick();
      const timer = setInterval(tick, 1000);
      setTimeout(() => { if (el.isConnected && !el.querySelector('.otp__cell:focus')) cells[0].focus(); }, 60);
      return () => clearInterval(timer);
    },
  });

  // ---------------------------------------------------------------- 01 profile: new resident and editing
  /** Section card 01: title, "*", "(اختیاری)/(ضروری)", icon (at the edge or before the title). */
  function sec(o) {
    const icon = o.icon ? h`<span class="field__icon">${App.icon(o.icon)}</span>` : '';
    const label = h`<span>${o.title}</span>${o.req ? h` <span class="field__req" aria-hidden="true">*</span> <span class="field__opt">${t('common.required')}</span>` : ''}${o.opt ? h` <span class="field__opt">${t('common.optional')}</span>` : ''}`;
    const head = o.labelFor ? h`<label class="field__label" for="${o.labelFor}">${label}</label>` : h`<h2 class="field__label">${label}</h2>`;
    return h`<section class="card form-card auth-sec"${o.id ? h` id="${o.id}"` : ''}>
      <div class="field__head${o.iconFirst ? ' auth-sec__head--start' : ''}">${o.iconFirst ? icon : ''}${head}${o.iconFirst ? '' : icon}</div>
      ${o.content}
    </section>`;
  }
  function bloodTiles(value) {
    const v = String(value || '');
    const tile = (val, label, icon) => h`<label class="tile${val === 'unknown' ? ' tile--wide' : ''}">
        <input type="radio" name="au-blood" value="${val}"${v === val ? h` checked` : ''}>
        <span class="tile__text">${label}</span>${icon ? App.icon(icon, { size: 18 }) : ''}</label>`;
    return h`<div class="auth-blood" role="radiogroup" aria-label="${t('auth.signup.blood')}">
        <div class="tiles" dir="ltr" style="--cols:4">${BLOOD.map((b) => tile(b, b))}</div>
        <div class="tiles" style="--cols:1">${tile('unknown', t('auth.signup.bloodUnknown'), 'help')}</div>
      </div>
      <p class="field__hint">${t('auth.signup.bloodHint')}</p>`;
  }
  function contactsBlock() {
    return h`${ui.button({ text: t('auth.signup.addContact'), kind: 'outline', icon: 'plus', small: true, id: 'au-c-add' })}
      <div class="auth-cform" id="au-cform" hidden>
        <input class="input" id="au-c-name" maxlength="100" autocomplete="off" placeholder="${t('auth.contact.namePlaceholder')}" aria-label="${t('auth.signup.contactName')}">
        ${phoneField({ id: 'au-c-phone', autocomplete: 'off', label: t('auth.signup.contactPhone') })}
        <div class="auth-cform__row">
          ${ui.button({ text: t('common.add'), small: true, icon: 'plus', id: 'au-c-ok' })}
          ${ui.button({ text: t('common.cancel'), kind: 'outline', small: true, id: 'au-c-cancel' })}
        </div>
      </div>
      <ul class="auth-contacts" id="au-contacts"></ul>
      <p class="auth-contacts__min">${t('auth.signup.contactsMin')}</p>
      <p class="field__error" id="au-c-error" role="alert" hidden></p>`;
  }
  function homeBlock() {
    return h`<div class="auth-home__btns">
        ${ui.button({ text: t('loc.enterAddress'), kind: 'outline', icon: 'edit', small: true, id: 'au-h-addr' })}
        ${ui.button({ text: t('loc.useCurrent'), kind: 'outline-accent', icon: 'locate', small: true, id: 'au-h-gps' })}
      </div>
      <button class="btn btn--ghost btn--sm auth-home__map" type="button" id="au-h-map">${App.icon('map', { size: 20 })}<span>${t('auth.home.pickMap')}</span></button>
      <div class="auth-home__search" id="au-h-search" hidden>
        <div class="auth-home__row">
          <input class="input" id="au-h-q" type="search" maxlength="200" enterkeyhint="search" placeholder="${t('auth.home.searchPlaceholder')}" aria-label="${t('loc.enterAddress')}">
          <button class="btn btn--primary btn--sm btn--inline" type="button" id="au-h-go" aria-label="${t('common.search')}">${App.icon('search', { size: 20 })}</button>
        </div>
        <ul class="auth-home__results" id="au-h-results"></ul>
      </div>
      <p class="auth-home__status" id="au-h-status" role="status" hidden></p>
      <div class="auth-home__chosen" id="au-h-chosen" hidden></div>`;
  }
  /** Home point on the map (a separate window: the resident's App.location is left alone). → {lat, lon} or null. */
  function pickOnMap(center) {
    return new Promise((resolve) => {
      let chosen = null;
      let mapH = null;
      const sheet = ui.sheet(h`
        <p class="muted">${t('loc.pickHint')}</p>
        <div class="map map--pick" data-au-map></div>
        <div class="stack stack--sm">
          <button class="btn btn--primary" type="button" data-au-ok disabled>${App.icon('check')}<span>${t('loc.confirm')}</span></button>
          <button class="btn btn--outline" type="button" data-au-cancel>${t('common.cancel')}</button>
        </div>`, { title: t('auth.home.pickTitle'), onClose: () => { if (mapH) mapH.destroy(); resolve(chosen); } });
      const ok = sheet.el.querySelector('[data-au-ok]');
      mapH = App.map(sheet.el.querySelector('[data-au-map]'), {
        center: center || App.config.mapCenter, zoom: center ? 15 : App.config.mapZoom, scoped: false,
        onPick(pt) { chosen = pt; ok.disabled = false; },
      });
      ok.addEventListener('click', () => sheet.close());
      sheet.el.querySelector('[data-au-cancel]').addEventListener('click', () => { chosen = null; sheet.close(); });
    });
  }
  const round6 = (x) => Math.round(Number(x) * 1e6) / 1e6;
  let profileDraft = null; // what was typed in the profile — survives a redraw on language change

  App.screen('profile', {
    route: '#/profile',
    auth: 'user',
    title: 'auth.signup.title',
    async render(params, el) {
      const isNew = params.new === '1';
      const next = safeNext(params.next);
      let user = App.state.user || {};
      try {
        const r = await App.api('GET', App.config.me);
        if (r && r.user) user = r.user;
        else { App.replace(withNext(App.config.login, '#/profile')); return undefined; }
      } catch (err) {
        if (err.status === 401) { App.setUser(null); App.replace(withNext(App.config.login, '#/profile')); return undefined; }
        // no network — show what we already know
      }
      if (!el.isConnected) return undefined;
      const draft = profileDraft && profileDraft.id === user.id && profileDraft.isNew === isNew ? profileDraft : null;
      profileDraft = null;
      if (draft) user = Object.assign({}, user, { name: draft.name, blood_group: draft.blood, health: draft.health });
      const st = {
        contacts: draft ? draft.contacts : (Array.isArray(user.contacts) ? user.contacts : []).filter((c) => c && c.phone).map((c) => ({ name: c.name || '', phone: c.phone })),
        home: draft ? draft.home : (user.home_lat != null && user.home_lon != null) ? { lat: Number(user.home_lat), lon: Number(user.home_lon), address: user.home_address || '' } : null,
        searchAt: 0,
      };
      const health = Array.isArray(user.health) && (user.health.length || draft) ? user.health : ['none'];
      const title = isNew ? t('auth.signup.title') : t('auth.profile.title');
      document.title = title + ' — ' + t('app.brand');
      el.classList.add('auth-screen', 'screen--gray');
      el.innerHTML = String(h`
        <div class="auth auth--profile${isNew ? ' is-new' : ' is-edit'}">
          ${ui.header({ title, back: App.config.home })}
          <div class="auth auth-form">
            ${sec({ title: t('auth.signup.name'), opt: true, icon: 'user', labelFor: 'au-name', content: h`<input class="input" id="au-name" maxlength="100" autocomplete="name" placeholder="${t('auth.signup.namePlaceholder')}" value="${user.name || ''}">` })}
            ${sec({ title: t('auth.phone'), req: true, icon: 'mobile', labelFor: 'au-phone', content: h`<input class="input auth-ro" id="au-phone" dir="ltr" readonly aria-readonly="true" value="${phoneInput(user.phone)}">` })}
            ${sec({ title: t('auth.signup.blood'), opt: true, icon: 'drop', content: bloodTiles(user.blood_group) })}
            ${sec({ title: t('auth.signup.health'), opt: true, icon: 'heart', content: h`<div class="auth-health">${ui.tiles({ name: 'au-health', check: true, cols: 3, wide: ['none'], value: health, items: HEALTH.map(([v, k]) => ({ value: v, label: t(k) })) })}</div>` })}
            ${sec({ title: t('auth.signup.contacts'), icon: 'users', id: 'au-contacts-sec', content: contactsBlock() })}
            ${sec({ title: t('auth.signup.home'), opt: true, icon: 'pin-fill', iconFirst: true, content: homeBlock() })}
          </div>
          <div class="auth auth-actions">
            <p class="field__error auth-form-error" role="alert" hidden></p>
            ${ui.button({ text: isNew ? t('auth.signup.create') : t('auth.profile.save'), icon: 'check', id: 'au-save' })}
            ${isNew ? h`${ui.button({ text: t('auth.signup.skip'), kind: 'outline', icon: 'skip', edge: true, go: next || App.config.home, id: 'au-skip' })}
              <p class="auth-have">${t('auth.signup.haveAccount')} <a href="${App.config.login}" data-go="${App.config.login}" id="au-have">${t('auth.login')}${App.icon(App.dir() === 'rtl' ? 'chevron-left' : 'chevron-right', { size: 18 })}</a></p>`
            : h`<div class="auth-langs auth-langs--profile" dir="ltr">${ui.langSwitch()}</div>
              ${ui.button({ text: t('auth.logout'), kind: 'outline', icon: 'logout', edge: true, id: 'au-logout', cls: 'auth-logout' })}`}
          </div>
        </div>`);
      const $ = (s) => el.querySelector(s);

      // health: "بدون مشکل خاص" clears the others and vice versa
      const healthInputs = [...el.querySelectorAll('input[name="au-health"]')];
      healthInputs.forEach((i) => i.addEventListener('change', () => {
        if (!i.checked) return;
        healthInputs.forEach((o) => { if (o !== i && (i.value === 'none' || o.value === 'none')) o.checked = false; });
      }));

      // emergency contacts
      const addBtn = $('#au-c-add'), cform = $('#au-cform'), cName = $('#au-c-name'), cPhone = $('#au-c-phone');
      const list = $('#au-contacts'), cErr = $('#au-c-error');
      const showCErr = (text) => { cErr.textContent = text || ''; cErr.hidden = !text; };
      function drawContacts() {
        list.innerHTML = String(h`${st.contacts.map((c, i) => h`<li class="auth-contact">
            <button class="btn-icon auth-contact__del" type="button" data-del="${i}" aria-label="${t('auth.contact.delete')}">${App.icon('trash', { size: 22 })}</button>
            <span class="auth-contact__text">${c.name ? h`<span class="auth-contact__name">${c.name}:</span> ` : ''}<span class="auth-contact__phone">${App.phone.display(c.phone)}</span></span>
            <a class="btn-icon auth-contact__call" href="${App.phone.tel(c.phone)}" aria-label="${t('common.call')}">${App.icon('phone', { size: 22 })}</a>
          </li>`)}`);
        list.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
          st.contacts.splice(Number(b.dataset.del), 1);
          drawContacts();
        }));
        addBtn.disabled = st.contacts.length >= MAX_CONTACTS;
      }
      const openForm = (on) => {
        cform.hidden = !on; addBtn.hidden = on;
        if (on) cName.focus(); else { cName.value = ''; cPhone.value = ''; ui.fieldError(cPhone, null); }
      };
      function addContact() {
        const phone = App.phone.normalize(cPhone.value);
        let error = null;
        if (!phone) error = t('error.phone');
        else if (phone === user.phone) error = t('auth.err.contactSelf');
        else if (st.contacts.some((c) => c.phone === phone)) error = t('auth.err.contactDup');
        else if (st.contacts.length >= MAX_CONTACTS) error = t('auth.err.contactsMax', { n: MAX_CONTACTS });
        if (error) { ui.fieldError(cPhone, error); cPhone.focus(); return false; }
        st.contacts.push({ name: cName.value.trim().slice(0, 100), phone });
        openForm(false);
        showCErr(null);
        drawContacts();
        return true;
      }
      addBtn.addEventListener('click', () => openForm(true));
      $('#au-c-ok').addEventListener('click', addContact);
      $('#au-c-cancel').addEventListener('click', () => { openForm(false); addBtn.focus(); });
      cName.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); cPhone.focus(); } });
      cPhone.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addContact(); } });
      cPhone.addEventListener('input', () => ui.fieldError(cPhone, null));
      drawContacts();

      // home location: address (Nominatim via the server), GPS, point on the map
      const statusEl = $('#au-h-status'), chosen = $('#au-h-chosen'), searchBox = $('#au-h-search');
      const qInput = $('#au-h-q'), goBtn = $('#au-h-go'), results = $('#au-h-results'), gpsBtn = $('#au-h-gps');
      function status(text, warn, mapAction) {
        statusEl.hidden = !text;
        statusEl.classList.toggle('is-warn', !!warn);
        statusEl.innerHTML = text ? String(h`<span>${text}</span>${mapAction ? h` <button class="auth-link" type="button" data-au-pick>${t('auth.home.pickMap')}</button>` : ''}`) : '';
        const b = statusEl.querySelector('[data-au-pick]');
        if (b) b.addEventListener('click', pickHome);
      }
      function drawHome() {
        if (!st.home) { chosen.hidden = true; chosen.innerHTML = ''; return; }
        chosen.hidden = false;
        chosen.innerHTML = String(h`${App.icon('pin-fill', { size: 24 })}
          <span class="auth-home__text"><span class="auth-home__addr">${st.home.address || t('auth.home.point')}</span><span class="auth-home__coord">${App.fmt.coord(st.home.lat, st.home.lon)}</span></span>
          <button class="btn-icon" type="button" id="au-h-clear" aria-label="${t('auth.home.remove')}">${App.icon('close', { size: 20 })}</button>`);
        chosen.querySelector('#au-h-clear').addEventListener('click', () => { st.home = null; drawHome(); });
      }
      /** Set home; with no address, ask the server for the place name (one request, the server spares Nominatim). */
      function setHome(lat, lon, address) {
        const home = { lat: round6(lat), lon: round6(lon), address: address || '' };
        st.home = home;
        status(null);
        drawHome();
        if (address) return;
        App.api('GET', `/api/geo/name?lat=${home.lat}&lon=${home.lon}&lang=${App.lang}`).then((r) => {
          if (st.home === home && r && r.name) { home.address = String(r.name).slice(0, 300); drawHome(); }
        }, () => { /* offline — the coordinates stay */ });
      }
      async function pickHome() {
        const s = App.state.location;
        const pt = await pickOnMap(st.home ? [st.home.lat, st.home.lon] : (s ? [s.lat, s.lon] : null));
        if (pt) setHome(pt.lat, pt.lon, '');
      }
      async function search() {
        const q = qInput.value.trim();
        if (!q) { qInput.focus(); return; }
        if (Date.now() - st.searchAt < 1100) return; // Nominatim: at most once a second
        st.searchAt = Date.now();
        busy(goBtn, true);
        status(t('auth.home.searching'));
        results.innerHTML = '';
        try {
          const r = await App.api('GET', `/api/geo/search?q=${encodeURIComponent(q)}&lang=${App.lang}`);
          const found = r && Array.isArray(r.results) ? r.results : [];
          if (r && r.offline) { status(t('auth.home.offline'), true, true); return; }
          if (!found.length) { status(t('auth.home.noResults'), true, true); return; }
          status(null);
          results.innerHTML = String(h`${found.map((x, i) => h`<li><button class="auth-home__result" type="button" data-i="${i}">${App.icon('pin', { size: 20 })}<span class="auth-home__rtext"><span class="auth-home__rname">${x.name || x.full}</span>${x.full && x.full !== x.name ? h`<span class="auth-home__rfull">${x.full}</span>` : ''}</span></button></li>`)}`);
          results.querySelectorAll('[data-i]').forEach((b) => b.addEventListener('click', () => {
            const x = found[Number(b.dataset.i)];
            setHome(x.lat, x.lon, String(x.full || x.name || '').slice(0, 300));
            results.innerHTML = '';
            searchBox.hidden = true;
          }));
        } catch (err) {
          status(err.status === 0 ? t('auth.home.offline') : (err.message || t('error.generic')), true, true);
        } finally { busy(goBtn, false); }
      }
      $('#au-h-addr').addEventListener('click', () => { searchBox.hidden = !searchBox.hidden; if (!searchBox.hidden) qInput.focus(); });
      goBtn.addEventListener('click', search);
      qInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
      $('#au-h-map').addEventListener('click', pickHome);
      gpsBtn.addEventListener('click', async () => {
        busy(gpsBtn, true);
        status(t('auth.home.locating'));
        try {
          const g = await App.location.gps();
          setHome(g.lat, g.lon, '');
        } catch (err) {
          status(t('auth.home.gpsFailed'), true, true);
        } finally { busy(gpsBtn, false); }
      });
      drawHome();

      // save: PUT /api/me
      const saveBtn = $('#au-save'), formErr = $('.auth-form-error');
      const showFormErr = (text) => { formErr.textContent = text || ''; formErr.hidden = !text; };
      saveBtn.addEventListener('click', async () => {
        showFormErr(null);
        showCErr(null);
        if (!cform.hidden && (cName.value.trim() || cPhone.value.trim()) && !addContact()) { cPhone.scrollIntoView({ block: 'center' }); return; }
        if (!st.contacts.length) {
          showCErr(t('auth.err.contactsMin'));
          $('#au-contacts-sec').scrollIntoView({ behavior: 'smooth', block: 'center' });
          return;
        }
        const body = {
          name: $('#au-name').value.trim().slice(0, 100),
          blood_group: (el.querySelector('input[name="au-blood"]:checked') || {}).value || '',
          health: healthInputs.filter((i) => i.checked).map((i) => i.value),
          contacts: st.contacts,
          home_lat: st.home ? st.home.lat : null,
          home_lon: st.home ? st.home.lon : null,
          home_address: st.home ? String(st.home.address || '').slice(0, 300) : '',
        };
        busy(saveBtn, true);
        try {
          const r = await App.api('PUT', '/api/me', body);
          App.setUser(r.user || App.state.user);
          App.toast(isNew ? t('auth.profile.created') : t('auth.profile.saved'), 'ok');
          if (isNew) App.replace(next || App.config.home); else App.back(App.config.home);
        } catch (err) {
          if (err.code === 'bad_contacts') showCErr(err.message); else showFormErr(err.message || t('error.generic'));
          busy(saveBtn, false);
        }
      });

      // language (profile editing): remember what was typed before the redraw
      const offLang = App.on('lang', () => {
        profileDraft = {
          id: user.id, isNew,
          name: $('#au-name').value,
          blood: (el.querySelector('input[name="au-blood"]:checked') || {}).value || '',
          health: healthInputs.filter((i) => i.checked).map((i) => i.value),
          contacts: st.contacts.slice(),
          home: st.home,
        };
      });

      // sign out (profile editing)
      const out = $('#au-logout');
      if (out) {
        out.addEventListener('click', async () => {
          busy(out, true);
          try { await App.api('POST', '/api/auth/logout', {}); } catch (err) {
            if (err.status === 0) { App.toast(err.message, 'error'); busy(out, false); return; }
          }
          pending.clear();
          draftPhone = null;
          App.setUser(null);
          App.replace(App.config.login);
        });
      }
      return offLang;
    },
  });

  // ---------------------------------------------------------------- start: not signed in → #/login
  // On an empty address the core goes to App.config.home. Here, before the first route (the 'user' event of App.start):
  // no resident → straight to #/login, no flash of home. Guest — /api/me answers {user: null} (or 401): stay on sign-in;
  // no sign-in server (404, as in the core test on a static server) or no network — back to #/home, as before.
  if (typeof window !== 'undefined' && window.history && App.on) {
    const off = App.on('user', (u) => {
      off();
      const hash = window.location.hash;
      if (u || (hash && hash !== '#' && hash !== '#/')) return;
      window.history.replaceState(null, '', App.config.login);
      const home = () => { const cur = App.current(); if (cur && cur.name === 'login') App.replace(App.config.home); };
      App.api('GET', App.config.me, null, { timeout: 5000 }).then(
        (r) => { if (r && r.user) { App.setUser(r.user); home(); } },  // an admin but no resident — stay on sign-in
        (err) => { if (!err || err.status !== 401) home(); });
    });
  }
})(typeof window !== 'undefined' ? window.App : globalThis.App);
