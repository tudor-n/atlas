"""The tablet page must run on the SM-T110's Android 4.2 WebKit (phase 3 design §6.1; the arm UI
handoff §1): ES5 only, none of the newer DOM or CSS it lacks, and nothing that summons the Android
keyboard. This can't run the old browser, so it refuses what that browser can't do."""
import re
from pathlib import Path

import pytest

TABLET = Path(__file__).resolve().parent.parent / "web" / "tablet"
JS = sorted(p for p in TABLET.glob("*.js"))

_SYNTAX = {
    "arrow function": r"=>", "template string": r"`", "let": r"\blet\s", "const": r"\bconst\s",
    "class": r"\bclass\s+\w", "spread or rest": r"\.\.\.", "async": r"\basync\s", "await": r"\bawait\b",
    "for...of": r"for\s*\([^;)]*\bof\b", "yield": r"\byield\b", "import": r"^\s*import\b", "export": r"^\s*export\b",
    "optional chaining": r"\?\.", "nullish coalescing": r"\?\?", "exponent": r"\*\*",
    "default parameters": r"function\s*\w*\s*\([^)]*=", "destructuring": r"\bvar\s*[\[{]",
    "shorthand method": r"[{,]\s*(?!(?:if|for|while|switch|function|catch|with|return)\b)\w+\s*\([^()]*\)\s*\{", "computed key": r"[{,]\s*\[[^\]]+\]\s*:",
}
_APIS = {
    "includes": r"\.includes\(", "startsWith": r"\.startsWith\(", "endsWith": r"\.endsWith\(",
    "Array find": r"\.find\(", "findIndex": r"\.findIndex\(", "repeat": r"\.repeat\(", "padStart": r"\.pad(Start|End)\(",
    "Array.from": r"Array\.from", "Object.assign": r"Object\.assign", "Object.entries/values": r"Object\.(entries|values)",
    "fetch": r"\bfetch\(", "Promise": r"\bPromise\b", "Map/Set": r"\bnew (Map|Set|WeakMap|WeakSet)\b", "Symbol": r"\bSymbol\b",
    "unprefixed requestAnimationFrame": r"(?<![\w.])requestAnimationFrame\(", "WebSocket": r"\bWebSocket\b",
    "classList": r"\.classList\b", "dataset": r"\.dataset\b", "closest": r"\.closest\(", "Element.remove()": r"\.remove\(\)",
    "append/prepend": r"\.(append|prepend|before|after|replaceWith)\(", "passive listeners": r"\bpassive\b",
    "Number.isNaN/isInteger": r"Number\.is", "Math.trunc/sign": r"Math\.(trunc|sign)\b",
    "focus (would open the Android keyboard)": r"\.focus\(", "inputs": r"createElement\('(input|textarea)'",
    "pointer events": r"\bpointer(down|up|move)\b",
    "NodeList.forEach": r"querySelectorAll\([^)]*\)\.forEach", "Element.matches": r"\.matches\(",
    "listener options": r"addEventListener\([^)]*\{",
}
_CSS = {
    "grid or flex": r"display\s*:\s*(-webkit-)?(grid|flex|inline-flex|box)\b", "gap": r"(?<![\w-])gap\s*:",
    "variables": r"var\(", "flex-wrap": r"flex-wrap", "shadows": r"(box|text)-shadow", "transitions": r"\btransition",
    "animations": r"\banimation|@keyframes", "transparency": r"rgba\(|hsla?\(|\bopacity\s*:|transparent",  # see _css
    "gradients": r"gradient", "filters": r"(?<![\w-])filter\s*:|backdrop", "clip-path": r"clip-path",
    "calc": r"calc\(", "viewport units": r"\d+(vw|vh|vmin|vmax)\b", "fixed or sticky": r"position\s*:\s*(fixed|sticky)",
}


def _code(text: str) -> str:
    """The JS without comments and string literals (the page has no regex literal holding a quote)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append("''")
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def test_there_is_a_page():
    assert {p.name for p in JS} >= {"tablet.js", "kb.js", "logic.js"}


@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_es5_only(path):
    code = _code(path.read_text(encoding="utf-8"))
    found = [f"{name}: {m.group(0)!r} (line {code[:m.start()].count(chr(10)) + 1})"
             for table in (_SYNTAX, _APIS) for name, rx in table.items()
             for m in [re.search(rx, code, re.M)] if m]
    assert not found, f"{path.name} uses what Android 4.2's WebKit lacks: {found}"


def test_the_lint_catches_what_it_should():
    bad = ("var f = (x) => x; let y = `t`; [].includes(1); el.remove(); fetch('/x'); el.focus();"
           "function g(a, b = 1) {} var {a} = o; var o2 = { m() { return 1; } }; var o3 = { [k]: 1 };"
           "document.querySelectorAll('a').forEach(f); el.matches('.x'); el.addEventListener('t', f, { once: true });")
    code = _code(bad)
    hits = {name for table in (_SYNTAX, _APIS) for name, rx in table.items() if re.search(rx, code, re.M)}
    assert {"arrow function", "let", "template string", "includes", "Element.remove()", "fetch", "default parameters",
            "destructuring", "shorthand method", "computed key", "NodeList.forEach", "Element.matches",
            "listener options"} <= hits
    assert re.search(_APIS["focus (would open the Android keyboard)"], code)
    assert not re.search(_SYNTAX["arrow function"], _code("var s = '=>'; // =>"))


def test_css_has_nothing_the_old_webkit_lacks():
    css = re.sub(r"/\*.*?\*/", "", (TABLET / "tablet.css").read_text(encoding="utf-8"), flags=re.S)
    # The one allowed alpha: a transparent tap highlight paints nothing (the arm UI's own choice on this
    # device); an opaque one would cover the white press feedback.
    css = css.replace("-webkit-tap-highlight-color: rgba(0,0,0,0);", "")
    found = [name for name, rx in _CSS.items() if re.search(rx, css)]
    assert not found, f"tablet.css uses {found}"
    radii = [int(v) for v in re.findall(r"border-radius\s*:\s*(\d+)px", css)]
    assert radii and max(radii) <= 4, radii


def test_no_input_ever_summons_the_android_keyboard():
    html = (TABLET / "index.html").read_text(encoding="utf-8").lower()
    assert "<input" not in html and "<textarea" not in html and "contenteditable" not in html
    for path in JS:
        assert "contenteditable" not in path.read_text(encoding="utf-8").lower(), path.name
