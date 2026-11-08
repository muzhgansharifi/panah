/* PANAH (پناه) ui-core — icons (our own SVG) and App.ui.* components: each returns safe HTML (App.html),
   for use in an App.html`…${App.ui.button({...})}…` template or in el.innerHTML. Styles: css/components.css. */
(function (App) {
  'use strict';
  const h = App.html;
  const t = (k, v) => App.t(k, v);

  // ---------------------------------------------------------------- icons: 24×24, stroke 2, color = currentColor
  const S = (d) => `<path d="${d}"/>`;
  const F = (d) => `<path d="${d}" fill="currentColor" stroke="none"/>`;
  const ICONS = {
    'back': S('M15 5l-7 7 7 7'),
    'chevron-left': S('M15 5l-7 7 7 7'),
    'chevron-right': S('M9 5l7 7-7 7'),
    'chevron-down': S('M5 9l7 7 7-7'),
    'caret-down': F('M5.5 9h13L12 16.5z'),
    'arrow-left': S('M20 12H5M11 6l-6 6 6 6'),
    'arrow-right': S('M4 12h15M13 6l6 6-6 6'),
    'skip': S('M5 6l6 6-6 6M13 6l6 6-6 6'),
    'close': S('M6 6l12 12M18 6L6 18'),
    'search': '<circle cx="11" cy="11" r="6.5"/>' + S('M16 16l4.5 4.5'),
    'home': S('M4 11l8-7 8 7v8.5c0 .3-.2.5-.5.5H15v-6H9v6H4.5a.5.5 0 0 1-.5-.5z'),
    'play': F('M8 5.2v13.6L18.8 12z'),
    'play-circle': '<circle cx="12" cy="12" r="9.5"/>' + F('M10 8.3v7.4l6-3.7z'),
    'phone': S('M6.6 3.5h2.6l1.5 4.2-2 1.4a12 12 0 0 0 6.2 6.2l1.4-2 4.2 1.5v2.6a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4.6 5.7a2 2 0 0 1 2-2.2z'),
    'chat': S('M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-8L5.5 20v-3.5H6a2 2 0 0 1-2-2z') + S('M8.5 10.3h.01M12 10.3h.01M15.5 10.3h.01'),
    'bell': S('M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z') + S('M10 20.5a2 2 0 0 0 4 0'),
    'mail': '<rect x="3" y="5.5" width="18" height="13" rx="2"/>' + S('M3.5 7l8.5 6.5L20.5 7'),
    'folder': S('M3 7a1.5 1.5 0 0 1 1.5-1.5H9.3l2 2.3h8.2A1.5 1.5 0 0 1 21 9.3v9.2a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z'),
    'shield': S('M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z'),
    'user': '<circle cx="12" cy="8" r="4"/>' + S('M4.5 20.5c.7-3.6 3.7-6 7.5-6s6.8 2.4 7.5 6'),
    'user-plus': '<circle cx="10" cy="8" r="4"/>' + S('M3 20.5c.7-3.6 3.4-6 7-6 1.6 0 3 .4 4.2 1.2M18.5 13v6M15.5 16h6'),
    'users': '<circle cx="9" cy="8.5" r="3.5"/>' + S('M2.5 20c.6-3.2 3.2-5.3 6.5-5.3s5.9 2.1 6.5 5.3') + S('M15.5 5.3a3.5 3.5 0 0 1 0 6.4M17.8 14.9c2 .7 3.4 2.6 3.7 5.1'),
    'mobile': '<rect x="6.5" y="2.5" width="11" height="19" rx="2.3"/>' + S('M10.5 18.3h3'),
    'lock': '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/>' + S('M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2.5'),
    'eye': S('M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z') + '<circle cx="12" cy="12" r="3"/>',
    'eye-off': S('M4 4l16 16M9.9 5.8A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.7 3.5M14.1 14.2A3 3 0 0 1 9.8 9.9M6.4 7.3A16.5 16.5 0 0 0 2.5 12S6 18.5 12 18.5c1.6 0 3-.4 4.3-1'),
    'drop': F('M12 2.8s6.3 6.9 6.3 11.5a6.3 6.3 0 0 1-12.6 0C5.7 9.7 12 2.8 12 2.8z'),
    'heart': S('M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z') + S('M6.5 12.5h3l1.3-2.3 2 4 1.2-1.7h3.5'),
    'pin': S('M12 21.5s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z') + '<circle cx="12" cy="9.5" r="2.5"/>',
    'pin-fill': F('M12 22s7.5-6.6 7.5-12.5a7.5 7.5 0 0 0-15 0C4.5 15.4 12 22 12 22zm0-9.7a2.8 2.8 0 1 1 0-5.6 2.8 2.8 0 0 1 0 5.6z'),
    'locate': '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2"/>' + S('M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3'),
    'map': S('M3 6.5l6-2.5 6 2.5 6-2.5v13.5l-6 2.5-6-2.5-6 2.5zM9 4v13.5M15 6.5V20'),
    'edit': S('M4 20h4L19 9l-4-4L4 16z') + S('M13.5 6.5l4 4'),
    'check': S('M5 12.5l4.5 4.5L19 7.5'),
    'check-circle': '<circle cx="12" cy="12" r="10" fill="currentColor" stroke="none"/>' + '<path d="M7.5 12.3l3 3 6-6.3" stroke="#fff"/>',
    'check-square': '<rect x="3.5" y="3.5" width="17" height="17" rx="3" fill="currentColor" stroke="none"/>' + '<path d="M7.5 12.3l3 3 6-6.3" stroke="#fff"/>',
    'plus': S('M12 5v14M5 12h14'),
    'trash': S('M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5'),
    'refresh': S('M19.5 11A7.5 7.5 0 0 0 6 6.5L4.5 8M4.5 4v4h4M4.5 13A7.5 7.5 0 0 0 18 17.5l1.5-1.5M19.5 20v-4h-4'),
    'clock': '<circle cx="12" cy="12" r="9"/>' + S('M12 7v5l3.5 2'),
    'calendar': '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/>' + S('M3.5 9.5h17M8 3v4M16 3v4'),
    'send': S('M20.5 3.5L3.5 10.3l7 2.6 2.6 7z') + S('M20.5 3.5l-10 9.4'),
    'clip': S('M20 11.5l-8.2 8.2a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6'),
    'siren': S('M6.5 17.5V13a5.5 5.5 0 0 1 11 0v4.5M4.5 17.5h15v3h-15zM12 2.5v2M4.2 5.7l1.4 1.4M19.8 5.7l-1.4 1.4'),
    'battery': '<rect x="2.5" y="7" width="17" height="10" rx="2"/>' + S('M21.5 10.5v3') + '<rect x="5" y="9.5" width="9" height="5" rx=".8" fill="currentColor" stroke="none"/>',
    'signal': F('M3 17h3v3.5H3zM8.2 13.5h3v7h-3zM13.4 9.5h3v11h-3zM18.6 5h3v15.5h-3z'),
    'wifi': S('M2.5 9a14 14 0 0 1 19 0M5.5 12.3a9.5 9.5 0 0 1 13 0M8.7 15.5a5 5 0 0 1 6.6 0') + F('M12 20.2a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2z'),
    'globe': '<circle cx="12" cy="12" r="9"/>' + S('M3 12h18M12 3c2.6 2.7 3.8 5.7 3.8 9s-1.2 6.3-3.8 9c-2.6-2.7-3.8-5.7-3.8-9S9.4 5.7 12 3z'),
    'alert': S('M12 3.5L2.5 20h19z') + S('M12 10v4.5M12 17.3v.2'),
    'flood': S('M3 17c1.5 0 1.5-1.2 3-1.2s1.5 1.2 3 1.2 1.5-1.2 3-1.2 1.5 1.2 3 1.2 1.5-1.2 3-1.2 1.5 1.2 3 1.2M3 21c1.5 0 1.5-1.2 3-1.2s1.5 1.2 3 1.2 1.5-1.2 3-1.2 1.5 1.2 3 1.2 1.5-1.2 3-1.2 1.5 1.2 3 1.2M6 13V8.5L12 4l6 4.5V13'),
    'quake': S('M2.5 12h4l2-5 3 10 3-12 2.5 7h4.5'),
    'info': '<circle cx="12" cy="12" r="9"/>' + S('M12 11v6M12 7.5v.2'),
    'help': '<circle cx="12" cy="12" r="9"/>' + S('M9.5 9.3a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.2-2.5 3.8M12 17v.2'),
    'image': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>' + '<circle cx="9" cy="10" r="1.8"/>' + S('M4 17.5l5-4.5 4 3.5 3-2.5 4 3.5'),
    'video': '<rect x="2.5" y="6" width="13.5" height="12" rx="2"/>' + S('M16 10.5l5.5-3v9l-5.5-3'),
    'logout': S('M14 4.5H6.5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1H14M10.5 12H20M16.5 8.5L20 12l-3.5 3.5'),
    'list': S('M8.5 6.5H20M8.5 12H20M8.5 17.5H20M4 6.5h.01M4 12h.01M4 17.5h.01'),
    'dot': '<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>',
  };
  /** App.icon('phone', {size: 24, cls: 'muted', label: 'متن برای خواننده صفحه'}) */
  App.icon = function (name, o = {}) {
    const body = ICONS[name] || ICONS.info;
    const size = o.size || 24;
    const a11y = o.label ? `role="img" aria-label="${App.esc(o.label)}"` : 'aria-hidden="true"';
    return App.raw(`<svg class="ic ic-${name}${o.cls ? ' ' + App.esc(o.cls) : ''}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="${o.stroke || 2}" stroke-linecap="round" stroke-linejoin="round" ${a11y}>${body}</svg>`);
  };
  App.icons = Object.keys(ICONS);

  const ui = {};
  App.ui = ui;

  /** Logo PANAH (پناه), redrawn from the group's sample (colours V3): a dark rounded square, orange mountains with arcs, an amber signal,
      «پناه» and PANAH. kind: 'sos' | 'mark' | 'admin' — the icon (02, 03, 14), 'mini' — no text, bold lines (header
      01, 04), 'full' — the icon with the motto. The geometry is generated by app/web/img/make_logo.py. */
  let logoN = 0;
  /* logo-geometry:start — generated by app/web/img/make_logo.py, do not edit by hand */
  const LOGO_DEFS = (id) => `<defs><linearGradient id="${id}b" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1F2430"/><stop offset="1" stop-color="#2B3140"/></linearGradient><radialGradient id="${id}h" cx=".3" cy=".18" r=".7"><stop offset="0" stop-color="#fff" stop-opacity=".13"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient><linearGradient id="${id}r" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFC24D"/><stop offset="1" stop-color="#FFC24D"/></linearGradient></defs>`;
  const LOGO_ICON = (id) => `<rect width="1024" height="1024" rx="224" fill="url(#${id}b)"/><rect width="1024" height="1024" rx="224" fill="url(#${id}h)"/><path d="M176 436C224 276 420 170 584 176C640 178 684 196 712 222C676 205 632 196 584 198C432 202 266 290 176 436Z" fill="#F04A0C"/><path d="M116.0 574.0 L380.4 381.9 L419.2 410.3 L550.8 282.7 L641.8 377.7 L683.2 350.6 L816.5 450.6 L835.0 440.2 L930.0 546.0 L930.0 546.0 L849.0 425.8 L827.5 433.4 L696.8 321.4 L658.2 346.3 L545.2 241.3 L400.8 379.7 L363.6 354.1 L116.0 574.0Z" fill="#F04A0C"/><path d="M352.0 478.0 L527.0 337.6 L572.0 436.0 L572.0 436.0 L521.0 306.4 L352.0 478.0Z" fill="#F04A0C"/><path d="M578.0 372.0 L700.0 470.0 L700.0 470.0 L578.0 372.0Z" fill="#F04A0C"/><g fill="none" stroke="url(#${id}r)" stroke-linecap="round" stroke-width="36"><path d="M758 246A112 112 0 0 1 870 358"/><path d="M758 170A188 188 0 0 1 946 358"/></g><circle cx="780" cy="336" r="34" fill="url(#${id}r)"/><text x="512" y="706" text-anchor="middle" direction="rtl" font-family="Vazirmatn, Tahoma, sans-serif" font-size="300" font-weight="900" fill="#fff">پناه</text><path d="M118 812C292 730 470 712 590 750C470 728 300 742 118 812Z" fill="#F04A0C"/><text x="523" y="912" text-anchor="middle" font-family="Vazirmatn, 'Segoe UI', Arial, sans-serif" font-size="96" font-weight="700" letter-spacing="22" fill="#fff">PANAH</text>`;
  const LOGO_MINI = (id) => `<rect width="1024" height="1024" rx="224" fill="url(#${id}b)"/><rect width="1024" height="1024" rx="224" fill="url(#${id}h)"/><path d="M116.0 574.0 L399.0 412.4 L439.0 443.1 L556.7 325.5 L624.3 411.4 L668.3 382.6 L802.4 472.8 L816.3 459.7 L930.0 546.0 L930.0 546.0 L867.7 406.3 L841.6 411.2 L711.7 289.4 L675.7 312.6 L539.3 198.5 L381.0 346.9 L345.0 323.6 L116.0 574.0Z" fill="#F04A0C" transform="translate(0 150)"/><g fill="none" stroke="url(#${id}r)" stroke-linecap="round" stroke-width="88"><path d="M600 300A230 230 0 0 1 830 530"/><path d="M600 110A420 420 0 0 1 1020 530"/></g><circle cx="640" cy="490" r="78" fill="url(#${id}r)"/>`;
  const LOGO_MOTTO = `<text x="512" y="1150" text-anchor="middle" font-family="Vazirmatn, 'Segoe UI', Arial, sans-serif" font-size="47" font-weight="700" fill="#1F2430">A warning today, a protection tomorrow</text>`;
  /* logo-geometry:end */
  ui.logo = function (o = {}) {
    const kind = o.kind || 'sos';
    const id = 'panah' + (++logoN);
    const a11y = o.label ? `role="img" aria-label="${App.esc(o.label)}"` : 'aria-hidden="true"';
    const full = kind === 'full';
    const w = o.size || (full ? 240 : kind === 'mini' ? 30 : 112);
    const vh = full ? 1200 : 1024;
    const body = kind === 'mini' ? LOGO_MINI(id) : LOGO_ICON(id) + (full ? LOGO_MOTTO : '');
    return App.raw(`<svg class="logo logo--${kind}" viewBox="0 0 1024 ${vh}" width="${w}" height="${Math.round(w * vh / 1024)}" ${a11y}>${LOGO_DEFS(id)}${body}</svg>`);
  };

  /** Header (01, 04): {title, back: true | '#/fallback-address' | false, shield: true, sub} */
  ui.header = function (o = {}) {
    const back = o.back === undefined ? true : o.back;
    return h`<header class="hdr${o.cls ? ' ' + o.cls : ''}">
      <span class="hdr__start">${o.shield === false ? '' : ui.logo({ kind: 'mini', size: 30 })}</span>
      <h1 class="hdr__title">${o.title || ''}</h1>
      <span class="hdr__end">${back ? h`<button class="btn-icon" type="button" data-back="${typeof back === 'string' ? back : ''}" aria-label="${t('common.back')}">${App.icon('back', { size: 26 })}</button>` : ''}</span>
    </header>${o.sub ? h`<p class="hdr__sub">${o.sub}</p>` : ''}`;
  };
  /** Large centered title (15–18): {title, sub} */
  ui.pageTitle = (o = {}) => h`<div class="page-title"><h1>${o.title || ''}</h1>${o.sub ? h`<p>${o.sub}</p>` : ''}</div>`;
  /** The app's name at the top of a screen: the V3 icon and «PANAH» (Latin, as the group asked) */
  ui.brand = (o = {}) => h`<span class="brand${o.cls ? ' ' + o.cls : ''}">${ui.logo({ kind: 'mini', size: o.size || 26 })}<b class="brand__name" dir="ltr">${t('app.brand')}</b></span>`;

  /** Button: {text, kind: 'primary'|'purple'|'outline'|'outline-accent'|'ghost'|'sos'|'danger', icon, sub, go, href,
      type, id, name, value, disabled, small, block(true), attrs: {...}} — with go/href it is a link. */
  ui.button = function (o = {}) {
    const kind = o.kind || 'primary';
    const cls = ['btn', 'btn--' + kind];
    if (o.small) cls.push('btn--sm');
    if (o.edge) cls.push('btn--edge');
    if (o.block === false) cls.push('btn--inline');
    if (o.sub) cls.push('btn--tall');
    if (o.cls) cls.push(o.cls);
    let icon = o.icon ? App.icon(o.icon) : '';
    if (kind === 'sos') icon = h`<span class="sos-dot" aria-hidden="true">SOS</span>`;
    if ((kind === 'primary' || kind === 'purple') && o.icon === 'check') icon = h`<span class="btn__check">${App.icon('check', { size: 20, stroke: 3 })}</span>`;
    const text = o.sub ? h`<span class="btn__text"><span>${o.text}</span><small>${o.sub}</small></span>` : h`<span>${o.text || ''}</span>`;
    const extra = Object.entries(o.attrs || {}).map(([k, v]) => h` ${k}="${v}"`);
    if (o.go || o.href) {
      return h`<a class="${cls.join(' ')}" href="${o.href || o.go}"${o.go ? h` data-go="${o.go}"` : ''}${o.id ? h` id="${o.id}"` : ''}${extra}>${text}${icon}</a>`;
    }
    return h`<button class="${cls.join(' ')}" type="${o.type || 'button'}"${o.id ? h` id="${o.id}"` : ''}${o.name ? h` name="${o.name}"` : ''}${o.value != null ? h` value="${o.value}"` : ''}${o.disabled ? h` disabled` : ''}${extra}>${text}${icon}</button>`;
  };
  /** Round icon button: {icon, label, go, cls, attrs} */
  ui.iconButton = (o = {}) => h`<button class="btn-icon${o.cls ? ' ' + o.cls : ''}" type="button" aria-label="${o.label || ''}"${o.go ? h` data-go="${o.go}"` : ''}${Object.entries(o.attrs || {}).map(([k, v]) => h` ${k}="${v}"`)}>${App.icon(o.icon || 'info')}</button>`;
  /** "+ add" tile (16, 17): {text, go, id} */
  ui.addTile = (o = {}) => h`<button class="add-tile" type="button"${o.go ? h` data-go="${o.go}"` : ''}${o.id ? h` id="${o.id}"` : ''}><span class="add-tile__plus">${App.icon('plus', { size: 26, stroke: 2.4 })}</span><span>${o.text || t('common.add')}</span></button>`;
  /** "یا" (or) divider (02, 14) */
  ui.or = (text) => h`<div class="or"><span>${text || t('common.or')}</span></div>`;

  /** Card: ui.card(content, {kind: 'light'|'pink'|'blue'|'green'|'muted', cls, pad}) */
  ui.card = (content, o = {}) => h`<div class="card${o.kind ? ' card--' + o.kind : ''}${o.cls ? ' ' + o.cls : ''}">${content}</div>`;
  /** Gray empty placeholder card (05, 06) */
  ui.skeleton = (n = 1) => h`${Array.from({ length: n }, () => h`<div class="card card--muted" aria-hidden="true"></div>`)}`;

  /** Field (01, 15): {label, name, type, value, placeholder, required, optional, icon (by the label),
      inputIcon (in the field), end (HTML on the right in the field), hint, error, dir, inputmode, autocomplete,
      rows (textarea), options [{value,label}] (select), loud (whole label red, as in 15), id, attrs} */
  let uid = 0;
  ui.field = function (o = {}) {
    const id = o.id || 'f' + (++uid);
    const attrs = Object.entries(o.attrs || {}).map(([k, v]) => h` ${k}="${v}"`);
    const common = h`id="${id}" name="${o.name || id}"${o.required ? h` required` : ''}${o.placeholder ? h` placeholder="${o.placeholder}"` : ''}${o.dir ? h` dir="${o.dir}"` : ''}${o.inputmode ? h` inputmode="${o.inputmode}"` : ''}${o.autocomplete ? h` autocomplete="${o.autocomplete}"` : ''}${attrs}`;
    let control;
    if (o.options) {
      control = h`<select class="input input--select" ${common}>${o.placeholder ? h`<option value="" disabled${o.value == null ? h` selected` : ''}>${o.placeholder}</option>` : ''}${o.options.map((x) => h`<option value="${x.value}"${String(x.value) === String(o.value) ? h` selected` : ''}>${x.label}</option>`)}</select>`;
    } else if (o.rows) {
      control = h`<textarea class="input input--area" rows="${o.rows}" ${common}>${o.value || ''}</textarea>`;
    } else {
      control = h`<input class="input${o.lg ? ' input--lg' : ''}" type="${o.type || 'text'}" value="${o.value == null ? '' : o.value}" ${common}>`;
    }
    const wrapped = (o.inputIcon || o.end)
      ? h`<div class="input-wrap">${o.inputIcon ? h`<span class="input-wrap__icon">${App.icon(o.inputIcon)}</span>` : ''}${control}${o.end ? h`<span class="input-wrap__end">${o.end}</span>` : ''}</div>`
      : control;
    const label = o.label ? h`<label class="field__label" for="${id}">${o.required ? h`<span class="field__req" aria-hidden="true">*</span> ` : ''}<span>${o.label}</span>${o.optional ? h` <span class="field__opt">${t('common.optional')}</span>` : ''}${o.required && o.showRequired ? h` <span class="field__opt">${t('common.required')}</span>` : ''}</label>` : '';
    return h`<div class="field${o.loud ? ' field--loud' : ''}${o.cls ? ' ' + o.cls : ''}">
      ${(label || o.icon) ? h`<div class="field__head">${label}${o.icon ? h`<span class="field__icon">${App.icon(o.icon)}</span>` : ''}</div>` : ''}
      ${wrapped}
      ${o.hint ? h`<p class="field__hint">${o.hint}</p>` : ''}
      <p class="field__error" ${o.error ? '' : 'hidden'}>${o.error || ''}</p>
    </div>`;
  };
  /** Show/clear the error under a field: ui.fieldError(inputEl, 'text' | null) */
  ui.fieldError = function (input, text) {
    const f = input.closest('.field');
    if (!f) return;
    const p = f.querySelector('.field__error');
    f.classList.toggle('is-invalid', !!text);
    if (p) { p.textContent = text || ''; p.hidden = !text; }
  };
  /** Section card with a title and an icon (01): {title, icon, optional, required, content} */
  ui.section = (o = {}) => h`<section class="card form-card">
      <div class="field__head"><h2 class="field__label">${o.required ? h`<span class="field__req" aria-hidden="true">*</span> ` : ''}<span>${o.title || ''}</span>${o.optional ? h` <span class="field__opt">${t('common.optional')}</span>` : ''}</h2>${o.icon ? h`<span class="field__icon">${App.icon(o.icon)}</span>` : ''}</div>
      ${o.content || ''}</section>`;

  /** Choice tiles (blood group, 01): {name, items: [{value, label, icon}], value, cols: 4, type: 'radio'|'checkbox',
      wide: [value…] — full width, check: true — with a checkbox square (health, 01)} */
  ui.tiles = function (o = {}) {
    const type = o.type || (o.check ? 'checkbox' : 'radio');
    const vals = [].concat(o.value == null ? [] : o.value).map(String);
    return h`<div class="tiles" style="--cols:${o.cols || 4}" role="${type === 'radio' ? 'radiogroup' : 'group'}">${(o.items || []).map((it) => {
      const wide = (o.wide || []).map(String).includes(String(it.value));
      return h`<label class="tile${o.check ? ' tile--check' : ''}${wide ? ' tile--wide' : ''}${it.ok ? ' tile--ok' : ''}">
        <input type="${type}" name="${o.name}" value="${it.value}"${vals.includes(String(it.value)) ? h` checked` : ''}>
        ${o.check ? h`<span class="tile__box" aria-hidden="true">${App.icon('check', { size: 14, stroke: 3 })}</span>` : ''}
        <span class="tile__text" dir="auto">${it.label}</span>${it.icon ? App.icon(it.icon, { size: 18 }) : ''}
      </label>`;
    })}</div>`;
  };

  /** Badge: ui.badge('10', 'red'|'sos'|'gray'|'green'|'orange'|'purple') */
  ui.badge = (text, kind) => h`<span class="badge badge--${kind || 'red'}">${text}</span>`;
  /** Alert banner (05/06): {kind: 'ok'|'alert'|'danger', title, text, go} */
  ui.banner = function (o = {}) {
    const inner = h`<strong class="banner__title">${o.title || ''}</strong>${o.text ? h`<span class="banner__text">${o.text}</span>` : ''}`;
    const cls = 'banner banner--' + (o.kind || 'ok');
    return o.go ? h`<a class="${cls}" href="${o.go}" data-go="${o.go}">${inner}</a>` : h`<div class="${cls}" role="status">${inner}</div>`;
  };
  /** Section title with a badge and an arrow (05 "هشدارهای قبلی 10 ▼"): {title, badge, open, toggle: true} */
  ui.sectionTitle = (o = {}) => h`<div class="section-title">
      <h2>${o.title || ''}</h2>
      <span class="section-title__end">${o.badge != null && o.badge !== '' ? ui.badge(typeof o.badge === 'number' ? App.fmt.num(o.badge) : o.badge, 'red') : ''}${o.toggle ? h`<button class="btn-icon section-title__toggle${o.open ? ' is-open' : ''}" type="button" aria-expanded="${o.open ? 'true' : 'false'}" aria-label="${o.title || ''}">${App.icon('caret-down')}</button>` : ''}</span>
    </div>`;
  /** List row (09, numbers): {icon, title, sub, end (HTML), go, href, cls} */
  ui.row = function (o = {}) {
    const inner = h`<span class="row__icon">${App.icon(o.icon || 'info')}</span><span class="row__body"><span class="row__title">${o.title || ''}</span>${o.sub ? h`<span class="row__sub">${o.sub}</span>` : ''}</span>${o.end ? h`<span class="row__end">${o.end}</span>` : ''}`;
    if (o.go || o.href) return h`<a class="row${o.cls ? ' ' + o.cls : ''}" href="${o.href || o.go}"${o.go ? h` data-go="${o.go}"` : ''}>${inner}</a>`;
    return h`<div class="row${o.cls ? ' ' + o.cls : ''}">${inner}</div>`;
  };
  /** Phone number to call: a tel: link showing "۰۷۷۷ ۱۲۳ ۴۵۶" */
  ui.phone = (p) => h`<a class="phone-link" href="${App.phone.tel(p)}">${App.phone.display(p)}</a>`;

  /** Empty state: {icon, title, text, action: {text, go}} */
  ui.empty = (o = {}) => h`<div class="empty">
      <span class="empty__icon">${App.icon(o.icon || 'info', { size: 30 })}</span>
      ${o.title ? h`<p class="empty__title">${o.title}</p>` : ''}${o.text ? h`<p class="empty__text">${o.text}</p>` : ''}
      ${o.action ? ui.button({ text: o.action.text, go: o.action.go, kind: 'outline', small: true, block: false }) : ''}
    </div>`;
  ui.loading = () => h`<div class="loading" role="status"><span class="spinner"></span><span>${t('common.loading')}</span></div>`;
  /** Screen load error with a "try again" button */
  ui.errorState = (err) => ui.empty({ icon: 'alert', title: (err && err.message) || t('error.generic'), action: { text: t('common.retry'), go: (typeof location !== 'undefined' && location.hash) || App.config.home } });

  /** Chat bubble (11, 13): {side: 'out' (own, at line start) | 'in', name, text, time, status: 'sent', avatar: 'user'|'users',
      title (instead of name), checks: [lines with a check mark], footer (HTML), dot: true (green dot by the title)} */
  ui.msg = function (o = {}) {
    const side = o.side || 'out';
    const head = (o.name || o.title) ? h`<div class="msg__head">${o.dot ? h`<span class="live-dot" aria-hidden="true"></span>` : ''}<span class="msg__name">${o.name || o.title}</span></div>` : '';
    const avatar = o.avatar !== false ? h`<span class="msg__avatar">${App.icon(o.avatar || (side === 'out' ? 'user' : 'users'), { size: 22 })}</span>` : '';
    return h`<div class="msg msg--${side}">
      ${avatar}
      <div class="msg__body">
        ${head}
        ${o.text ? h`<p class="msg__text">${o.text}</p>` : ''}
        ${o.checks && o.checks.length ? h`<ul class="checks">${o.checks.map((c) => h`<li>${App.icon('check-circle', { size: 20 })}<span>${c}</span></li>`)}</ul>` : ''}
        ${o.footer || ''}
        ${(o.time || o.status) ? h`<div class="msg__meta">${o.status === 'sent' ? h`<span class="msg__status">${App.icon('check-circle', { size: 18 })}${t('common.sent')}</span>` : ''}${o.time ? h`<span class="msg__time">${App.icon('clock', { size: 15 })}${o.time}</span>` : ''}</div>` : ''}
      </div>
    </div>`;
  };
  /** "Location attached" card (13): {lat, lon, title, sub} */
  ui.msgLocation = (o = {}) => h`<div class="msg msg--in msg--loc">
      <span class="msg__avatar msg__avatar--green">${App.icon('pin-fill', { size: 30 })}</span>
      <div class="msg__body">
        <div class="msg__head"><span class="live-dot" aria-hidden="true"></span><span class="msg__name">${o.title || t('sos.locationAttached')}</span></div>
        <p class="msg__text">${App.fmt.coord(o.lat, o.lon)}</p>
        <div class="msg__meta msg__meta--line">${App.icon('refresh', { size: 18, cls: 'is-green' })}<span>${o.sub || t('sos.autoUpdate')}</span></div>
      </div>
    </div>`;
  /** Chat input bar (13): {placeholder, attach: true, name} → then ui.bindChatbar(el, text => …) */
  ui.chatbar = (o = {}) => h`<form class="chatbar${o.dock ? ' is-docked' : ''}" autocomplete="off">
      <button class="chatbar__send" type="submit" aria-label="${t('chat.send')}">${App.icon('send', { size: 22 })}</button>
      <input class="chatbar__input" name="${o.name || 'text'}" placeholder="${o.placeholder || t('chat.placeholder')}" aria-label="${o.placeholder || t('chat.placeholder')}" enterkeyhint="send">
      ${o.attach === false ? '' : h`<button class="chatbar__attach" type="button" aria-label="${t('chat.attach')}">${App.icon('clip', { size: 22 })}</button>`}
    </form>`;
  /** Send from the chat bar: onSend(text) may return a Promise; empty is not sent. onAttach() — the paperclip. */
  ui.bindChatbar = function (root, onSend, onAttach) {
    const form = root.matches && root.matches('.chatbar') ? root : root.querySelector('.chatbar');
    if (!form) return null;
    const input = form.querySelector('.chatbar__input');
    const sync = () => form.classList.toggle('has-text', input.value.trim() !== '');
    input.addEventListener('input', sync);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = ''; sync();
      try { await onSend(text); } catch (err) { input.value = text; sync(); App.toast(err.message || t('error.generic'), 'error'); }
      input.focus();
    });
    const clip = form.querySelector('.chatbar__attach');
    if (clip) { if (onAttach) clip.addEventListener('click', onAttach); else clip.hidden = true; }
    return { input, focus: () => input.focus() };
  };

  /** Language switch (02): English | دری | پشتو */
  ui.langSwitch = () => h`<div class="langs" role="group" aria-label="${t('common.language')}">
      ${App.icon('globe', { size: 22 })}
      ${['en', 'fa', 'ps'].map((l, i) => h`${i ? h`<span class="langs__sep" aria-hidden="true">|</span>` : ''}<button type="button" class="langs__item${App.lang === l ? ' is-on' : ''}" data-lang="${l}" lang="${l}" aria-pressed="${App.lang === l ? 'true' : 'false'}">${t('lang.' + l)}</button>`)}
    </div>`;

  /** Bottom menu: 'resident' (05) or 'admin' (15–18); active — tab id. The shell adds it from the screen's nav. */
  ui.tabbar = function (kind, active) {
    const tabs = App.navs[kind] || [];
    return h`<nav class="tabbar tabbar--${kind}" aria-label="menu">${tabs.map((tb) => {
      const on = tb.id === active;
      const n = App.badges[kind + '.' + tb.id];
      return h`<a class="tabbar__item${on ? ' is-on' : ''}" href="${tb.route}" data-tab="${tb.id}"${on ? h` aria-current="page"` : ''}>
        <span class="tabbar__icon">${App.icon(tb.icon, { size: kind === 'admin' ? 24 : 22 })}<span class="tabbar__badge"${n ? '' : h` hidden`}>${n ? App.fmt.num(n) : ''}</span></span>
        <span class="tabbar__label">${t(tb.label)}</span></a>`;
    })}</nav>`;
  };

  /** Bottom sheet over the screen: ui.sheet(content, {title, onClose}) → {el, close} */
  ui.sheet = function (content, o = {}) {
    const wrap = document.createElement('div');
    wrap.className = 'sheet';
    wrap.innerHTML = String(h`<div class="sheet__backdrop" data-sheet-close></div>
      <div class="sheet__panel" role="dialog" aria-modal="true" aria-label="${o.title || ''}">
        <div class="sheet__head"><h2>${o.title || ''}</h2><button class="btn-icon" type="button" data-sheet-close aria-label="${t('common.close')}">${App.icon('close')}</button></div>
        <div class="sheet__body">${content}</div>
      </div>`);
    document.body.appendChild(wrap);
    document.body.classList.add('has-sheet');
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      wrap.remove();
      if (!document.querySelector('.sheet')) document.body.classList.remove('has-sheet');
      if (o.onClose) o.onClose();
    };
    wrap.querySelectorAll('[data-sheet-close]').forEach((b) => b.addEventListener('click', close));
    App.onLeave(close);
    return { el: wrap, close };
  };
})(window.App);
