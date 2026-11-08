/* Group "sos" (figma 10, 11/13, 12) — the resident's screens:
     #/sos/now  — urgent SOS (10): 3 s to cancel → POST /api/sos {kind:'default', …} → the "ارسال‌شده" card;
     #/sos      — chat with rescuers (11/13): GET /api/sos/current; the first message → POST /api/sos {kind:'custom'}
                  → #/sos/sent; then POST /api/sos/{id}/messages; rescuer replies — App.live 'sos_message';
     #/sos/sent — "پیام ارسال شد!" (12), moves on to the chat by itself.
   While a case is open, the location is sent every 30 s (POST /api/sos/{id}/location), even when the resident is on
   other tabs: the timer is here, not in the core. Offline, SOS and messages wait in a queue (localStorage) and go
   out by themselves, without duplicates. Server — app/server/sos.py; new strings — i18n/<lang>/sos.json. */
(function (App) {
  'use strict';
  const h = App.html;
  const t = (k, v) => App.t(k, v);
  const icon = (name, o) => App.icon(name, o);

  const EVERY_MS = 30000;      // the location — every 30 s
  const COUNTDOWN = 3;         // seconds to cancel an urgent SOS
  const TO_CHAT = 8;           // 12 → the chat by itself after this many seconds
  const LOC_WAIT_MS = 10000;   // do not wait longer for the location: the SOS goes without it
  const GPS_WAIT_MS = 20000;   // one GPS attempt in the timer (including the browser's permission prompt)
  const KEY = 'sosCase';      // id of the last case (App.store) — to show "closed" after a reload

  // ---------------------------------------------------------------- case state (shared by the screens)
  const S = { case: null, timer: null, timerId: null, gps: null, fixAt: null, busy: false, offs: [], unread: 0,
    last: { case: null, id: 0 } };  // last — the case's last known message (the cut-off for the duplicate check)
  App.sos = { state: S, every: EVERY_MS }; // for tests and for other groups: read only
  const changed = () => App.emit('sos', S);

  function setCase(c) {
    if (!c) return;
    S.case = c;
    App.store.set(KEY, c.id);
    changed();
  }

  /** Remember the case's last message (the queue uses it to tell "already arrived" from "not yet"). */
  function note(caseId, messages) {
    if (caseId == null) return;
    if (S.last.case !== caseId) S.last = { case: caseId, id: 0 };
    (messages || []).forEach((m) => { if (m && m.id > S.last.id) S.last.id = m.id; });
  }
  const openId = () => (S.case && S.case.status === 'open' ? S.case.id : null);

  // the server's text_key → a dictionary string (the Pashto and English translation); no key — the server's text as is
  const KEYS = { sos_default: 'sos.defaultText', sos_auto_received: 'sos.autoReply', sos_auto_wait: 'sos.teamReply' };
  const textOf = (m) => (m.text_key && KEYS[m.text_key] ? t(KEYS[m.text_key]) : m.text);

  const wait = (ms) => new Promise((r) => { setTimeout(r, ms); });
  const within = (p, ms) => Promise.race([Promise.resolve(p).catch(() => null), wait(ms).then(() => null)]);
  const hasLoc = (c) => !!c && c.lat != null && c.lon != null;

  /** Location for an SOS: GPS → saved (no map window), no longer than LOC_WAIT_MS; none — null. */
  async function locate() {
    const loc = await within(App.location.get({ pick: false }), LOC_WAIT_MS);
    S.gps = !!(loc && loc.source === 'gps');
    return loc;
  }
  /** Battery and network — only what the browser gives (null — it does not; the server does not store such values). */
  async function deviceInfo() {
    const d = await within(App.device.get(), 3000);
    return d ? { battery: d.battery, signal: d.signal } : {};
  }
  function point(loc) {
    if (!loc) return {};
    const p = { lat: loc.lat, lon: loc.lon };
    if (loc.accuracy != null) p.accuracy = loc.accuracy;
    return p;
  }

  // ---------------------------------------------------------------- location every 30 s while the case is open
  const tracker = {
    start(c) {
      if (!c || c.status !== 'open') return;
      setCase(c);
      if (S.timerId === c.id) return;
      tracker.stop();
      S.timerId = c.id;
      S.timer = setInterval(tracker.tick, EVERY_MS);
      S.offs = [App.live.on('sos_case', onCaseEvent, { global: true }),
        App.live.on('sos_message', onMessageEvent, { global: true })];
      if (S.gps === null) within(App.location.gps(15000), GPS_WAIT_MS).then((p) => { S.gps = !!p; if (p) App.location.save(p); changed(); });
      changed();
    },
    stop() {
      clearInterval(S.timer);
      S.timer = null;
      S.timerId = null;
      S.offs.splice(0).forEach((off) => off());
    },
    /** Browser GPS only: a saved or map-picked location is never passed off as "live". */
    async tick() {
      const id = S.timerId;
      if (!id || S.busy) return;
      S.busy = true;
      try {
        const pos = await within(App.location.gps(15000), GPS_WAIT_MS);
        S.gps = !!pos;
        if (!pos) { changed(); return; }
        App.location.save(pos);
        const r = await App.api('POST', `/api/sos/${id}/location`, Object.assign(point(pos), await deviceInfo()));
        S.fixAt = Date.now();
        setCase(r.case);
      } catch (e) {
        if (e && [403, 404, 409].includes(e.status)) {
          tracker.stop();
          if (e.status === 409 && S.case && S.case.id === id) { S.case = Object.assign({}, S.case, { status: 'closed' }); changed(); }
        }
      } finally { S.busy = false; }
    },
  };
  App.sos.tracker = tracker;

  function onCaseEvent(d) {
    const c = d && d.case;
    if (!c || !S.case || c.id !== S.case.id) return;
    S.case = Object.assign({}, S.case, c);
    if (c.status !== 'open') tracker.stop();
    changed();
  }
  /** A rescuer's reply while the resident is not in the chat: a toast and the number on the "مسج" tab. */
  function onMessageEvent(d) {
    if (!d || !d.message || !S.case || d.case_id !== S.case.id || d.message.sender !== 'rescuer') return;
    const cur = App.current();
    if (cur && cur.name === 'sos-chat') return;
    S.unread += 1;
    App.setBadge('resident', 'message', S.unread);
    App.toast(t('sos.newReply'), 'info');
  }

  // on start and when the resident changes: an open case → the location timer
  let seen;
  App.on('user', (u) => {
    const who = u && u.id != null ? 'u' + u.id : 'guest';
    if (who === seen) return;
    seen = who;
    App.api('GET', '/api/sos/current').then((r) => {
      if (r && r.case) note(r.case.id, r.messages);
      if (r && r.case && r.case.status === 'open') tracker.start(r.case);
      else if (S.timer) tracker.stop();
    }, () => { /* no server — nothing to send */ });
    if (queue.has()) queue.flush();
  });

  // ---------------------------------------------------------------- offline queue: SOS and chat messages
  // No network or no answer from the server — the send waits in localStorage and goes out by itself: when the
  // network is back (core event 'online') and every 15 s, in order. No duplicates: if the last attempt may have
  // arrived (cut-off answer, timeout, server error), first ask the server whether this message is already there.
  const Q_KEY = 'sosQueue';
  const RETRY_MS = 15000;
  const CALL = '119';            // "تماس با ۱۱۹" when offline (the number was confirmed by the project group)
  const waiting = new Set();     // qids queue.send waits for — their answers go to it, others go out as an event
  const done = new Map();        // qid → the server's answer
  const failed = new Map();      // qid → a 4xx error (no point retrying)
  const claimed = new Set();     // ids of server messages already matched with sends from the queue
  let flushing = null, retry = null, netDown = false;

  const retryable = (e) => !e || e.status === 0 || e.status >= 500 || e.status === 408 || e.status === 429;
  // could a failed attempt still have reached the server (then check before retrying)
  const mayHaveArrived = (e) => !!e && (e.code === 'timeout' || e.status >= 500
    || (e.status === 0 && !(typeof navigator !== 'undefined' && navigator.onLine === false)));

  const queue = {
    items() { const v = App.store.get(Q_KEY, []); return Array.isArray(v) ? v : []; },
    has() { return queue.items().length > 0; },
    save(list) { App.store.set(Q_KEY, list); queue.arm(); App.emit('sos:queue', list); },
    /** Add: {type: 'sos', body: the POST /api/sos body} | {type: 'msg', caseId, body: {text}}; from — the screen. */
    add(item) {
      if (!item.qid) Object.assign(item, { qid: 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7), at: Date.now(), tries: 0, maybe: false });
      queue.save(queue.items().concat([item]));
      return item;
    },
    remove(qid) { queue.save(queue.items().filter((x) => x.qid !== qid)); },
    /** Waiting for the network: everything except a first attempt in progress. */
    pending() { return queue.items().filter((x) => !(x.tries === 0 && waiting.has(x.qid))); },
    arm() {
      const need = queue.has();
      if (need && !retry) retry = setInterval(() => { queue.flush(); }, RETRY_MS);
      if (!need && retry) { clearInterval(retry); retry = null; }
    },
    /** Send the queue in order; already running — the same attempt. Several tabs take turns (Web Locks). */
    flush() {
      if (!flushing) {
        const locks = typeof navigator !== 'undefined' && navigator.locks;
        const p = locks && locks.request ? locks.request('panah-sos-queue', flushRun) : flushRun();
        flushing = Promise.resolve(p).catch((e) => { console.error(e); }).then(() => { flushing = null; });
      }
      return flushing;
    },
    /** Queue and try at once → the server's answer; null — waiting for the network; a 4xx error — throws. */
    async send(item) {
      Object.assign(item, { qid: 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7), at: Date.now(), tries: 0, maybe: false });
      waiting.add(item.qid);  // before writing to the queue: screens do not flash "no network" during the first attempt
      queue.add(item);
      try {
        await queue.flush();
        // an attempt may have started before the add — then one more
        if (!done.has(item.qid) && !failed.has(item.qid) && !netDown && queue.items().some((x) => x.qid === item.qid)) await queue.flush();
      } finally { waiting.delete(item.qid); }
      if (failed.has(item.qid)) { const e = failed.get(item.qid); failed.delete(item.qid); throw e; }
      const res = done.get(item.qid) || null;
      done.delete(item.qid);
      return res;
    },
  };
  App.sos.queue = queue;

  async function flushRun() {
    netDown = false;
    for (;;) {
      const it = queue.items()[0];
      if (!it) return;
      let res = null;
      try {
        if (it.maybe) res = await alreadyThere(it);
        if (!res) res = it.type === 'msg'
          ? await App.api('POST', `/api/sos/${it.caseId}/messages`, it.body)
          : await App.api('POST', '/api/sos', it.body);
      } catch (e) {
        if (retryable(e)) {
          netDown = true;
          const list = queue.items();
          const x = list.find((y) => y.qid === it.qid);
          if (x) { x.tries += 1; x.maybe = x.maybe || mayHaveArrived(e); queue.save(list); }
          return;
        }
        queue.remove(it.qid);
        if (waiting.has(it.qid)) failed.set(it.qid, e); else App.emit('sos:failed', { item: it, error: e });
        continue;
      }
      if (!res.message) res.message = [...(res.messages || [])].reverse().find((m) => m.sender === 'user') || null;
      if (res.message) claimed.add(res.message.id);
      queue.remove(it.qid);
      delivered(it, res);
    }
  }
  /** The previous attempt may have arrived: is this message on the server? → an answer like a send's, or null. */
  async function alreadyThere(it) {
    const since = it.at - 120000; // the phone's and the server's clocks may differ slightly
    const same = (m) => m.sender === 'user' && !claimed.has(m.id) && Date.parse(m.created_at) >= since
      && (it.type === 'sos' && it.body.kind === 'default' ? m.text_key === 'sos_default' : m.text === it.body.text);
    if (it.type === 'msg') {
      const r = await App.api('GET', `/api/sos/${it.caseId}/messages?after=${it.after || 0}`);
      const m = (r.messages || []).find(same);
      return m ? { case: r.case, message: m, messages: r.messages.filter((x) => x.id >= m.id) } : null;
    }
    const r = await App.api('GET', '/api/sos/current');
    if (!r.case) return null;
    const m = (r.messages || []).find((x) => same(x) && (r.case.id !== it.caseId || x.id > (it.after || 0)));
    return m ? { case: r.case, message: m, messages: r.messages } : null;
  }
  /** Arrived: location timer, cut-off; the answer → the waiting queue.send or screens via 'sos:sent' (no one — a toast). */
  function delivered(it, res) {
    if (res.case) {
      note(res.case.id, res.messages || [res.message]);
      if (res.case.status === 'open') tracker.start(res.case); else setCase(res.case);
    }
    if (it.type === 'sos' && it.body.lat != null) S.fixAt = it.at;
    if (waiting.has(it.qid)) { done.set(it.qid, res); return; }
    const ev = { item: it, res, handled: false };
    App.emit('sos:sent', ev);
    if (!ev.handled) App.toast(t('sos.queueSent'), 'ok');
  }
  queue.arm();
  App.on('online', () => { queue.flush(); });  // core event: the network is back

  /** The "no network — queued" strip and a big call to 119. */
  function offlineBox() {
    return h`<div class="sos-offline" role="alert" data-offline>
      <p class="sos-offline__text">${icon('alert', { size: 22 })}<span>${t('sos.offline')}</span></p>
      <a class="btn btn--danger sos-offline__call" href="${App.phone.tel(CALL)}">${icon('phone')}<span>${t('sos.call', { n: App.phone.display(CALL) })}</span></a>
    </div>`;
  }

  /** A map window if there is no location; the picked location goes to the server. → case or null. */
  async function pickAndSend(id) {
    const loc = await App.location.pick();
    if (!loc) return null;
    const r = await App.api('POST', `/api/sos/${id}/location`, Object.assign(point(loc), await deviceInfo()));
    S.fixAt = Date.now();
    setCase(r.case);
    return r.case;
  }

  // ---------------------------------------------------------------- screen pictures (our own SVG)
  const svg = (s) => App.raw(s);
  const ART = {
    siren: svg(`<svg viewBox="0 0 64 64" width="58" height="58" aria-hidden="true"><defs><linearGradient id="sos-siren-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FF5B4F"/><stop offset="1" stop-color="#D81E17"/></linearGradient></defs>`
      + `<path d="M32 3.5v6.5M12.5 9.5l4.3 5M51.5 9.5l-4.3 5" stroke="#E5372B" stroke-width="3.6" stroke-linecap="round" fill="none"/>`
      + `<path d="M13.5 47V36.5a18.5 18.5 0 0 1 37 0V47z" fill="url(#sos-siren-g)"/>`
      + `<path d="M20.5 30a12.5 12.5 0 0 1 7-8" stroke="#fff" stroke-opacity=".5" stroke-width="3" stroke-linecap="round" fill="none"/>`
      + `<text x="32" y="43" text-anchor="middle" font-size="11.5" font-weight="800" fill="#fff" font-family="Vazirmatn, system-ui, sans-serif">SOS</text>`
      + `<rect x="8.5" y="47" width="47" height="8.5" rx="3.5" fill="#596069"/></svg>`),
    building: svg(`<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="currentColor"><path d="M3 21V9.5l5-2.5v3.2l5-2.7V21z" opacity=".55"/><path d="M11 21V4.5L18.5 2.5V21zM20 21V8.5l1.5.8V21z"/><path d="M13 7h1.6v1.6H13zM16 6h1.6v1.6H16zM13 10.5h1.6v1.6H13zM16 10.5h1.6v1.6H16zM13 14h1.6v1.6H13zM16 14h1.6v1.6H16z" fill="#fff"/><path d="M2 21h20v1.2H2z"/></svg>`),
    pinRing: svg(`<svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true"><circle cx="32" cy="32" r="30" fill="#F2FBF4" stroke="#BFE8CB" stroke-width="2"/><circle cx="32" cy="32" r="20" fill="#E3F6E8" stroke="#9EDDB1" stroke-width="2"/><path d="M32 43.5s8-7.3 8-13.5a8 8 0 0 0-16 0c0 6.2 8 13.5 8 13.5z" fill="#1E9E46"/><circle cx="32" cy="30" r="3" fill="#fff"/></svg>`),
    pinRadio: svg(`<svg viewBox="0 0 80 80" width="76" height="76" aria-hidden="true"><circle cx="40" cy="40" r="38" fill="#E8F7EC"/><ellipse cx="40" cy="62" rx="16" ry="4" fill="#BFE8CB"/><path d="M40 63s17-15.3 17-28.5a17 17 0 0 0-34 0C23 47.7 40 63 40 63z" fill="#1E9E46"/><path d="M35.5 41.5l4.5-11 4.5 11M37 38h6" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M33 29.5a9 9 0 0 1 14 0M30 26.5a13.5 13.5 0 0 1 20 0" stroke="#fff" stroke-width="2" stroke-linecap="round" fill="none"/></svg>`),
    shield: svg(`<svg viewBox="0 0 64 72" width="62" height="70" aria-hidden="true"><path d="M32 4 58 13v21c0 17.2-11.3 29.4-26 34C17.3 63.4 6 51.2 6 34V13z" fill="#EAF2FE" stroke="#8DB8F2" stroke-width="3.5" stroke-linejoin="round"/><circle cx="32" cy="27" r="5.6" fill="#3B86E8"/><circle cx="21" cy="31" r="4.4" fill="#3B86E8"/><circle cx="43" cy="31" r="4.4" fill="#3B86E8"/><path d="M22 47c.8-6.5 4.8-10.5 10-10.5S41.2 40.5 42 47z" fill="#3B86E8"/><path d="M12.5 46c.5-4.8 3.6-8 8-8 1.6 0 3 .4 4.2 1.1-2.4 2-4 4.7-4.6 6.9zM51.5 46c-.5-4.8-3.6-8-8-8-1.6 0-3 .4-4.2 1.1 2.4 2 4 4.7 4.6 6.9z" fill="#3B86E8"/></svg>`),
    bubble: svg(`<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="M12 2.5c5.5 0 9.5 3.8 9.5 8.6s-4 8.6-9.5 8.6c-1.2 0-2.4-.2-3.5-.5L3.5 21l1.3-4.2C3.4 15.3 2.5 13.3 2.5 11.1 2.5 6.3 6.5 2.5 12 2.5z" fill="#2F80ED"/><circle cx="8" cy="11.2" r="1.4" fill="#fff"/><circle cx="12" cy="11.2" r="1.4" fill="#fff"/><circle cx="16" cy="11.2" r="1.4" fill="#fff"/></svg>`),
  };

  // ---------------------------------------------------------------- screen pieces
  const signalBars = { strong: 4, medium: 3, weak: 1, none: 0 };
  function signalIcon(signal) {
    const n = signalBars[signal] != null ? signalBars[signal] : 4;
    const bars = [[3, 17, 3.5], [8.2, 13.5, 7], [13.4, 9.5, 11], [18.6, 5, 15.5]]
      .map(([x, y, hh], i) => `<rect x="${x}" y="${y}" width="3" height="${hh}" rx=".8" fill="currentColor" opacity="${i < n ? 1 : 0.25}"/>`).join('');
    return svg(`<svg class="ic" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">${bars}</svg>`);
  }
  /** Battery and network — only if they came (otherwise the block is not shown). */
  function deviceBits(c) {
    const out = [];
    if (c && c.battery != null) {
      out.push(h`<span class="sos-dev__item" title="${t('device.battery', { n: App.fmt.percent(c.battery) })}">${icon('battery', { size: 24 })}<span>${App.fmt.percent(c.battery)}</span></span>`);
    }
    if (c && c.signal && signalBars[c.signal] != null) {
      out.push(h`<span class="sos-dev__item" role="img" aria-label="${t('device.signal', { s: App.device.signalText(c.signal) })}">${signalIcon(c.signal)}</span>`);
    }
    const conn = typeof navigator !== 'undefined' && navigator.connection;
    if (out.length && conn && conn.type === 'wifi') out.push(h`<span class="sos-dev__item">${icon('wifi', { size: 22 })}</span>`);
    return out;
  }
  /** "لطفاً…" → "نزدیک: X" plus the rest of the address on line two (as in 10); no doubled "نزدیک:" from the server. */
  function placeLines(name) {
    const s = String(name || '').replace(/^\s*(نزدیک|نږدې|Near)\s*:\s*/i, '').trim();
    if (!s) return null;
    const i = s.search(/[،,]/);
    return i > 0 ? [s.slice(0, i).trim(), s.slice(i + 1).trim()] : [s, ''];
  }
  const coordNum = (v) => App.raw('⁦' + App.esc(App.fmt.num(Math.abs(Number(v)), 4) + '°') + '⁩');

  /** The "موقعیت زنده در حال اشتراک / به‌روزرسانی خودکار: فعال" strip (10, 12) — truthful about the location. */
  function liveStrip(c) {
    const live = hasLoc(c) && S.gps !== false;
    const line1 = !hasLoc(c) ? t('sos.noLocation') : live ? t('sos.liveSharing') : t('sos.locationSent');
    return h`<div class="sos-live${live ? '' : ' is-off'}" data-sos-live>
      <span class="sos-live__art">${ART.pinRing}</span>
      <div class="sos-live__lines">
        <p><span>${line1}</span><span class="live-dot" aria-hidden="true"></span></p>
        <p><span>${live ? t('sos.autoUpdateOn') : t('sos.autoUpdateOff')}</span>${icon('refresh', { size: 20 })}</p>
      </div>
    </div>`;
  }

  // ---------------------------------------------------------------- 10: urgent SOS
  function card10(c, sentAt) {
    const place = placeLines(c.place_name);
    const ago = S.fixAt ? App.fmt.ago(S.fixAt) : (hasLoc(c) ? App.fmt.ago(c.updated_at) : '');
    const dev = deviceBits(c);
    const loc = hasLoc(c)
      ? h`<div class="sos-row"><span>${t(c.lat >= 0 ? 'sos.north' : 'sos.south')}</span><span class="sos-num">${coordNum(c.lat)}</span>${icon('locate', { size: 21, cls: 'is-red' })}</div>
          <div class="sos-row"><span>${t(c.lon >= 0 ? 'sos.east' : 'sos.west')}</span><span class="sos-num">${coordNum(c.lon)}</span>${icon('pin-fill', { size: 21, cls: 'is-red' })}</div>
          ${place ? h`<hr><div class="sos-row sos-row--top"><span class="sos-place"><span>${t('sos.near')} <b>${place[0]}</b></span>${place[1] ? h`<span>${place[1]}</span>` : ''}</span><span class="is-red">${ART.building}</span></div>` : ''}
          <hr><div class="sos-row"><span>${t('sos.updated')} <b class="is-green" data-sos-ago>${ago}</b></span>${icon('refresh', { size: 21, cls: 'is-red' })}</div>`
      : h`<div class="sos-row"><span>${t('sos.noLocation')}</span>${icon('pin', { size: 21, cls: 'is-red' })}</div>
          <button class="btn btn--outline btn--sm" type="button" data-pick>${icon('map')}<span>${t('sos.pickLocation')}</span></button>`;
    return h`<article class="sos-card" data-sos-card="${c.id}">
      <header class="sos-card__top">
        <span class="sos-card__stamp">${App.fmt.stamp(sentAt)}</span>
        <span class="sos-card__sent"><span>${t('sos.sentBadge')}</span>${icon('send', { size: 22 })}</span>
      </header>
      <div class="sos-card__hero">
        <div class="sos-card__intro"><h1>${t('sos.urgent')}</h1><p>${t('sos.defaultText')}</p></div>
        <span class="sos-card__siren">${ART.siren}</span>
      </div>
      <div class="sos-card__body">
        <h2 class="sos-label"><span>${t('sos.myLocation')}</span>${icon('pin-fill', { size: 22, cls: 'is-red' })}</h2>
        <div class="sos-locbox" data-sos-loc>${loc}</div>
        <h2 class="sos-label"><span>${t('sos.sentTime')}</span>${icon('clock', { size: 22 })}</h2>
        <p class="sos-when">${App.fmt.stamp(sentAt)}</p>
        <div class="sos-note"><p>${t('sos.viaApp')}</p>${icon('mobile', { size: 24 })}</div>
        <p class="sos-please"><span>${t('sos.pleaseHelp')}</span><span class="sos-please__hands" aria-hidden="true">🙏</span></p>
      </div>
      <footer class="sos-card__status">
        <span class="sos-ok"><span>${t('sos.statusSent')}</span>${icon('check-circle', { size: 24 })}</span>
        ${dev.length ? h`<span class="sos-dev">${dev}</span>` : ''}
      </footer>
    </article>`;
  }

  App.screen('sos-now', {
    route: '#/sos/now',
    title: 'sos.urgent',
    render(params, el) {
      el.classList.add('sos-screen');
      let left = COUNTDOWN, tick = null, sent = false, sending = false, refresh = null, alive = true;
      const locP = locate();            // look for the location during the countdown
      const devP = deviceInfo();
      const countdown = () => h`<div class="sos-count" role="alertdialog" aria-labelledby="sos-count-t">
          <div class="sos-count__ring" aria-hidden="true"><span data-n>${App.fmt.num(left)}</span></div>
          <h1 id="sos-count-t">${t('sos.urgent')}</h1>
          <p class="sos-count__text" data-text aria-live="assertive">${t('sos.now.countdown', { n: left })}</p>
          <p class="muted small">${t('sos.now.cancelHint')}</p>
          <div class="stack stack--sm sos-count__btns">
            <button class="btn btn--danger" type="button" data-send-now>${t('sos.now.sendNow')}</button>
            <button class="btn btn--outline" type="button" data-cancel>${t('common.cancel')}</button>
          </div>
        </div>`;
      el.innerHTML = String(countdown());
      const q = (s) => el.querySelector(s);

      async function send() {
        if (sending || sent) return;
        sending = true;
        clearInterval(tick);
        el.innerHTML = String(h`<div class="sos-count"><div class="sos-count__ring is-busy" aria-hidden="true"><span class="spinner"></span></div>
          <p class="sos-count__text" role="status">${t('sos.now.sending')}</p></div>`);
        const loc = await locP;
        const dev = await devP;
        const item = { type: 'sos', from: 'now', caseId: openId(), after: S.last.case === openId() ? S.last.id : 0,
          body: Object.assign({ kind: 'default' }, point(loc), dev) };
        let r;
        try {
          r = await queue.send(item);
        } catch (e) {
          sending = false;
          if (!alive) return;
          el.innerHTML = String(h`<div class="sos-count">${App.ui.empty({ icon: 'alert', title: t('sos.now.failed'), text: e.message })}
            <div class="stack stack--sm sos-count__btns"><button class="btn btn--danger" type="button" data-retry>${t('common.retry')}</button>
            <button class="btn btn--outline" type="button" data-cancel>${t('common.cancel')}</button></div></div>`);
          q('[data-retry]').addEventListener('click', send);
          q('[data-cancel]').addEventListener('click', () => App.back('#/home'));
          return;
        }
        if (!r) { if (alive) queued(item, loc); return; }   // no network — waits in the queue
        shown(r, loc);
      }

      /** No network: the strip, call 119; once it arrives — the normal card of 10. */
      function queued(item, loc) {
        el.innerHTML = String(h`<div class="sos-count">
            <div class="sos-count__ring is-wait" aria-hidden="true">${icon('clock', { size: 64, stroke: 1.6 })}</div>
            ${offlineBox()}
            <div class="stack stack--sm sos-count__btns">
              <button class="btn btn--outline" type="button" data-retry>${t('common.retry')}</button>
              <button class="btn btn--ghost" type="button" data-cancel-q>${t('sos.cancelQueued')}</button>
            </div>
          </div>`);
        q('[data-retry]').addEventListener('click', () => { queue.flush(); });
        q('[data-cancel-q]').addEventListener('click', () => {
          queue.remove(item.qid);
          App.toast(t('sos.now.cancelled'), 'info');
          App.back('#/home');
        });
        App.onLeave(App.on('sos:sent', (ev) => {
          if (ev.item.qid !== item.qid || !alive) return;
          ev.handled = true;
          shown(ev.res, loc);
        }));
      }

      function shown(r, loc) {
        sent = true;
        tracker.start(r.case);
        if (!alive) return;
        const sentAt = r.message ? r.message.created_at : r.case.updated_at;
        const draw = () => {
          el.innerHTML = String(h`${card10(S.case || r.case, sentAt)}${liveStrip(S.case || r.case)}
            ${App.ui.button({ text: t('sos.goChat'), go: '#/sos', kind: 'outline', icon: 'chat', cls: 'sos-go-chat' })}`);
          const pick = q('[data-pick]');
          if (pick) pick.addEventListener('click', () => { pickAndSend(r.case.id).catch((e) => App.toast(e.message, 'error')); });
        };
        draw();
        const off = App.on('sos', draw);
        App.onLeave(off);
        refresh = setInterval(() => { const a = q('[data-sos-ago]'); if (a && S.fixAt) a.textContent = App.fmt.ago(S.fixAt); }, 10000);
        if (!loc) pickAndSend(r.case.id).catch((e) => App.toast(e.message, 'error'));
      }

      tick = setInterval(() => {
        left -= 1;
        if (left <= 0) { send(); return; }
        q('[data-n]').textContent = App.fmt.num(left);
        q('[data-text]').textContent = t('sos.now.countdown', { n: left });
      }, 1000);
      q('[data-send-now]').addEventListener('click', send);
      q('[data-cancel]').addEventListener('click', () => {
        clearInterval(tick);
        App.toast(t('sos.now.cancelled'), 'info');
        App.back('#/home');
      });
      // leaving the screen before the countdown ends also cancels; a send already started is completed
      return () => { alive = false; clearInterval(tick); clearInterval(refresh); };
    },
  });

  // ---------------------------------------------------------------- 12: «پیام ارسال شد!»
  App.screen('sos-sent', {
    route: '#/sos/sent',
    title: 'sos.messageSent',
    async render(params, el) {
      const r = await App.api('GET', '/api/sos/current');
      if (!r.case) { App.replace('#/sos'); return undefined; }
      tracker.start(r.case);
      el.classList.add('sos-screen');
      const msgs = r.messages || [];
      const auto = msgs.find((m) => m.text_key === 'sos_auto_received') || msgs.find((m) => m.sender === 'auto');
      const autoText = auto ? textOf(auto) : '';
      let left = TO_CHAT;
      const draw = () => {
        const c = S.case || r.case;
        const live = hasLoc(c) && S.gps !== false;
        const rows = [];
        rows.push(h`<li>${icon('refresh', { size: 24 })}<span>${live ? t('sos.updateEvery', { n: EVERY_MS / 1000 }) : t('sos.autoUpdateOff')}</span></li>`);
        if (c.battery != null) rows.push(h`<li>${icon('battery', { size: 24 })}<span>${t('device.battery', { n: App.fmt.percent(c.battery) })}</span></li>`);
        if (c.signal && signalBars[c.signal] != null) rows.push(h`<li>${signalIcon(c.signal)}<span>${t('device.signal', { s: App.device.signalText(c.signal) })}</span></li>`);
        el.innerHTML = String(h`${liveStrip(c)}
          <h1 class="sos-done"><span>${t('sos.messageSent')}</span>${icon('check-circle', { size: 36 })}</h1>
          <section class="sos-share">
            <div class="sos-share__head"><span class="live-dot" aria-hidden="true"></span><p>${hasLoc(c) ? t('sos.sharingWithTeam') : t('sos.noLocation')}</p><span class="sos-share__art">${ART.pinRadio}</span></div>
            <ul class="sos-share__rows">${rows}</ul>
          </section>
          ${autoText ? h`<section class="sos-auto">
            <div class="sos-auto__main"><h2>${ART.bubble}<span>${t('sos.autoReplyTitle')}</span></h2><p class="sos-q">${autoText}</p></div>
            <span class="sos-auto__art">${ART.shield}</span>
          </section>` : ''}
          <button class="btn btn--outline sos-go-chat" type="button" data-chat>${icon('chat')}<span data-left>${t('sos.goChatIn', { n: left })}</span></button>`);
        el.querySelector('[data-chat]').addEventListener('click', () => App.replace('#/sos'));
      };
      draw();
      App.onLeave(App.on('sos', draw));
      const timer = setInterval(() => {
        left -= 1;
        if (left <= 0) { clearInterval(timer); App.replace('#/sos'); return; }
        const b = el.querySelector('[data-left]');
        if (b) b.textContent = t('sos.goChatIn', { n: left });
      }, 1000);
      return () => clearInterval(timer);
    },
  });

  // ---------------------------------------------------------------- 11/13: chat with rescuers
  const quote = (text) => h`<span class="sos-q" dir="auto">${text}</span>`;
  /** Attachment (paperclip): a message whose text is an image path from POST /api/upload is shown as an image. */
  const IMAGE = /^\/uploads\/[\w.-]+\.(png|jpe?g|webp)$/i;
  const body = (text) => (IMAGE.test(String(text || '').trim())
    ? h`<a class="sos-img" href="${text.trim()}" target="_blank" rel="noopener"><img src="${text.trim()}" alt="${t('sos.photo')}" loading="lazy"></a>`
    : quote(text));
  /** The number from the server's auto-reply text ("شماره تماس مستقیم: ۱۱۹") — we do not invent our own. */
  function directNumber(text) {
    const m = App.fmt.latin(String(text || '')).match(/(\d{2,15})\s*$/);
    return m ? m[1] : null;
  }
  function teamFoot(m) {
    return h`<div class="msg__meta sos-sign"><span class="msg__time">${icon('clock', { size: 16 })}${App.fmt.time(m.created_at)}</span><span>${t('sos.teamSign')} —</span></div>`;
  }
  function msgHtml(m, c, first) {
    if (m.sender === 'user') {
      const sos = m.text_key === 'sos_default';
      const name = sos ? h`${t('sos.user')} ${App.ui.badge('SOS', 'sos')}` : t('sos.user');
      const bubble = App.ui.msg({ side: 'out', name, text: body(textOf(m)), status: 'sent' });
      return first ? h`${bubble}${locCard(c)}` : bubble;
    }
    if (m.sender === 'auto' && m.text_key === 'sos_auto_wait') {
      const num = directNumber(m.text);
      return App.ui.msg({
        side: 'in', avatar: 'users', dot: true, name: t('sos.team'),
        text: h`${quote(t('sos.teamReply'))}<span class="sos-meanwhile">${t('sos.meanwhile')}</span>`,
        checks: [t('sos.tip.tracked'), t('sos.tip.safePlace'), t('sos.tip.battery')],
        footer: h`${num ? h`<div class="sos-direct"><span>${t('sos.directNumber')}</span><a class="sos-direct__tel" href="${App.phone.tel(num)}">${App.phone.display(num)}${icon('phone', { size: 22 })}</a></div>` : ''}${teamFoot(m)}`,
      });
    }
    if (m.sender === 'auto') {
      return App.ui.msg({ side: 'in', avatar: 'users', dot: true, name: t('sos.team'), text: quote(textOf(m)), footer: teamFoot(m) });
    }
    return App.ui.msg({ side: 'in', avatar: 'users', dot: true, name: t('sos.rescuer'), text: body(m.text), footer: teamFoot(m) });
  }
  /** Our own message waiting for the network. */
  function pendingHtml(it) {
    const sos = it.type === 'sos' && it.body.kind === 'default';
    const name = sos ? h`${t('sos.user')} ${App.ui.badge('SOS', 'sos')}` : t('sos.user');
    return h`<div class="sos-pending" data-qid="${it.qid}">${App.ui.msg({ side: 'out', name, text: body(sos ? t('sos.defaultText') : it.body.text),
      footer: h`<div class="msg__meta"><span class="sos-queued">${icon('clock', { size: 16 })}${t('sos.queued')}</span></div>` })}</div>`;
  }
  const CHAT_KEY = 'sosChat';  // the last conversation — so that the chat opens offline too
  function locCard(c) {
    if (!hasLoc(c)) {
      return h`<div class="msg msg--in msg--loc" data-sos-loc><span class="msg__avatar msg__avatar--green">${icon('pin-fill', { size: 30 })}</span>
        <div class="msg__body"><div class="msg__head"><span class="msg__name">${t('sos.noLocation')}</span></div>
        <button class="btn btn--outline btn--sm" type="button" data-pick>${icon('map')}<span>${t('sos.pickLocation')}</span></button></div></div>`;
    }
    const live = S.gps !== false;
    return h`<div data-sos-loc>${App.ui.msgLocation({ lat: c.lat, lon: c.lon, sub: live ? t('sos.autoUpdate') : t('sos.autoUpdateOff') })}</div>`;
  }

  App.screen('sos-chat', {
    route: '#/sos',
    title: 'sos.chat.title',
    async render(params, el) {
      el.classList.add('sos-screen', 'sos-chat');
      S.unread = 0;
      App.setBadge('resident', 'message', 0);
      let r;
      try {
        r = await App.api('GET', '/api/sos/current');
      } catch (e) {  // no network — the last saved conversation; new messages go out from the queue
        if (e.status !== 0) throw e;
        r = App.store.get(CHAT_KEY) || { case: S.case, messages: [] };
      }
      if (!r.case && navigator.onLine !== false) {  // no open case — maybe the last one was closed: show it as closed
        const last = App.store.get(KEY);
        if (last) {
          try { r = await App.api('GET', `/api/sos/${encodeURIComponent(last)}/messages`); } catch (e) { App.store.del(KEY); r = { case: null, messages: [] }; }
        }
      }
      let c = r.case;
      let msgs = r.messages || [];
      if (c && c.status === 'open') tracker.start(c); else if (c) S.case = c;
      if (c) note(c.id, msgs);
      const ids = new Set(msgs.map((m) => m.id));
      const keep = () => { App.store.set(CHAT_KEY, { case: c, messages: msgs.slice(-100) }); };
      keep();

      el.innerHTML = String(h`<header class="hdr sos-chat__head">
          <span class="hdr__start"><a class="sos-chat__sos" href="#/sos/now" data-go="#/sos/now" aria-label="${t('sos.sosLabel')}">SOS</a></span>
          <h1 class="hdr__title">${t('sos.chat.title')}</h1>
          <span class="hdr__end"><button class="btn-icon" type="button" data-back="#/home" aria-label="${t('common.back')}">${icon('back', { size: 26 })}</button></span>
        </header>
        <div class="chat sos-chat__list" data-list aria-live="polite"></div>
        <div class="sos-closed" data-closed hidden>
          <p class="sos-closed__title">${icon('check-circle', { size: 22 })}<span>${t('sos.closedTitle')}</span></p>
          <p class="muted">${t('sos.closedText')}</p>
          <button class="btn btn--primary" type="button" data-new>${t('sos.newCase')}</button>
        </div>
        ${App.ui.chatbar({ dock: true })}
        <input type="file" accept="image/png,image/jpeg,image/webp" hidden data-file>`);
      const list = el.querySelector('[data-list]');
      const closedBox = el.querySelector('[data-closed]');
      const bar = el.querySelector('.chatbar');

      const atBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 140;
      const toBottom = () => { window.scrollTo(0, document.documentElement.scrollHeight); };
      // waiting for the network: SOS (any) and messages of this case
      const pending = () => queue.pending().filter((it) => it.type === 'sos' || (c && it.type === 'msg' && it.caseId === c.id));
      function drawAll() {
        const later = pending();
        if (!msgs.length && !later.length) {
          list.innerHTML = String(h`<div class="sos-hint">${icon('chat', { size: 26 })}<p>${t('sos.chat.hint')}</p></div>`);
        } else {
          let firstUser = true;
          list.innerHTML = String(h`${msgs.map((m) => {
            const first = m.sender === 'user' && firstUser;
            if (first) firstUser = false;
            return msgHtml(m, c, first);
          })}${later.map(pendingHtml)}${later.length ? offlineBox() : ''}`);
        }
        bindPick();
        const closed = !!c && c.status !== 'open';
        closedBox.hidden = !closed;
        bar.hidden = closed;
      }
      function bindPick() {
        const b = list.querySelector('[data-pick]');
        if (b && c) b.addEventListener('click', () => { pickAndSend(c.id).catch((e) => App.toast(e.message, 'error')); });
      }
      function add(list2) {
        const stick = atBottom();
        let any = false;
        (list2 || []).forEach((m) => { if (m && !ids.has(m.id)) { ids.add(m.id); msgs.push(m); any = true; } });
        if (any) { msgs.sort((a, b) => a.id - b.id); if (c) note(c.id, msgs); keep(); drawAll(); if (stick) toBottom(); }
      }
      drawAll();
      toBottom();

      // own message: the first one — a new case (→ 12), later ones — into the open case
      async function sendText(text) {
        if (!c || c.status !== 'open') {
          const loc = await locate();
          const dev = await deviceInfo();
          const res = await queue.send({ type: 'sos', from: 'chat', caseId: null, after: 0,
            body: Object.assign({ kind: 'custom', text }, point(loc), dev) });
          if (!res) { drawAll(); toBottom(); return; }   // no network — "در صف ارسال", it goes out by itself
          if (loc) S.fixAt = Date.now();
          c = res.case;
          App.replace('#/sos/sent');                     // replace: from the chat it is a single "back"
          if (!loc) pickAndSend(res.case.id).catch(() => {});
          return;
        }
        try {
          const res = await queue.send({ type: 'msg', from: 'chat', caseId: c.id, after: S.last.case === c.id ? S.last.id : 0, body: { text } });
          if (!res) { drawAll(); toBottom(); return; }
          c = Object.assign({}, c, res.case);
          add(res.messages || [res.message]);
          toBottom();
        } catch (e) {
          if (e.status === 409) { closed(text); return; }
          throw e;
        }
      }
      /** The case was closed while the resident was writing: show "closed", put the text back in the field. */
      function closed(text) {
        c = Object.assign({}, c, { status: 'closed' });
        tracker.stop();
        drawAll();
        const input = bar.querySelector('input');
        if (text && !input.value) input.value = text;
      }
      const file = el.querySelector('[data-file]');
      App.ui.bindChatbar(el, sendText, () => file.click());
      file.addEventListener('change', async () => {
        const f = file.files && file.files[0];
        file.value = '';
        if (!f) return;
        App.toast(t('sos.uploading'), 'info');
        try { const up = await App.upload(f); await sendText(up.url); } catch (e) { App.toast(e.message || t('error.generic'), 'error'); }
      });
      el.querySelector('[data-new]').addEventListener('click', () => {
        App.store.del(KEY);
        c = null; msgs = []; ids.clear();
        keep();
        drawAll();
        bar.querySelector('input').focus();
      });

      // rescuer replies and closing arrive by themselves; after a connection drop — catch up on what was missed
      App.live.on('sos_message', (d) => { if (c && d && d.case_id === c.id) add([d.message]); });
      App.onLeave(App.on('sos', (st) => {
        if (!c || !st.case || st.case.id !== c.id) return;
        const was = c.status;
        c = Object.assign({}, c, st.case);
        if (was !== c.status) drawAll();
        else { const box = list.querySelector('[data-sos-loc]'); if (box) { box.outerHTML = String(locCard(c)); bindPick(); } }
      }));
      // queue: "در صف ارسال" bubbles; arrived without us — the normal path (the first → 12, later ones — into the feed)
      App.onLeave(App.on('sos:queue', () => { drawAll(); }));
      App.onLeave(App.on('sos:sent', (ev) => {
        const { item, res } = ev;
        if (item.type === 'sos' && item.from === 'chat' && (!c || c.status !== 'open')) {
          ev.handled = true;
          c = res.case;
          App.replace('#/sos/sent');
          return;
        }
        if (c && res.case && res.case.id === c.id) {
          if (item.from === 'chat') ev.handled = true;
          c = Object.assign({}, c, res.case);
          add(res.messages || [res.message]);
        }
      }));
      App.onLeave(App.on('sos:failed', (ev) => {
        if (c && ev.item.caseId === c.id && ev.error && ev.error.status === 409) closed(ev.item.body.text);
        else if (ev.item.from === 'chat') App.toast(ev.error.message || t('error.generic'), 'error');
      }));

      // after the stream reconnects (App.live 'reconnect') — reload the conversation: missed messages and "closed"
      async function reload() {
        try {
          if (c) {
            const x = await App.api('GET', `/api/sos/${c.id}/messages`);
            const was = c.status;
            if (x.case) c = Object.assign({}, c, x.case);
            if (c.status !== 'open') tracker.stop();
            add(x.messages);
            if (was !== c.status) { keep(); drawAll(); }
          } else {
            const x = await App.api('GET', '/api/sos/current');
            if (x.case) { c = x.case; tracker.start(c); add(x.messages); }
          }
        } catch (e) { /* no network — catch up next time */ }
      }
      App.live.on('reconnect', reload); // core event: the stream reopened after a drop (removed on leaving)
      return undefined;
    },
  });
})(window.App);
