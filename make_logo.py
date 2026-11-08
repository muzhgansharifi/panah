"""Build the PANAH (پناه) logo from one geometry — redrawn from the group's sample (materials/group-files/logo-sample.jpeg):
a dark rounded square, brand-orange mountains with arcs, an amber signal at the top right, «پناه» in white, PANAH below.
Colours: variant V3 chosen by the group (stand/logo-variants/), in the app palette (app/web/css/base.css); the signal is
amber instead of the sample's orange, so that it reads apart from the mountains at 30–48 px.

Writes:
  app/web/img/icon.svg              the icon (no motto) — screens and favicon; Vazirmatn subset embedded (works as <img>)
  app/web/img/logo.svg              the icon with the motto «A warning today, a protection tomorrow» — thesis title page
  app/web/img/logo-panah-1024.png   the icon, 1024 px (transparent corners)
  thesis/figures/logo-panah.png     the variant with the motto, 1024 px wide
  app/web/js/ui.js                  the inline icon (between the logo-geometry markers; uses the page font)

Run from the project root:  python app/web/img/make_logo.py
Needs fontTools (subset of the font) and Google Chrome (PNG); the Vazirmatn TTF files are downloaded once into stand/logo.
"""
import base64
import io
import math
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / 'app' / 'web'
WORK = ROOT / 'stand' / 'logo'
MOTTO = 'A warning today, a protection tomorrow'
SQUARE_TOP, SQUARE_BOTTOM = '#1F2430', '#2B3140'  # the square, top right → bottom left: ink of the app (--c-ink)
INK = '#F04A0C'                                    # mountains and arcs: brand orange (--c-brand)
SIGNAL_TOP, SIGNAL_BOTTOM = '#FFC24D', '#FFC24D'   # the signal: amber, lighter and warmer than the mountains
TEXT = '#fff'                                      # «پناه» and PANAH
MOTTO_INK = '#1F2430'                              # the motto under the square (the square's colour)


def taper(points, width, power=0.7):
    """A brush stroke along a polyline: closed outline, full width in the middle, a point at both ends."""
    seg = [math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(seg)
    pos, acc = [0.0], 0.0
    for s in seg:
        acc += s
        pos.append(acc / total)
    left, right = [], []
    for i, (x, y) in enumerate(points):
        a = points[max(i - 1, 0)]
        b = points[min(i + 1, len(points) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1
        nx, ny = -dy / n, dx / n
        w = width / 2 * (math.sin(math.pi * pos[i]) ** power)
        if 0 < i < len(points) - 1:  # keep the stroke width at a corner
            d1 = (points[i][0] - a[0], points[i][1] - a[1])
            d2 = (b[0] - points[i][0], b[1] - points[i][1])
            cos = (d1[0] * d2[0] + d1[1] * d2[1]) / ((math.hypot(*d1) * math.hypot(*d2)) or 1)
            w /= max(math.sqrt((1 + cos) / 2), 0.55)
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    pts = left + right[::-1]
    return 'M' + ' L'.join(f'{x:.1f} {y:.1f}' for x, y in pts) + 'Z'


RIDGE = [(116, 574), (372, 368), (410, 395), (548, 262), (650, 362), (690, 336), (822, 442), (842, 433), (930, 546)]
INNER_LEFT = [(352, 478), (524, 322), (572, 436)]
INNER_RIGHT = [(578, 372), (700, 470)]
ARC_TOP = 'M176 436C224 276 420 170 584 176C640 178 684 196 712 222C676 205 632 196 584 198C432 202 266 290 176 436Z'
ARC_BOTTOM = 'M118 812C292 730 470 712 590 750C470 728 300 742 118 812Z'


def mark_body(fa_font, lat_font, gid, text=True, mini=False):
    """The icon inside a 1024×1024 box. mini: no text, thicker lines (header icon, 30 px)."""
    out = [f'<rect width="1024" height="1024" rx="224" fill="url(#{gid}b)"/>',
           f'<rect width="1024" height="1024" rx="224" fill="url(#{gid}h)"/>']
    if mini:
        out.append(f'<path d="{taper(RIDGE, 92, 0.5)}" fill="{INK}" transform="translate(0 150)"/>')
        out.append(f'<g fill="none" stroke="url(#{gid}r)" stroke-linecap="round" stroke-width="88">'
                   '<path d="M600 300A230 230 0 0 1 830 530"/><path d="M600 110A420 420 0 0 1 1020 530"/></g>'
                   f'<circle cx="640" cy="490" r="78" fill="url(#{gid}r)"/>')
        return ''.join(out)
    out.append(f'<path d="{ARC_TOP}" fill="{INK}"/>')
    out.append(f'<path d="{taper(RIDGE, 30)}" fill="{INK}"/>')
    out.append(f'<path d="{taper(INNER_LEFT, 20)}" fill="{INK}"/><path d="{taper(INNER_RIGHT, 18)}" fill="{INK}"/>')
    out.append(f'<g fill="none" stroke="url(#{gid}r)" stroke-linecap="round" stroke-width="36">'
               '<path d="M758 246A112 112 0 0 1 870 358"/><path d="M758 170A188 188 0 0 1 946 358"/></g>'
               f'<circle cx="780" cy="336" r="34" fill="url(#{gid}r)"/>')
    if text:
        out.append(f'<text x="512" y="706" text-anchor="middle" direction="rtl" font-family="{fa_font}" font-size="300" '
                   f'font-weight="900" fill="{TEXT}">پناه</text>')
        out.append(f'<path d="{ARC_BOTTOM}" fill="{INK}"/>')
        out.append(f'<text x="523" y="912" text-anchor="middle" font-family="{lat_font}" font-size="96" font-weight="700" '
                   f'letter-spacing="22" fill="{TEXT}">PANAH</text>')
    return ''.join(out)


def defs(gid):
    return (f'<defs><linearGradient id="{gid}b" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{SQUARE_TOP}"/>'
            f'<stop offset="1" stop-color="{SQUARE_BOTTOM}"/></linearGradient>'
            f'<radialGradient id="{gid}h" cx=".3" cy=".18" r=".7"><stop offset="0" stop-color="#fff" stop-opacity=".13"/>'
            f'<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
            f'<linearGradient id="{gid}r" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{SIGNAL_TOP}"/>'
            f'<stop offset="1" stop-color="{SIGNAL_BOTTOM}"/></linearGradient></defs>')


def font_face(ttf, family, text):
    """A WOFF subset (only the glyphs of text, with the Arabic joining forms) as an @font-face rule."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    opts = subset.Options()
    opts.flavor = 'woff'
    opts.layout_features = ['*']
    opts.name_IDs = ['*']
    font = TTFont(str(ttf))
    sub = subset.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(font)
    buf = io.BytesIO()
    font.flavor = 'woff'
    font.save(buf)
    data = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"@font-face{{font-family:'{family}';src:url(data:font/woff;base64,{data}) format('woff');}}"


def standalone(ttf_dir, motto):
    fa = font_face(ttf_dir / 'Vazirmatn-Black.ttf', 'PanahFa', 'پناه')
    lat = font_face(ttf_dir / 'Vazirmatn-Bold.ttf', 'PanahLat', 'PANAH' + (' ' + MOTTO if motto else ''))
    h = 1200 if motto else 1024
    body = mark_body("PanahFa, Vazirmatn, Tahoma, 'Segoe UI', sans-serif",
                     "PanahLat, Vazirmatn, 'Segoe UI', Arial, sans-serif", 'g')
    extra = (f'<text x="512" y="1150" text-anchor="middle" font-family="PanahLat, Vazirmatn, \'Segoe UI\', Arial, sans-serif" '
             f'font-size="47" font-weight="700" fill="{MOTTO_INK}">{MOTTO}</text>') if motto else ''
    title = 'پناه — PANAH' + (f': {MOTTO}' if motto else '')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 {h}" width="1024" height="{h}">'
            f'<title>{title}</title><style>{fa}{lat}</style>{defs("g")}{body}{extra}</svg>\n')


def inline_js():
    """The inline variant for ui.js: geometry with ${id} gradient ids and the page font (Vazirmatn)."""
    fa = "Vazirmatn, Tahoma, sans-serif"
    lat = "Vazirmatn, 'Segoe UI', Arial, sans-serif"
    full = mark_body(fa, lat, '${id}')
    mini = mark_body(fa, lat, '${id}', mini=True)
    d = defs('${id}')
    motto = (f'<text x="512" y="1150" text-anchor="middle" font-family="{lat}" font-size="47" font-weight="700" '
             f'fill="{MOTTO_INK}">{MOTTO}</text>')
    esc = lambda s: s.replace('`', '\\`')
    return ('  /* logo-geometry:start — generated by app/web/img/make_logo.py, do not edit by hand */\n'
            f'  const LOGO_DEFS = (id) => `{esc(d)}`;\n'
            f'  const LOGO_ICON = (id) => `{esc(full)}`;\n'
            f'  const LOGO_MINI = (id) => `{esc(mini)}`;\n'
            f'  const LOGO_MOTTO = `{esc(motto)}`;\n'
            '  /* logo-geometry:end */\n')


def render_png(svg_path, png_path, width, height):
    sys.path.insert(0, str(ROOT))
    from app.tests.test_ui_core import CDP, WS, find_chrome
    import json
    chrome = find_chrome()
    profile = WORK / 'chrome-render'
    proc = subprocess.Popen([chrome, '--headless=new', f'--user-data-dir={profile}', '--remote-debugging-port=0',
                             '--no-first-run', '--hide-scrollbars', 'about:blank'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = profile / 'DevToolsActivePort'
        end = time.time() + 30
        while not (port_file.is_file() and port_file.read_text().strip()) and time.time() < end:
            time.sleep(0.1)
        port = int(port_file.read_text().split()[0])
        pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=10))
        ws = WS(next(p['webSocketDebuggerUrl'] for p in pages if p.get('type') == 'page'))
        c = CDP(ws)
        c.call('Page.enable')
        c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
        c.call('Emulation.setDefaultBackgroundColorOverride', {'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0}})
        c.call('Page.navigate', {'url': Path(svg_path).resolve().as_uri()})
        c.wait_for('document.readyState === "complete"')
        c.eval('document.fonts.ready.then(() => true)', wait=True)
        time.sleep(0.3)
        shot = c.call('Page.captureScreenshot', {'format': 'png', 'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})
        Path(png_path).write_bytes(base64.b64decode(shot['data']))
        try:
            c.call('Browser.close', timeout=5)
        except Exception:
            pass
        ws.close()
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def ttf_dir():
    d = WORK / 'package' / 'fonts' / 'ttf'
    if not (d / 'Vazirmatn-Black.ttf').is_file():
        WORK.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen('https://registry.npmjs.org/vazirmatn/-/vazirmatn-33.0.3.tgz', timeout=60).read()
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(WORK, members=[m for m in tar.getmembers() if m.name.startswith('package/fonts/ttf/')])
    return d


def main():
    fonts = ttf_dir()
    (WEB / 'img' / 'icon.svg').write_text(standalone(fonts, False), encoding='utf-8')
    (WEB / 'img' / 'logo.svg').write_text(standalone(fonts, True), encoding='utf-8')
    ui = WEB / 'js' / 'ui.js'
    text = ui.read_text(encoding='utf-8')
    new, n = re.subn(r'  /\* logo-geometry:start.*?/\* logo-geometry:end \*/\n', lambda m: inline_js(), text, flags=re.S)
    if n != 1:
        raise SystemExit('ui.js: logo-geometry markers not found')
    ui.write_text(new, encoding='utf-8')
    render_png(WEB / 'img' / 'icon.svg', WEB / 'img' / 'logo-panah-1024.png', 1024, 1024)
    render_png(WEB / 'img' / 'logo.svg', ROOT / 'thesis' / 'figures' / 'logo-panah.png', 1024, 1200)
    print('ok: img/icon.svg, img/logo.svg, img/logo-panah-1024.png, thesis/figures/logo-panah.png, ui.js')


if __name__ == '__main__':
    main()
