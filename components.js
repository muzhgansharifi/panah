/* ui-core demo page: all App.ui components on one page — for checking against the Figma frames. Section captions are for
   developers (in English, with Figma screen numbers); everything a resident sees goes through App.t. */
(async function () {
  'use strict';
  await App.ready();
  const { html: h, ui, t } = App;
  const root = document.getElementById('demo');
  const sec = (title) => h`<h2 class="demo-h" dir="auto">${title}</h2>`;
  const now = Date.now();
  let mapH = null;

  function render() {
    if (mapH) { mapH.destroy(); mapH = null; }
    const blood = ['O+', 'AB+', 'B+', 'A+', 'O-', 'AB-', 'B-', 'A-'].map((v) => ({ value: v, label: v }));
    blood.push({ value: 'unknown', label: t('auth.signup.bloodUnknown'), icon: 'user' });
    const health = ['epilepsy', 'bloodPressure', 'diabetes', 'disability', 'heart', 'allergy'].map((v) => ({ value: v, label: t('health.' + v) }));
    health.push({ value: 'none', label: t('health.none') });
    const fmtRows = [
      ['fmt.date', App.fmt.date(now)], ['fmt.dateNum', App.fmt.dateNum(now)], ['fmt.time', App.fmt.time(now)],
      ['fmt.dateTime', App.fmt.dateTime(now)], ['fmt.stamp (10)', App.fmt.stamp('2026-05-01T06:03:00Z')],
      ['fmt.ago', App.fmt.ago(now - 5 * 60000)], ['fmt.distance', `${App.fmt.distance(12)} · ${App.fmt.distance(1.5)} · ${App.fmt.distance(0.35)}`],
      ['fmt.percent', App.fmt.percent(67)], ['fmt.coord (13)', App.fmt.coord(34.555, 69.207)],
      ['phone.display', App.phone.display('0777123456')], ['phone.normalize', App.phone.normalize('۰۷۷۷ ۱۲۳ ۴۵۶')],
    ];

    root.innerHTML = String(h`
      ${sec('Language (02) — switches dir: Dari and Pashto right to left, English left to right')}
      ${ui.langSwitch()}

      ${sec('Header (01, 04) and large title (15–18)')}
      ${ui.header({ title: t('auth.signup.title') })}
      ${ui.header({ title: t('auth.verify.title'), shield: true })}
      ${ui.pageTitle({ title: t('admin.event.title'), sub: t('admin.event.subtitle') })}

      ${sec('Logos (02, 03, 14)')}
      <div class="cluster" style="justify-content: space-around">
        <div class="demo-hero">${ui.logo({ kind: 'sos', size: 96 })}<b>${t('app.name')}</b><span>${t('app.title')}</span><span class="muted small" dir="ltr" lang="en">${t('app.tagline')}</span></div>
        <div class="demo-hero">${ui.logo({ kind: 'admin', size: 96 })}${ui.logo({ kind: 'mini', size: 30 })}</div>
      </div>
      <div class="demo-hero">${ui.logo({ kind: 'full', size: 260, label: 'پناه — PANAH' })}</div>

      ${sec('Alert banner (05, 06) and section title with a badge (05)')}
      <div class="stack">
        ${ui.banner({ kind: 'ok', title: t('home.noAlert'), text: t('home.niceDay') })}
        ${ui.banner({ kind: 'alert', title: t('home.alertFlood'), text: t('home.distance', { d: App.fmt.distance(12) }) })}
        ${ui.banner({ kind: 'danger', title: t('home.alertQuake'), text: t('home.distance', { d: App.fmt.distance(1.5) }) })}
      </div>
      ${ui.sectionTitle({ title: t('home.pastAlerts'), badge: 10, toggle: true })}
      <div class="stack">${ui.skeleton(2)}</div>

      ${sec('Buttons: orange (01, 02), purple (04), outline (02, 04, 14), SOS strip (02), two-line (03)')}
      <div class="stack stack--sm">
        ${ui.button({ text: t('auth.signup.create'), icon: 'check' })}
        ${ui.button({ text: t('auth.login') })}
        ${ui.button({ text: t('auth.verify.confirm'), kind: 'purple', icon: 'check' })}
        ${ui.button({ text: t('auth.verify.resend'), kind: 'outline', icon: 'refresh' })}
        ${ui.button({ text: t('auth.newAccount'), kind: 'outline', icon: 'user-plus', edge: true })}
        ${ui.button({ text: t('auth.signup.skip'), kind: 'outline', icon: 'skip' })}
        ${ui.button({ text: t('auth.sosNoLogin'), kind: 'sos' })}
        ${ui.button({ text: t('auth.quick.start'), sub: t('auth.quick.oneTap'), icon: 'check' })}
        ${ui.button({ text: t('auth.quick.skip'), sub: t('auth.quick.skipHint'), kind: 'outline', icon: 'skip' })}
        ${ui.button({ text: t('auth.verify.editPhone'), kind: 'outline', icon: 'edit', disabled: true })}
        <div class="cluster">${ui.button({ text: t('auth.login'), kind: 'ghost', block: false, icon: 'chevron-left' })}${ui.iconButton({ icon: 'search', label: t('common.search') })}${ui.iconButton({ icon: 'user', label: 'profile' })}</div>
      </div>

      ${sec('Admin (.theme-admin, #FF6B00): buttons, "+ add" (16, 17), "یا" (14)')}
      <div class="theme-admin stack stack--sm">
        ${ui.button({ text: t('admin.login.submit') })}
        ${ui.or()}
        ${ui.button({ text: t('admin.login.otp'), kind: 'outline-accent', icon: 'lock' })}
        ${ui.addTile({ text: t('admin.numbers.add') })}
        <div class="cluster">${ui.button({ text: t('admin.messages.reply'), small: true, block: false })}${ui.button({ text: t('admin.messages.viewReply'), small: true, block: false })}</div>
      </div>

      ${sec('Fields with a label and an icon (01, 02, 14, 15), "*" — required')}
      <div class="stack">
        ${ui.section({ title: t('auth.signup.name'), optional: true, icon: 'user', content: ui.field({ name: 'name', value: 'احمد', placeholder: t('auth.signup.namePlaceholder') }) })}
        ${ui.section({ title: t('auth.phone'), required: true, icon: 'mobile', content: ui.field({ name: 'phone', type: 'tel', dir: 'ltr', inputmode: 'tel', value: App.fmt.digits('0777 123 456'), placeholder: t('auth.phonePlaceholder') }) })}
        ${ui.field({ label: t('auth.phone'), icon: 'user', name: 'phone2', type: 'tel', dir: 'ltr', inputIcon: 'mobile', placeholder: t('auth.phonePlaceholder') })}
        ${ui.field({ name: 'pass', type: 'password', inputIcon: 'lock', placeholder: t('admin.login.password'), end: ui.iconButton({ icon: 'eye-off', label: t('admin.login.password') }) })}
        <div class="theme-admin stack">
          ${ui.field({ label: t('admin.event.type'), required: true, loud: true, name: 'type', placeholder: t('admin.event.typePlaceholder'), options: [{ value: 'flood', label: t('alert.type.flood') }, { value: 'earthquake', label: t('alert.type.earthquake') }] })}
          ${ui.field({ label: t('admin.event.location'), required: true, loud: true, name: 'where', placeholder: t('admin.event.locationPlaceholder'), lg: true })}
        </div>
        ${ui.field({ label: t('chat.placeholder'), name: 'text', rows: 3 })}
        ${ui.field({ label: t('auth.phone'), required: true, name: 'bad', dir: 'ltr', value: '۰۷۷۷ ۱۲۳ ۴۵۴۷', error: t('error.phone') })}
      </div>

      ${sec('Choice tiles — blood group (01) and checkbox tiles — health (01)')}
      ${ui.section({ title: t('auth.signup.blood'), optional: true, icon: 'drop', content: h`${ui.tiles({ name: 'blood', items: blood, value: 'A+', cols: 4, wide: ['unknown'] })}<p class="field__hint">${t('auth.signup.bloodHint')}</p>` })}
      <div style="block-size: 12px"></div>
      ${ui.section({ title: t('auth.signup.health'), optional: true, icon: 'heart', content: ui.tiles({ name: 'health', items: health, check: true, cols: 3, wide: ['none'], value: ['none'] }) })}

      ${sec('Badges (05, 18)')}
      <div class="cluster">${ui.badge(App.fmt.num(10), 'red')}${ui.badge(t('admin.messages.defaultSos'), 'sos')}${ui.badge(t('admin.messages.custom'), 'gray')}${ui.badge(t('sos.sentBadge'), 'green')}${ui.badge(t('alert.type.flood'), 'orange')}${ui.badge(t('alert.type.earthquake'), 'purple')}${App.html`<span class="badge badge--sos badge--lg">${t('admin.messages.defaultSos')}</span>`}</div>

      ${sec('Cards: plain, light (16), pink (18), blue (13), green (03)')}
      <div class="stack stack--sm">
        ${ui.card(h`<b class="ltr" style="font-size: 28px; color: #111">${App.fmt.digits('119')}</b><p class="muted">تلفون اضطراری</p>`)}
        ${ui.card(h`<p>${t('home.callNumbers')}</p>`, { kind: 'light' })}
        ${ui.card(h`<div class="spread"><b style="color: #111">احمد یوسفی</b>${ui.badge(t('admin.messages.defaultSos'), 'sos')}</div><p>در ساحه کارته چهار، سیلاب آمده است. کمک فوری ضرورت است.</p><p class="muted small">کارته چهار، کابل</p>`, { kind: 'pink' })}
        ${ui.card(h`<p>${t('sos.autoReply')}</p>`, { kind: 'blue' })}
        ${ui.card(h`<div class="cluster">${App.icon('lock', { cls: 'is-green' })}<span>${t('auth.quick.safe')}</span></div>`, { kind: 'green' })}
      </div>

      ${sec('List rows (09, numbers) and a phone number to call')}
      <div>
        ${ui.row({ icon: 'phone', title: App.fmt.digits('119'), sub: 'تلفون اضطراری', end: ui.button({ text: t('common.call'), href: App.phone.tel('119'), small: true, block: false, icon: 'phone' }) })}
        ${ui.row({ icon: 'flood', title: t('home.alertFlood'), sub: `${App.fmt.date(now - 3 * 86400000)} · ${App.fmt.distance(12)}`, end: App.icon('chevron-left') })}
        ${ui.row({ icon: 'quake', title: t('home.alertQuake'), sub: App.fmt.ago(now - 2 * 3600000) })}
        <p>${t('auth.verify.codeSentTo')} ${ui.phone('0777123456')} ${t('auth.verify.codeSent')}</p>
      </div>

      ${sec('Empty state, loading, toast')}
      ${ui.empty({ icon: 'search', title: t('search.empty'), text: t('common.empty'), action: { text: t('common.goHome'), go: '#/home' } })}
      ${ui.loading()}
      <div class="stack stack--sm" style="align-items: center">
        <div class="toast toast--ok">${t('loc.picked')}</div>
        <div class="toast toast--error">${t('error.network')}</div>
        <div class="toast">${t('live.offline')}</div>
        <div class="demo-bar">
          <button class="btn btn--outline btn--sm btn--inline" type="button" data-toast="ok">toast ok</button>
          <button class="btn btn--outline btn--sm btn--inline" type="button" data-toast="error">toast error</button>
          <button class="btn btn--outline btn--sm btn--inline" type="button" data-pick>App.location.pick()</button>
        </div>
      </div>

      ${sec('Chat: bubbles (11, 13), location (13), input bar (13)')}
      <div class="chat">
        ${ui.msg({ side: 'out', name: t('sos.user'), text: '"' + t('sos.helpMe') + '"', status: 'sent' })}
        ${ui.msgLocation({ lat: 34.555, lon: 69.207 })}
        ${ui.msg({ side: 'in', name: t('sos.team'), dot: true, text: '"' + t('sos.teamReply') + '"',
          checks: [t('sos.tip.tracked'), t('sos.tip.safePlace'), t('sos.tip.battery')],
          footer: h`<div class="msg__meta msg__meta--line"><span>${t('sos.directNumber')}</span>${ui.phone('119')}${App.icon('phone', { size: 20, cls: 'accent' })}</div>`,
          time: App.fmt.time('2026-05-01T06:04:00Z') })}
        ${ui.chatbar()}
      </div>

      ${sec('Map (OpenStreetMap): marker and a 2 km circle')}
      <div id="demo-map"></div>

      ${sec('Bottom menu of the resident (05) and the admin (15–18), the current tab highlighted')}
      ${ui.tabbar('resident', 'home')}
      ${ui.tabbar('admin', 'numbers')}

      ${sec('App.fmt and App.phone')}
      <table class="demo-fmt">${fmtRows.map(([k, v]) => h`<tr><td>${k}</td><td><bdi>${v}</bdi></td></tr>`)}</table>

      ${sec('Icons (our own SVG): App.icon(name)')}
      <div class="demo-icons">${App.icons.map((n) => h`<div>${App.icon(n)}<span>${n}</span></div>`)}</div>
    `);

    mapH = App.map(root.querySelector('#demo-map'), {
      center: [34.5281, 69.1723], zoom: 13,
      markers: [{ lat: 34.5281, lon: 69.1723, color: 'red', label: t('home.alertFlood') }, { lat: 34.519, lon: 69.152, color: 'green', label: t('admin.event.safePlace') }],
      circle: { lat: 34.5281, lon: 69.1723, radius: 2000, color: 'orange' },
    });
    App.ui.bindChatbar(root, async (text) => App.toast(text, 'ok'), () => App.toast(t('chat.attach')));
    root.querySelectorAll('[data-toast]').forEach((b) => b.addEventListener('click', () => App.toast(t(b.dataset.toast === 'ok' ? 'loc.picked' : 'error.network'), b.dataset.toast)));
    root.querySelector('[data-pick]').addEventListener('click', async () => {
      const loc = await App.location.pick();
      if (loc) App.toast(t('loc.picked') + ': ' + App.fmt.coord(loc.lat, loc.lon), 'ok');
    });
    root.querySelectorAll('.section-title__toggle').forEach((b) => b.addEventListener('click', () => {
      b.classList.toggle('is-open'); b.setAttribute('aria-expanded', String(b.classList.contains('is-open')));
    }));
    document.body.dataset.ready = '1';
  }

  App.on('lang', render);
  render();
})();
