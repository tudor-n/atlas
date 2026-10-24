"""Guard for the phase 4.5 instrument-panel language (design §2, §5) and rule 6: hex colours only (no
named colours), no shadows, gradients, filters, blending or translucency, nothing loaded from outside
(CSS, HTML or JS), no motion but the switch knob, no inline styles from HTML or JS, no HTML sinks in
JS, every /static/ link resolves, and the bundled fonts are served as font/woff2. Each scanner has a
self-test (test_guard_catches_*) feeding it synthetic violations."""
import re
from pathlib import Path

import pytest

from tests.test_server import app, app_paths, client  # noqa: F401  (the aiohttp app fixtures)

APP = Path(__file__).resolve().parent.parent
WEB = APP / "web"
DIST = APP / "desktop" / "dist"
TABLET = WEB / "tablet"

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
# Every CSS named colour (CSS Color 4: 148 names; `transparent`, `currentColor` and the wide keywords
# such as `inherit` are not colours and stay allowed), plus the system colours, which also bypass
# the tokens. Matched case-insensitively as whole words in property values only (_values).
CSS_NAMED_COLOURS = frozenset("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown
burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet
deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise
mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru
pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown
seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan
teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen
""".split())
CSS_SYSTEM_COLOURS = frozenset("""
accentcolor accentcolortext activetext buttonborder buttonface buttontext canvas canvastext field
fieldtext graytext highlight highlighttext linktext mark marktext selecteditem selecteditemtext
visitedtext
""".split())
assert len(CSS_NAMED_COLOURS) == 148
NAMED = re.compile(r"(?<![-\w])(" + "|".join(sorted(CSS_NAMED_COLOURS | CSS_SYSTEM_COLOURS, key=len, reverse=True))
                   + r")(?![-\w])", re.I)
BANNED = re.compile(r"box-shadow|text-shadow|drop-shadow\s*\(|(linear|radial|conic)-gradient|"
                    r"(^|[\s;{])(-webkit-)?filter\s*:|-webkit-filter|-webkit-backdrop-filter|backdrop-filter|"
                    r"blur\s*\(|mix-blend-mode|background-blend-mode|@(-[a-z]+-)?keyframes|"
                    r"(^|[\s;{])(-[a-z]+-)?animation(-[a-z]+)?\s*:", re.I)
EXTERNAL = re.compile(r"https?://|url\(\s*['\"]?//|@import", re.I)
# The one thing allowed to move: exactly the knob selector components.css gives a transition to
# (`.sw i`, and its reduced-motion override). Not `.x .sw i`, not `.sw.s2 i`, not other files.
KNOB_FILE = WEB / "css" / "components.css"
KNOB_SELECTORS = frozenset({".sw i"})


def _is_knob(selector: str) -> bool:
    return " ".join(selector.split()) in KNOB_SELECTORS


def _values(body: str) -> list[str]:
    """The value half of each declaration, with quoted strings and url() removed (font names and
    file names are not colours)."""
    out = []
    for decl in body.split(";"):
        if ":" in decl:
            v = decl.split(":", 1)[1]
            v = re.sub(r"\"[^\"]*\"|'[^']*'|url\([^)]*\)", " ", v)
            out.append(v)
    return out


@pytest.mark.parametrize("f", STYLED)
def test_colours_are_hex_only(f):
    bad = []
    for selector, body in _rules(_css_of(f)):
        if NON_HEX.search(body):
            bad.append(f"{selector}: colour function in {body.strip()[:80]}")
        for h in HEX.findall(body):
            if len(h) not in (3, 6):
                bad.append(f"{selector}: #{h} (hex with alpha, or malformed)")
        for v in _values(body):
            if NAMED.search(v):
                bad.append(f"{selector}: named colour {NAMED.search(v).group(0)}")
        for m in re.finditer(r"(?<![-\w])opacity\s*:\s*([^;]+)", body):
            if m.group(1).strip() not in ("1", "1.0"):
                bad.append(f"{selector}: opacity {m.group(1).strip()} (translucency)")
    assert not bad, bad


@pytest.mark.parametrize("f", STYLED)
def test_no_shadows_gradients_filters_or_external_urls(f):
    css = re.sub(r"/\*.*?\*/", "", _css_of(f), flags=re.S)
    assert not BANNED.search(css), BANNED.search(css).group(0)
    assert not EXTERNAL.search(css), EXTERNAL.search(css).group(0)


def _bad_transitions(css: str, knob_allowed: bool) -> list[str]:
    bad = []
    for selector, body in _rules(css):
        if re.search(r"transition", body, re.I):
            parts = [p.strip() for p in selector.split(",")]
            if not knob_allowed or not parts or not all(_is_knob(p) for p in parts):
                bad.append(selector)
    return bad


@pytest.mark.parametrize("f", STYLED)
def test_transition_only_on_the_switch_knob(f):
    bad = _bad_transitions(_css_of(f), knob_allowed=f.resolve() == KNOB_FILE.resolve())
    assert not bad, bad


def test_the_knob_is_the_one_transition():
    """The rule above must have something to allow: components.css animates the knob, and only it."""
    css = KNOB_FILE.read_text(encoding="utf-8")
    moving = {" ".join(p.split()) for s, b in _rules(css) if "transition" in b for p in s.split(",")}
    assert moving == KNOB_SELECTORS, moving


def test_guard_catches_violations(tmp_path):
    """The scanners themselves: each forbidden thing is found."""
    css = (".a{color:rgba(0,0,0,.5)} .b{box-shadow:0 0 1px #000} .c{background:#0008}"
           ".d{transition:left 1s} .sw i{transition:left 120ms} .e{background:url(https://x/y.png)}"
           ".f{filter: blur(2px)} .g{color:white} .h{opacity:.5}")
    rules = dict(_rules(css))
    assert NON_HEX.search(rules[".a"]) and HEX.findall(rules[".c"]) == ["0008"]
    assert BANNED.search(css) and EXTERNAL.search(css) and NAMED.search(rules[".g"])
    html = tmp_path / "x.html"
    html.write_text('<style>.x{color:#fff}</style><p style="color:red">', encoding="utf-8")
    assert "color:red" in _css_of(html)


def test_guard_catches_named_colours_and_banned_effects():
    def named(css):
        return [NAMED.search(v).group(0) for _, b in _rules(css) for v in _values(b) if NAMED.search(v)]
    for value in ("Tomato", "REBECCAPURPLE", "lightgoldenrodyellow", "darkslategrey", "tan", "Canvas",
                  "1px solid gold", "white !important"):
        assert named(f".a{{border-color:{value}}}"), value
    # allowed: hex, tokens whose names contain a colour, the keywords, colour words outside values
    for ok in (".white .red{color:#fff}", "/* red */ .a{color:#000}", ".a{color:var(--green)}",
               ".a{background:transparent;color:currentColor;fill:inherit}", ".a{--green:#0f0}",
               '.a{font-family:"Gold Sans",monospace}', ".a{background:url(red.png)}",
               ".a{grid-area:x; cursor:default}"):
        assert not named(ok), ok
    for css in ("-webkit-filter:blur(1px)", ".a{-webkit-filter:none}", ".a{-webkit-backdrop-filter:none}",
                ".a{mix-blend-mode:screen}", ".a{filter:drop-shadow(0 0 1px #000)}", "drop-shadow (",
                ".a{-webkit-box-shadow:0 0 1px #000}", ".a{-webkit-animation:x 1s}",
                "@-webkit-keyframes x{}", ".a{background-blend-mode:screen}", ".a{filter:none}"):
        assert BANNED.search(css), css
    assert not BANNED.search(".a{color:#fff; border:1px solid #333}")


def test_guard_catches_transitions_off_the_knob():
    assert _bad_transitions(".sw i{transition:left 120ms}", knob_allowed=True) == []
    assert _bad_transitions(".sw  i{transition:none}", knob_allowed=True) == []
    for sel in (".x .sw i", ".sw.s2 i", ".sw i, .y", ".sw", ".sw i b", "div.sw i"):
        assert _bad_transitions(f"{sel}{{transition:left 1s}}", knob_allowed=True), sel
    assert _bad_transitions(".sw i{transition:left 120ms}", knob_allowed=False)  # other files: none
    assert _bad_transitions(".a{-webkit-transition:left 1s}", knob_allowed=True)


# Pages the hub serves under its CSP (style-src 'self'): no style="" and no inline <style> either.
SERVED_HTML = sorted(WEB.glob("*.html")) + [TABLET / "index.html"]
# Every page this repo ships, including the Tauri shell's first pages (those keep a <style> block:
# they load before the hub exists, and the CSS scans above read it).
ALL_HTML = SERVED_HTML + (sorted(DIST.glob("*.html")) if DIST.is_dir() else [])
STYLE_ATTR = re.compile(r"""\sstyle\s*=""", re.I)
STYLE_BLOCK = re.compile(r"<style\b", re.I)


def test_no_style_attributes_in_html():
    bad = [f.relative_to(APP).as_posix() for f in ALL_HTML if STYLE_ATTR.search(f.read_text(encoding="utf-8"))]
    bad += [f"{f.relative_to(APP).as_posix()}: <style>" for f in SERVED_HTML
            if STYLE_BLOCK.search(f.read_text(encoding="utf-8"))]
    assert (TABLET / "index.html") in SERVED_HTML and not bad, bad


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


# -- JS scans (app/web/js/**/*.js and app/web/tablet/*.js; *.test.mjs are tests, not page code) ------

def _js_files() -> list[Path]:
    files = sorted((WEB / "js").rglob("*.js")) + sorted(TABLET.glob("*.js"))
    return [f for f in files if f.is_file() and not f.name.endswith(".test.mjs")]


def _rel(f: Path) -> str:
    return f.relative_to(WEB).as_posix()


# The tablet page lays itself out absolutely from JS for the SM-T110's Android 4.2 WebKit (fixed
# 1024x600 canvas, scale transform): e.style.left/top/width/height/fontSize/transform. CSSOM writes
# are not blocked by a style-src CSP (only style="" / <style> are), and /tablet has no CSP, but the
# rule is "no inline styles from JS", so this is a known violation for the lead to rule on.
STYLE_XFAIL: dict[str, str] = {}
# The one ruled exception (phase 4.5 decisions, R-T1): the workbench tablet's fixed 1024×600 layout is
# placed from JS, as the arm UI does, for the SM-T110's Android 4.2 WebKit. The tablet page has no CSP.
# Only geometry and text placement may be written there, never colours, borders or effects.
TABLET_LAYOUT_JS = "tablet/tablet.js"
TABLET_LAYOUT_PROPS = {"left", "top", "width", "height", "fontSize", "lineHeight", "textAlign", "transform",
                       "webkitTransform"}
_PROP_WRITE = re.compile(r"\.style\.([A-Za-z]+)\s*[-+*/]?=(?!=)")
HTML_XFAIL: dict[str, str] = {}
URL_XFAIL: dict[str, str] = {}


def _js_params(xfail: dict[str, str]):
    out = []
    for f in _js_files():
        rel = _rel(f)
        marks = [pytest.mark.xfail(reason=xfail[rel], strict=True)] if rel in xfail else []
        out.append(pytest.param(f, id=rel, marks=marks))
    return out


def _strip_js_comments(src: str) -> str:
    """The source with // and /* */ comments blanked (newlines kept, so offsets and line numbers
    hold). Strings, template literals and regex literals are skipped over, not blanked."""
    out, i, n, prev = list(src), 0, len(src), ""
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c and (c == "`" or src[j] != "\n"):
                j += 2 if src[j] == "\\" else 1
            i, prev = j + 1, c
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in src[i:j]]
            i = j
            continue
        if c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):  # a regex literal
            j, cls = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    cls = True
                elif src[j] == "]":
                    cls = False
                elif src[j] == "/" and not cls:
                    break
                j += 1
            i, prev = j + 1, "/"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return "".join(out)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _find(patterns, code: str) -> list[tuple[int, str]]:
    return sorted({(_line(code, m.start()), name) for name, pat in patterns for m in pat.finditer(code)})


_Q = r"""['"`]"""
# Writes only: reading e.style.x or getComputedStyle() is fine.
STYLE_WRITES = [(name, re.compile(p)) for name, p in (
    ("style key", rf"(?<![\w$.\-]){_Q}?style{_Q}?\]?\s*:"),  # el('div', {style|'style'|"style": ...})
    ("setAttribute style", rf"\bsetAttribute\s*\(\s*{_Q}style{_Q}"),
    ("setAttributeNS style", rf"\bsetAttributeNS\s*\([^,()]*,\s*{_Q}style{_Q}"),
    ("style write", r"\.style\b\s*(?:\.\s*[\w$]+|\[[^\]]*\])?\s*(?:\*\*|\|\||&&|\?\?|<<|>>>?|[-+*/%&|^])?=(?!=)"),
    ("style method", r"\.style\s*\.\s*(?:setProperty|removeProperty)\s*\("),
    ("Object.assign style", r"\bObject\s*\.\s*assign\s*\(\s*[^,()]*\.style\b"),
    ("cssText write", r"\.cssText\s*=(?!=)"),
    ("attributeStyleMap", r"\battributeStyleMap\s*\.\s*(?:set|append|delete|clear)\s*\("),
    ("<style> element", rf"(?:\bcreateElement|(?<![\w$.])el)\s*\(\s*{_Q}style{_Q}"),
    ("stylesheet API", r"\b(?:insertRule|adoptedStyleSheets|CSSStyleSheet|replaceSync)\b"),
)]


def _style_writes(src: str) -> list[tuple[int, str]]:
    return _find(STYLE_WRITES, _strip_js_comments(src))


@pytest.mark.parametrize("f", _js_params(STYLE_XFAIL))
def test_el_never_gets_a_style(f):
    """Rule 6's sibling: no inline styles from JS either (the CSP has no 'unsafe-inline' for styles)."""
    src = f.read_text(encoding="utf-8")
    lines = _strip_js_comments(src).splitlines()
    bad = []
    for ln, name in _style_writes(src):
        if _rel(f) == TABLET_LAYOUT_JS:
            props = _PROP_WRITE.findall(lines[ln - 1]) if 0 < ln <= len(lines) else []
            if props and all(p in TABLET_LAYOUT_PROPS for p in props):
                continue  # geometry only: the ruled tablet exception
        bad.append(f"{_rel(f)}:{ln} {name}")
    assert not bad, bad


def test_guard_catches_style_writes():
    for src in ("el('div', {style: 'color:#fff'})", "el('div', {'style': 'x'})", 'el("p", {"style": x})',
                "el('p', {`style`: x})", "el('p', {['style']: x})", "n.setAttribute('style', 'x')",
                "n.setAttribute(`style`, s)", 'n.setAttribute( "style" ,s)', "n.setAttributeNS(null, 'style', s)",
                "n.style.left = '1px'", "n.style.webkitTransform=v", "a.b.style.width += 'px'",
                "n.style.cssText = 'x'", "n.style['left'] = '1px'", "n.style = 'color:red'",
                "n.style.setProperty('left', '1px')", "n.style.removeProperty('left')",
                "Object.assign(n.style, {left: '1px'})", "Object.assign( a.b.style ,s)", "x.cssText='a'",
                "n.attributeStyleMap.set('left', CSS.px(1))", "document.createElement('style')",
                "el('style', {}, css)", "document.adoptedStyleSheets = [s]", "new CSSStyleSheet()",
                "sheet.insertRule('a{}')", "a(); /* note */ n.style.top = y",
                # a regex literal holding // or a quote must not hide what follows as comment/string
                "var re = /a\\/\\//; n.style.top = y", "var re = /'/; n.style.top = y",
                "var s = '//'; n.style.top = y", "var t = `a ${b} //`; n.style.top = y"):
        assert _style_writes(src), src
    for src in ("const w = n.style.width;", "if (n.style.left === '1px') x();", "getComputedStyle(n).left",
                "n.style.left == y", "el('div', {class: 'font-style'})", "x = {'data-style': 1, fontstyle: 2}",
                "// n.style.left = 1", "/* el('p', {style: 1}) */",
                "n.styles = 1", "s = '.sw i{transition:none}'"):
        assert not _style_writes(src), src
    assert _style_writes("a\nb\n  n.style.top = y") == [(3, "style write")]


# -- HTML sinks (rule 6): text enters the page only through el()/textContent -------------------------
HTML_SINKS = [(name, re.compile(p)) for name, p in (
    ("HTML property", r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment|setHTMLUnsafe|"
                      r"parseHTMLUnsafe|srcdoc)\b"),
    ("setHTML", r"\.\s*setHTML\s*\("),
    ("document.write", r"\bdocument\s*\.\s*write(?:ln)?\s*\("),
    ("DOMParser text/html", r"\bparseFromString\s*\((?![^;]*" + _Q + r"(?:text|application)/xml" + _Q + r")"),
)]
# dom.js's el() refuses these attrs by name; that refusal (this exact line, only in dom.js) is the
# one allowed mention.
DOM_GUARD = ("if (k === 'innerHTML' || k === 'outerHTML' || k === 'srcdoc') "
             "throw new Error(`el(): ${k} is not allowed`);")


def _html_sinks(rel: str, src: str) -> list[tuple[int, str]]:
    lines = src.split("\n")
    return [(ln, name) for ln, name in _find(HTML_SINKS, _strip_js_comments(src))
            if not (rel == "js/dom.js" and lines[ln - 1].strip() == DOM_GUARD)]


@pytest.mark.parametrize("f", _js_params(HTML_XFAIL))
def test_no_html_sinks_in_js(f):
    bad = [f"{_rel(f)}:{ln} {name}" for ln, name in _html_sinks(_rel(f), f.read_text(encoding="utf-8"))]
    assert not bad, bad


def test_dom_js_still_refuses_html():
    """The allowance above must have something to allow: el() still throws on HTML attrs."""
    lines = [ln.strip() for ln in (WEB / "js" / "dom.js").read_text(encoding="utf-8").split("\n")]
    assert lines.count(DOM_GUARD) == 1


def test_guard_catches_html_sinks():
    for src in ("n.innerHTML = s", "n.outerHTML = s", "n.insertAdjacentHTML('beforeend', s)",
                "document.write(s)", "document . writeln(s)", "range.createContextualFragment(s)",
                "new DOMParser().parseFromString(s, 'text/html')", 'p.parseFromString(s, "application/xhtml+xml")',
                "p.parseFromString(s, mime)", "f.srcdoc = s", "n.setHTMLUnsafe(s)", "n.setHTML(s)",
                "n['innerHTML'] = s", "el('div', {innerHTML: s})", "x = 1; n.innerHTML = s"):
        assert _html_sinks("js/x.js", src), src
        assert _html_sinks("js/dom.js", src), src  # dom.js gets only its one guard line, nothing else
    for src in ("n.textContent = s", "// never innerHTML", "/* innerHTML */ n.append(t)",
                "new DOMParser().parseFromString(s, 'text/xml')", "n.innerText.length", "my_innerHTMLish = 1"):
        assert not _html_sinks("js/x.js", src), src
    assert not _html_sinks("js/dom.js", "  " + DOM_GUARD)
    assert _html_sinks("js/other.js", "  " + DOM_GUARD)
    assert _html_sinks("js/dom.js", "  " + DOM_GUARD.replace("'srcdoc'", "'x'"))


# -- External loads -----------------------------------------------------------------------------------
CSP_META = re.compile(r"""<meta\s[^>]*http-equiv\s*=\s*["']?Content-Security-Policy[^>]*>""", re.I)
HTML_EXTERNAL = [(name, re.compile(p, re.I)) for name, p in (
    ("absolute URL", r"\b(?:https?|wss?|ftp)://(?!www\.w3\.org/)"),  # w3.org: xmlns namespaces, not loads
    ("protocol-relative URL", r"""(?:\s|^)[\w:-]+\s*=\s*["']?\s*//"""),
    ("srcset URL", r"""\bsrcset\s*=\s*["'][^"']*(?:,\s*)?(?:[a-z][a-z0-9+.-]*:)?//"""),
    ("web font / CDN host", r"fonts\.googleapis|fonts\.gstatic|use\.typekit|fonts\.bunny|cdn\.jsdelivr|unpkg\.com|"
                            r"cdnjs\.|googletagmanager|google-analytics|ajax\.googleapis"),
    ("meta refresh", r"""http-equiv\s*=\s*["']?refresh"""),
    ("@import", r"@import"),
)]


def _html_external(text: str) -> list[tuple[int, str]]:
    # blank the CSP meta (it names ws://127.0.0.1:* for the hub's socket) but keep its line count
    text = CSP_META.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return _find(HTML_EXTERNAL, text)


@pytest.mark.parametrize("f", ALL_HTML, ids=lambda f: f.relative_to(APP).as_posix())
def test_html_loads_nothing_from_outside(f):
    bad = [f"{f.name}:{ln} {name}" for ln, name in _html_external(f.read_text(encoding="utf-8"))]
    assert not bad, bad


# Absolute URLs that may appear in page JS, each with why it is not a load. Keyed by file and the URL
# text as written; nothing here may sit on a line that fetches, imports, connects or assigns src/href.
JS_URL_ALLOW = {
    ("js/widget.js", "http://www.w3.org/2000/svg"): "SVG namespace for createElementNS",
    ("js/views/tablet-model.js", "http://${s.listening}:${s.port}/tablet"):
        "status text telling the owner the tablet's LAN address; shown, never fetched",
    ("js/views/setup.js", "https://github.com/you/atlas-backup.git"): "placeholder hint in the backup-remote field",
    ("js/views/settings-sections.js", "https://github.com/you/atlas-backup.git"):
        "placeholder hint in the backup-remote field",
}
JS_URL = re.compile(r"""(?:https?|wss?|ftp)://[^\s'"`]*|(?<=['"`])//[\w$.-]+[^\s'"`]*""", re.I)
JS_LOADER = re.compile(r"""\b(?:fetch|import|importScripts|WebSocket|EventSource|Worker|SharedWorker|sendBeacon|
                           open|Image|Audio|FontFace)\s*\(|\bfrom\s*['"`]|\.(?:src|href|action|srcset)\s*=""", re.X)


def _js_urls(rel: str, src: str) -> list[tuple[int, str]]:
    code = _strip_js_comments(src)
    lines = code.split("\n")
    bad = []
    for m in JS_URL.finditer(code):
        ln = _line(code, m.start())
        if (rel, m.group(0)) in JS_URL_ALLOW and not JS_LOADER.search(lines[ln - 1]):
            continue
        bad.append((ln, m.group(0)))
    return bad


@pytest.mark.parametrize("f", _js_params(URL_XFAIL))
def test_js_loads_nothing_from_outside(f):
    bad = [f"{_rel(f)}:{ln} {url}" for ln, url in _js_urls(_rel(f), f.read_text(encoding="utf-8"))]
    assert not bad, bad


def test_guard_catches_external_loads():
    for html in ('<script src="https://cdn.x/a.js"></script>', "<link href='//fonts.example/x.css'>",
                 '<img src=//x.y/a.png>', '<link rel="stylesheet" href="https://fonts.googleapis.com/css2">',
                 '<link rel="preconnect" href="fonts.gstatic.com">', '<a href="http://x">x</a>',
                 '<img srcset="/a.png 1x, //x.y/b.png 2x">', '<meta http-equiv="refresh" content="0;url=/x">',
                 '<form action="https://x/y">', '<iframe src="wss://x"></iframe>'):
        assert _html_external(html), html
    for html in ('<link rel="stylesheet" href="/static/css/base.css">', '<link rel="icon" href="data:,">',
                 '<a href="#/today">', '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                 '<meta http-equiv="Content-Security-Policy" content="connect-src \'self\' ws://127.0.0.1:*">'):
        assert not _html_external(html), html
    for src in ("fetch('https://x/y')", "import('https://esm.sh/x')", "import x from 'https://x/y.js';",
                "new WebSocket('wss://x')", "fetch('//x.y/z')", "img.src = `http://${h}/a.png`",
                "const u = 'https://example.com';", "fetch('https://github.com/you/atlas-backup.git')"):
        assert _js_urls("js/x.js", src), src
    # an allow-listed URL is still caught when the line loads it, and in any other file
    assert _js_urls("js/widget.js", "fetch('http://www.w3.org/2000/svg')")
    assert _js_urls("js/api.js", "const SVG = 'http://www.w3.org/2000/svg';")
    for src in ("const SVG = 'http://www.w3.org/2000/svg';", "// see https://example.com",
                "location.protocol === 'https:'", "fetch('/api/state')", "const re = /^\\/\\//;"):
        assert not _js_urls("js/widget.js", src), src


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
