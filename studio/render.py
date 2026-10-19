"""HTML -> PDF / PNG with headless Chrome (preinstalled on GitHub's ubuntu-latest runners).
Falls back to Playwright's Chromium if no Chrome is found. Installs emoji/Indic fonts once if missing."""
import glob, os, pathlib, re, shutil, subprocess, sys, tempfile

WORK = pathlib.Path(os.environ.get("STUDIO_WORK", "/tmp/studio"))
_BIN = []


def sh(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {(r.stderr or r.stdout)[-600:]}")
    return r.stdout


def pip(*pkgs):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=True, timeout=600)


def ensure_module(mod, pkg=None):
    try:
        __import__(mod)
    except ImportError:
        pip(pkg or mod)


def apt(*pkgs):
    if shutil.which("apt-get") and os.environ.get("CI"):
        subprocess.run("sudo apt-get update -qq >/dev/null 2>&1; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
                       + " ".join(pkgs) + " >/dev/null 2>&1", shell=True, timeout=900)


def ensure_fonts():
    """Emoji + Devanagari/Indic fonts so Hindi text and emoji never show as boxes (CI only, once per run)."""
    if os.environ.get("CI") and not os.path.exists("/tmp/.studio_fonts"):
        have = subprocess.run(["fc-list"], capture_output=True, text=True).stdout
        need = [p for p, k in (("fonts-noto-color-emoji", "Noto Color Emoji"), ("fonts-noto-core", "NotoSansDevanagari"),
                               ("fonts-dejavu-core", "DejaVuSans-Bold")) if k not in have]
        if need:
            apt(*need)
        pathlib.Path("/tmp/.studio_fonts").write_text("ok")


def chrome():
    if _BIN:
        return _BIN[0]
    for c in [os.environ.get("CHROME_BIN"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
        if c and shutil.which(c):
            _BIN.append(shutil.which(c)); return _BIN[0]
    found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    if not found:
        apt("chromium")  # Debian-style package; on Ubuntu this is a snap shim, so Playwright is the real fallback
        if shutil.which("chromium"):
            _BIN.append(shutil.which("chromium")); return _BIN[0]
        pip("playwright")
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], timeout=900)
        found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    if not found:
        raise RuntimeError("no Chrome/Chromium available")
    _BIN.append(found[-1])
    return _BIN[0]


FLAGS = ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars",
         "--no-first-run", "--no-default-browser-check", "--mute-audio", "--font-render-hinting=none",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000"]

PDF_SAFETY = """<style id="studio-safety">
@page { size: A4; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
img, svg, video, canvas { max-width: 100%; height: auto; }
table { max-width: 100%; border-collapse: collapse; }
td, th, p, li, h1, h2, h3, h4, dd, dt, figcaption { overflow-wrap: break-word; word-wrap: break-word; }
pre, code { white-space: pre-wrap; overflow-wrap: anywhere; }
tr, img, svg, figure, blockquote, .callout, .card, .box, .note, .tip, .warning { break-inside: avoid; page-break-inside: avoid; }
h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
</style>"""


def _run_chrome(args, timeout=150):
    """Run Chrome; if it hangs (e.g. a web font never loads), retry once offline so system fonts are used."""
    extra = []
    px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if px:  # only in sandboxes behind a proxy; GitHub runners have direct internet
        extra = [f"--proxy-server={px}", "--ignore-certificate-errors"]
    try:
        return sh([chrome(), *FLAGS, *extra, *args], timeout=timeout)
    except subprocess.TimeoutExpired:
        print("   chrome timed out; retrying offline")
        return sh([chrome(), *FLAGS, "--host-resolver-rules=MAP * ~NOTFOUND", *args], timeout=timeout)


def _write(html, name):
    WORK.mkdir(parents=True, exist_ok=True)
    p = WORK / name
    p.write_text(html, encoding="utf-8")
    return p


def inject(html, head_extra="", body_extra=""):
    if head_extra:
        html = re.sub(r"(?i)</head>", head_extra + "</head>", html, count=1) if re.search(r"(?i)</head>", html) else head_extra + html
    if body_extra:
        html = re.sub(r"(?i)</body>", body_extra + "</body>", html, count=1) if re.search(r"(?i)</body>", html) else html + body_extra
    return html


def pdf(html, out, safety=True):
    ensure_fonts()
    if safety:
        html = inject(html, PDF_SAFETY)
    src = _write(html, pathlib.Path(out).stem + ".html")
    out = pathlib.Path(out)
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as prof:
        _run_chrome([f"--user-data-dir={prof}", "--no-pdf-header-footer", "--generate-pdf-document-outline",
                     f"--print-to-pdf={out}", src.as_uri()], timeout=240)
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("PDF render produced no file")
    return out


def fit_script(w, h):
    """If the AI's layout is taller/wider than the canvas, reflow it wider and scale it down so nothing is cut off.
    Measures the real bottom/right edge of every element (catches content spilling out of fixed-height boxes)."""
    return f"""<style id="studio-canvas">html{{margin:0!important;width:{w}px;height:{h}px;overflow:hidden!important}}
body{{margin:0!important;transform-origin:0 0}}</style>
<script>(function(){{var W={w},H={h};
function ext(b){{var r0=b.getBoundingClientRect(),mb=0,mr=0,els=b.querySelectorAll('*');
for(var i=0;i<els.length;i++){{var r=els[i].getBoundingClientRect();if(!r.width&&!r.height)continue;
var cs=getComputedStyle(els[i]);if(cs.position==='fixed'||cs.visibility==='hidden')continue;
if(r.bottom>mb)mb=r.bottom;if(r.right>mr)mr=r.right;}}return [mb-r0.top,mr-r0.left];}}
function fit(){{var b=document.body;if(!b)return;b.style.transform='';var z=1,e;
for(var i=0;i<8;i++){{b.style.width=(W/z)+'px';b.style.height=(H/z)+'px';b.style.minHeight='0';b.style.maxHeight='none';
e=ext(b);var pad=12;var zn=Math.min(1,H/(e[0]+pad),(W/z)/Math.max(e[1],1)*z);if(Math.abs(zn-z)<0.004)break;z=(i<3)?zn:Math.min(zn,Math.sqrt(z*zn));}}
b.style.width=(W/z)+'px';b.style.height=(H/z)+'px';e=ext(b);z=Math.min(z,H/(e[0]+12));b.style.width=(W/z)+'px';b.style.height=(H/z)+'px';
b.style.transform=z<0.999?'scale('+z+')':'';}}
fit();window.addEventListener('load',fit);if(document.fonts)document.fonts.ready.then(fit);setTimeout(fit,1200);}})();</script>"""


def png(html, out, w=1080, h=1350, scale=2):
    ensure_fonts()
    html = inject(html, "", fit_script(w, h))
    src = _write(html, pathlib.Path(out).stem + ".html")
    out = pathlib.Path(out)
    out.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as prof:
        _run_chrome([f"--user-data-dir={prof}", f"--window-size={w},{h}", f"--force-device-scale-factor={scale}",
                     f"--screenshot={out}", src.as_uri()], timeout=150)
    if not out.exists():
        raise RuntimeError("screenshot produced no file")
    return out


# --------------- PDF utilities (pypdf) ---------------
def page_texts(path):
    ensure_module("pypdf")
    from pypdf import PdfReader
    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


def split(path, limit=48 * 1024 * 1024):
    """Telegram bots can send files up to 50 MB. Split a bigger PDF into parts by pages."""
    path = pathlib.Path(path)
    if path.stat().st_size <= limit:
        return [path]
    ensure_module("pypdf")
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(str(path))
    n = len(r.pages)
    parts_n = max(2, int(path.stat().st_size / (limit * 0.85)) + 1)
    while True:
        per = -(-n // parts_n)
        outs = []
        for k in range(parts_n):
            wtr = PdfWriter()
            for i in range(k * per, min(n, (k + 1) * per)):
                wtr.add_page(r.pages[i])
            if not wtr.pages:
                continue
            o = path.with_name(f"{path.stem}_part{k+1}.pdf")
            with open(o, "wb") as f:
                wtr.write(f)
            outs.append(o)
        if all(o.stat().st_size <= limit for o in outs) or parts_n >= n:
            return outs
        parts_n += 1
