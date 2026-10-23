"""Guard for the phase 4.5 instrument-panel language (design §2, §5): hex colours only, no shadows,
gradients, filters or translucency, nothing loaded from outside, no motion but the switch knob, no
inline styles, every /static/ link resolves, and the bundled fonts are served as font/woff2."""
import re
from pathlib import Path

import pytest

from tests.test_server import app, app_paths, client  # noqa: F401  (the aiohttp app fixtures)

APP = Path(__file__).resolve().parent.parent
WEB = APP / "web"
DIST = APP / "desktop" / "dist"

# Files that still speak the old look. Each is restyled by a later phase 4.5 wave, which drops it
# from here; until then the scan runs and is expected to fail (non-strict, so fixing early is fine).
LEGACY: dict[str, str] = {}


def _styled_files() -> list[Path]:
    files = sorted((WEB / "css").rglob("*.css")) if (WEB / "css").is_dir() else []
    files += [WEB / "widget.css"]
    files += sorted(DIST.glob("*.html")) if DIST.is_dir() else []
    return [f for f in files if f.is_file()]


def _param(f: Path):
    rel = f.relative_to(APP).as_posix()
    marks = [pytest.mark.xfail(reason=LEGACY[rel], strict=False)] if rel in LEGACY else []
    return pytest.param(f, id=rel, marks=marks)


STYLED = [_param(f) for f in _styled_files()]


def _css_of(f: Path) -> str:
    """The CSS a file carries: the whole file for .css; <style> blocks plus style="" bodies for HTML."""
    text = f.read_text(encoding="utf-8")
    if f.suffix == ".css":
        return text
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S | re.I)
    inline = re.findall(r"""\sstyle\s*=\s*["']([^"']*)["']""", text, flags=re.I)
    return "\n".join(blocks + [f"[inline]{{{s}}}" for s in inline])


def _rules(css: str) -> list[tuple[str, str]]:
    """Innermost rule blocks as (selector, declarations), comments removed; @media wrappers are
    skipped, so a rule inside one is judged by its own selector."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        selector = m.group(1).split(";")[-1].strip()
        out.append((selector, m.group(2)))
    return out


NON_HEX = re.compile(r"\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix|color)\s*\(", re.I)
HEX = re.compile(r"#([0-9a-fA-F]+)\b")
NAMED = re.compile(r"(?<![-\w])(white|black|red|green|blue|gray|grey|yellow|orange|silver|purple|pink|"
                   r"navy|teal|lime|maroon|olive|aqua|fuchsia)(?![-\w])", re.I)
BANNED = re.compile(r"box-shadow|text-shadow|drop-shadow|(linear|radial|conic)-gradient|(^|[\s;{])filter\s*:|"
                    r"backdrop-filter|blur\s*\(|mix-blend-mode|@keyframes|(^|[\s;{])animation(-[a-z]+)?\s*:", re.I)
EXTERNAL = re.compile(r"https?://|url\(\s*['\"]?//|@import", re.I)
KNOB = re.compile(r"\.sw(\.s2)?\s+i\s*$")


@pytest.mark.parametrize("f", STYLED)
def test_colours_are_hex_only(f):
    bad = []
    for selector, body in _rules(_css_of(f)):
        if NON_HEX.search(body):
            bad.append(f"{selector}: colour function in {body.strip()[:80]}")
        for h in HEX.findall(body):
            if len(h) not in (3, 6):
                bad.append(f"{selector}: #{h} (hex with alpha, or malformed)")
        if NAMED.search(body):
            bad.append(f"{selector}: named colour {NAMED.search(body).group(0)}")
        for m in re.finditer(r"(?<![-\w])opacity\s*:\s*([^;]+)", body):
            if m.group(1).strip() not in ("1", "1.0"):
                bad.append(f"{selector}: opacity {m.group(1).strip()} (translucency)")
    assert not bad, bad


@pytest.mark.parametrize("f", STYLED)
def test_no_shadows_gradients_filters_or_external_urls(f):
    css = re.sub(r"/\*.*?\*/", "", _css_of(f), flags=re.S)
    assert not BANNED.search(css), BANNED.search(css).group(0)
    assert not EXTERNAL.search(css), EXTERNAL.search(css).group(0)


@pytest.mark.parametrize("f", STYLED)
def test_transition_only_on_the_switch_knob(f):
    bad = []
    for selector, body in _rules(_css_of(f)):
        if re.search(r"transition", body, re.I):
            parts = [p.strip() for p in selector.split(",")]
            if not parts or not all(KNOB.search(p) for p in parts):
                bad.append(selector)
    assert not bad, bad


def test_the_knob_is_the_one_transition():
    """The rule above must have something to allow: components.css animates the knob, and only it."""
    css = (WEB / "css" / "components.css").read_text(encoding="utf-8")
    moving = [s for s, b in _rules(css) if "transition" in b]
    assert moving and all(KNOB.search(p.strip()) for s in moving for p in s.split(","))


def test_guard_catches_violations(tmp_path):
    """The scanners themselves: each forbidden thing is found."""
    css = (".a{color:rgba(0,0,0,.5)} .b{box-shadow:0 0 1px #000} .c{background:#0008}"
           ".d{transition:left 1s} .sw i{transition:left 120ms} .e{background:url(https://x/y.png)}"
           ".f{filter: blur(2px)} .g{color:white} .h{opacity:.5}")
    rules = dict(_rules(css))
    assert NON_HEX.search(rules[".a"]) and HEX.findall(rules[".c"]) == ["0008"]
    assert BANNED.search(css) and EXTERNAL.search(css) and NAMED.search(rules[".g"])
    assert not KNOB.search(".d") and KNOB.search(".sw i") and KNOB.search(".sw.s2 i")
    html = tmp_path / "x.html"
    html.write_text('<style>.x{color:#fff}</style><p style="color:red">', encoding="utf-8")
    assert "color:red" in _css_of(html)


def test_no_style_attributes_in_html():
    bad = [f.name for f in sorted(WEB.glob("*.html"))
           if re.search(r"""\sstyle\s*=""", f.read_text(encoding="utf-8"), re.I)]
    assert not bad, bad


@pytest.mark.parametrize("page", ["index.html", "widget.html"])
def test_every_static_link_resolves(page):
    text = (WEB / page).read_text(encoding="utf-8")
    refs = re.findall(r"""(?:href|src)\s*=\s*["'](/static/[^"'?#]+)""", text)
    assert refs, f"{page} links nothing under /static/"
    missing = [r for r in refs if not (WEB / r[len("/static/"):]).is_file()]
    assert not missing, missing


def test_css_font_urls_resolve():
    for f in sorted((WEB / "css").rglob("*.css")):
        for url in re.findall(r"""url\(\s*["']?([^"')]+)""", f.read_text(encoding="utf-8")):
            assert (f.parent / url).resolve().is_file(), f"{f.name}: {url}"


def test_el_never_gets_a_style():
    """Rule 6's sibling: no inline styles from JS either (the CSP has no 'unsafe-inline' for styles)."""
    bad = []
    for f in sorted((WEB / "js").rglob("*.js")):
        text = f.read_text(encoding="utf-8")
        for pat in (r"""\bstyle\s*:""", r"""setAttribute\(\s*['"]style['"]"""):
            for m in re.finditer(pat, text):
                bad.append(f"{f.relative_to(WEB)}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not bad, bad


def test_fonts_are_bundled_with_their_licences():
    fonts = WEB / "fonts"
    for name in ("roboto-latin-wght-normal", "roboto-latin-ext-wght-normal",
                 "roboto-mono-latin-wght-normal", "roboto-mono-latin-ext-wght-normal"):
        data = (fonts / f"{name}.woff2").read_bytes()
        assert data[:4] == b"wOF2", name
    assert "SIL Open Font License" in (fonts / "LICENSE-roboto.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in (fonts / "LICENSE-roboto-mono.txt").read_text(encoding="utf-8")


async def test_fonts_served_as_woff2(client):  # noqa: F811
    r = await client.get("/static/fonts/roboto-latin-wght-normal.woff2")
    assert r.status == 200
    assert r.content_type == "font/woff2"
    assert (await r.read())[:4] == b"wOF2"
    r = await client.get("/static/css/base.css")
    assert r.status == 200 and r.content_type == "text/css"
