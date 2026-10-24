"""
Milestone 1 - CSS Design-Token Extractor  (pure standard library)

Instead of returning raw text, this parser turns a stylesheet into *design
knowledge*:

  * variables   : every --custom-property, with var() chains fully resolved
                  (circular / undefined references are detected, not crashed on)
  * colors      : every color normalised to hex, counted by real usage
                  (a `color: var(--brand)` counts towards #1a73e8)
  * typography  : font families and font sizes (with a px estimate)
  * breakpoints : the widths used inside @media queries
  * overrides   : declarations that are dead because a later one wins
  * contrast    : WCAG ratio of color vs background in the same rule
  * palette drift: near-duplicate colors (#1a73e8 vs #1b74e9) = design smell
  * specificity : the most specific selectors (hard to override later)

Robustness: a hand-written scanner that understands comments, strings,
url(...) and nested at-rules, so `content: "}"` or
`url(data:image/png;base64,...;...)` never break the parse.
"""
from __future__ import annotations

import bisect
import colorsys
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Low level text helpers
# --------------------------------------------------------------------------


def strip_comments(css: str) -> str:
    """Blank out /* ... */ comments but keep newlines, so line numbers stay valid.
    Comment markers inside strings are left alone."""
    out, i, n, quote = [], 0, len(css), None
    while i < n:
        c = css[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(css[i + 1])
                i += 2
                continue
            if c == quote or c == "\n":  # an unescaped newline ends a bad string
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = n if end == -1 else end + 2  # unterminated comment -> to EOF
            out.append("".join(ch if ch == "\n" else " " for ch in css[i:end]))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _scan(text: str, i: int, stops: str, end: int):
    """Advance from i until a char in `stops` appears at paren-depth 0 and outside
    a string. Returns (index, stop_char); stop_char == '' means hit `end`."""
    depth, quote = 0, None
    while i < end:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or c == "\n":
                quote = None
        elif c in "\"'":
            quote = c
        elif c in stops and depth == 0:
            return i, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        i += 1
    return end, ""


def _match_brace(text: str, i: int, end: int) -> int:
    """`i` is just after an opening '{'. Return index of its matching '}', or
    `end` if the block is never closed."""
    depth, quote = 1, None
    while i < end:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or c == "\n":
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return end


def _split_top(s: str, sep: str) -> list[str]:
    parts, i = [], 0
    while True:
        j, stop = _scan(s, i, sep, len(s))
        parts.append(s[i:j].strip())
        if not stop:
            break
        i = j + 1
    return [p for p in parts if p]


class _Lines:
    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


# --------------------------------------------------------------------------
# Data model + parser
# --------------------------------------------------------------------------


@dataclass
class Declaration:
    prop: str
    value: str
    important: bool
    line: int


@dataclass
class Rule:
    selector: str
    declarations: list
    context: tuple  # e.g. ("@media (max-width: 600px)",)
    line: int
    order: int


@dataclass
class AtRule:
    name: str
    prelude: str
    line: int
    context: tuple


@dataclass
class Stylesheet:
    rules: list = field(default_factory=list)
    at_rules: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


_PROP_RE = re.compile(r"--[\w-]+|-?[A-Za-z_][\w-]*")
_CONTAINER_AT_RULES = {"media", "supports", "layer", "container", "document"}
_DECL_AT_RULES = {"font-face", "page", "property"}


def _parse_declarations(body, base, lines, warnings):
    decls, i, n = [], 0, len(body)
    while i < n:
        j, stop = _scan(body, i, ";{", n)
        if stop == "{":  # CSS nesting: not supported, skip it safely
            close = _match_brace(body, j + 1, n)
            warnings.append(f"line {lines.of(base + j)}: nested rule skipped (CSS nesting not parsed)")
            i = close + 1
            continue
        chunk = body[i:j]
        if chunk.strip():
            k = chunk.find(":")
            lead = len(chunk) - len(chunk.lstrip())
            line = lines.of(base + i + lead)
            if k > 0:
                prop = chunk[:k].strip()
                val = chunk[k + 1 :].strip()
                imp = False
                m = re.search(r"!\s*important\s*$", val, re.I)
                if m:
                    imp, val = True, val[: m.start()].strip()
                if _PROP_RE.fullmatch(prop) and val:
                    if not prop.startswith("--"):
                        prop = prop.lower()
                    decls.append(Declaration(prop, val, imp, line))
                else:
                    warnings.append(f"line {line}: malformed declaration ignored: {chunk.strip()[:40]!r}")
            else:
                warnings.append(f"line {line}: malformed declaration ignored: {chunk.strip()[:40]!r}")
        i = j + 1
    return decls


def _parse_rules(text, start, end, ctx, sheet, lines):
    i = start
    while i < end:
        j, stop = _scan(text, i, "{;}", end)
        prelude = " ".join(text[i:j].split())
        if not stop:
            if prelude:
                sheet.warnings.append(f"line {lines.of(i)}: trailing text ignored: {prelude[:40]!r}")
            break
        if stop == "}":  # stray closing brace
            sheet.warnings.append(f"line {lines.of(j)}: stray '}}' ignored")
            i = j + 1
            continue
        if stop == ";":  # statement at-rule such as @import / @charset
            if prelude.startswith("@"):
                name = re.match(r"@([\w-]+)", prelude)
                sheet.at_rules.append(AtRule(name.group(1).lower() if name else "?", prelude, lines.of(i), ctx))
            i = j + 1
            continue
        # stop == "{"
        close = _match_brace(text, j + 1, end)
        line = lines.of(i + (len(text[i:j]) - len(text[i:j].lstrip())))
        if close >= end:
            sheet.warnings.append(f"line {line}: block '{prelude[:30]}' is never closed (parsed up to end of file)")
        body_start = j + 1
        if prelude.startswith("@"):
            m = re.match(r"@([\w-]+)", prelude)
            name = m.group(1).lower() if m else "?"
            sheet.at_rules.append(AtRule(name, prelude, line, ctx))
            if name in _CONTAINER_AT_RULES:
                _parse_rules(text, body_start, close, ctx + (prelude,), sheet, lines)
            elif name in _DECL_AT_RULES:
                d = _parse_declarations(text[body_start:close], body_start, lines, sheet.warnings)
                sheet.rules.append(Rule(prelude, d, ctx, line, len(sheet.rules)))
            # @keyframes etc.: recorded, body skipped on purpose
        else:
            d = _parse_declarations(text[body_start:close], body_start, lines, sheet.warnings)
            sheet.rules.append(Rule(prelude, d, ctx, line, len(sheet.rules)))
        i = close + 1


def parse(css: str) -> Stylesheet:
    clean = strip_comments(css)
    sheet = Stylesheet()
    _parse_rules(clean, 0, len(clean), (), sheet, _Lines(clean))
    return sheet


# --------------------------------------------------------------------------
# Colors
# --------------------------------------------------------------------------

NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "navy": "#000080",
    "teal": "#008080", "maroon": "#800000", "olive": "#808000", "lime": "#00ff00",
    "aqua": "#00ffff", "cyan": "#00ffff", "fuchsia": "#ff00ff", "magenta": "#ff00ff",
    "pink": "#ffc0cb", "brown": "#a52a2a", "gold": "#ffd700", "coral": "#ff7f50",
    "crimson": "#dc143c", "indigo": "#4b0082", "tomato": "#ff6347",
    "whitesmoke": "#f5f5f5", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3",
    "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "slategray": "#708090",
}
HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
FUNC_RE = re.compile(r"\b(rgba?|hsla?)\(([^()]*)\)", re.I)
NAMED_RE = re.compile(r"(?<![\w#-])(" + "|".join(NAMED) + r")(?![\w-])", re.I)


def _num(tok: str) -> float:
    return float(tok[:-1]) / 100 if tok.endswith("%") else float(tok)


def parse_color(token: str):
    """'#fff' | 'rgb(...)' | 'hsl(...)' | 'red'  ->  ('#rrggbb', alpha) or None."""
    t = token.strip().lower()
    if t in NAMED:
        return NAMED[t], 1.0
    if t.startswith("#") and HEX_RE.fullmatch(t):
        h = t[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        alpha = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return "#" + h[:6], round(alpha, 2)
    m = FUNC_RE.fullmatch(t)
    if m:
        name, args = m.group(1), [p for p in re.split(r"[\s,/]+", m.group(2).strip()) if p]
        if len(args) < 3:
            return None
        try:
            alpha = max(0.0, min(1.0, _num(args[3]))) if len(args) > 3 else 1.0
            if name.startswith("rgb"):
                rgb = [max(0, min(255, round(float(a[:-1]) * 2.55 if a.endswith("%") else float(a)))) for a in args[:3]]
            else:
                h = float(re.sub(r"deg$", "", args[0])) % 360 / 360
                s, l = _num(args[1]), _num(args[2])
                r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l)), max(0, min(1, s)))
                rgb = [round(r * 255), round(g * 255), round(b * 255)]
        except ValueError:
            return None
        return "#%02x%02x%02x" % tuple(rgb), round(alpha, 2)
    return None


def _color_key(hexv, alpha):
    return hexv if alpha >= 1 else f"{hexv}@{alpha:g}"


def _is_color_prop(p: str) -> bool:
    return (
        p in {"color", "fill", "stroke", "box-shadow", "text-shadow", "caret-color", "accent-color", "outline"}
        or p.endswith("-color")
        or p.startswith("border")
        or p.startswith("background")
    )


def extract_colors(prop: str, value: str) -> list:
    """All colors in a value, left to right. url(...) and strings are ignored
    (so `fill: url(#abc)` is not mistaken for the hex color #abc)."""
    v = re.sub(r"url\([^)]*\)", " ", value, flags=re.I)
    v = re.sub(r"\"[^\"]*\"|'[^']*'", " ", v)
    found = []
    for rx in (HEX_RE, FUNC_RE) + ((NAMED_RE,) if _is_color_prop(prop) else ()):
        for m in rx.finditer(v):
            c = parse_color(m.group(0))
            if c:
                found.append((m.start(), c))
    return [c for _, c in sorted(found)]


def _lin(c: int) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hexv: str) -> float:
    r, g, b = (int(hexv[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two #rrggbb colors."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------
# var() resolution
# --------------------------------------------------------------------------


def resolve_value(value: str, env: dict, stack: tuple = ()):
    """Expand every var(--x[, fallback]). Returns (resolved, issues, used_vars).
    Circular and undefined references are reported and left untouched."""
    out, issues, used, i = [], [], [], 0
    while True:
        k = value.find("var(", i)
        if k == -1:
            out.append(value[i:])
            break
        out.append(value[i:k])
        j, stop = _scan(value, k + 4, ")", len(value))
        inner = value[k + 4 : j]
        name, _, fallback = inner.partition(",")
        name, fallback = name.strip(), fallback.strip()
        original = value[k : j + 1] if stop else value[k:]
        if name in stack:
            issues.append("circular reference: " + " -> ".join(stack + (name,)))
            out.append(original)
        elif name in env:
            used.append(name)
            r, sub, sub_used = resolve_value(env[name], env, stack + (name,))
            out.append(r)
            issues += sub
            used += sub_used
        elif fallback:
            r, sub, sub_used = resolve_value(fallback, env, stack)
            out.append(r)
            issues += sub
            used += sub_used
        else:
            issues.append(f"undefined variable {name}")
            out.append(original)
        i = j + 1
        if not stop:
            break
    return "".join(out), issues, used


# --------------------------------------------------------------------------
# Selector specificity (approximation good enough for audits)
# --------------------------------------------------------------------------

_ZERO_PSEUDO = {"not", "is", "where", "has", "matches"}


def specificity(selector: str) -> tuple:
    s = re.sub(r"\"[^\"]*\"|'[^']*'", '""', selector)
    s = re.sub(r"\[[^\]]*\]", " [a] ", s)
    ids = len(re.findall(r"#[\w-]+", s))
    classes = len(re.findall(r"\.[\w-]+", s)) + s.count("[a]")
    classes += sum(1 for p in re.findall(r"(?<!:):([\w-]+)", s) if p.lower() not in _ZERO_PSEUDO)
    pseudo_el = len(re.findall(r"::[\w-]+", s))
    rest = re.sub(r"[#.][\w-]+|::?[\w-]+(?:\([^)]*\))?|\[a\]", " ", s)
    elems = len(re.findall(r"(?<![\w-])[A-Za-z][\w-]*", rest)) + pseudo_el
    return (ids, classes, elems)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

_ROOT_SELECTORS = {":root", "html"}
_BP_RE = re.compile(r"\(\s*(min|max)-width\s*:\s*([\d.]+)(px|em|rem)\s*\)", re.I)
_BP_RANGE_RE = re.compile(r"\(\s*width\s*(>=|<=|>|<)\s*([\d.]+)(px|em|rem)\s*\)", re.I)


def _to_px(num: str, unit: str) -> float:
    return round(float(num) * (1 if unit.lower() == "px" else 16), 2)


class Analysis:
    """Parsed + interpreted stylesheet. `.report` is the JSON-friendly result."""

    def __init__(self, css: str, source: str = "<string>"):
        self.css, self.source = css, source
        self.sheet = parse(css)
        self.warnings = list(self.sheet.warnings)
        self._collect_variables()
        self._resolve_declarations()
        self.report = self._build_report()

    # -- variables ---------------------------------------------------------
    def _collect_variables(self):
        self.root_env, self.var_entries = {}, []
        for rule in self.sheet.rules:
            sels = [s.strip() for s in _split_top(rule.selector, ",")]
            is_root = not rule.context and any(s in _ROOT_SELECTORS for s in sels)
            for d in rule.declarations:
                if d.prop.startswith("--"):
                    self.var_entries.append((rule, d))
                    if is_root:
                        self.root_env[d.prop] = d.value  # last one wins
        self.variables = []
        for rule, d in self.var_entries:
            local = {x.prop: x.value for x in rule.declarations if x.prop.startswith("--")}
            env = {**self.root_env, **local}
            resolved, issues, _ = resolve_value(d.value, env, (d.prop,))
            status = "ok"
            if any(i.startswith("circular") for i in issues):
                status = "circular"
            elif any(i.startswith("undefined") for i in issues):
                status = "undefined_reference"
            for i in issues:
                self.warnings.append(f"line {d.line}: {d.prop}: {i}")
            cols = extract_colors("color", resolved)
            self.variables.append({
                "name": d.prop, "value": d.value, "resolved": resolved, "scope": rule.selector,
                "context": list(rule.context), "line": d.line, "status": status,
                "is_color": len(cols) == 1 and parse_color(resolved) is not None,
            })

    # -- declarations ------------------------------------------------------
    def _resolve_declarations(self):
        self.resolved = []  # (rule, decl, resolved_value, used_vars)
        for rule in self.sheet.rules:
            local = {x.prop: x.value for x in rule.declarations if x.prop.startswith("--")}
            env = {**self.root_env, **local}
            for d in rule.declarations:
                if d.prop.startswith("--"):
                    continue
                rv, issues, used = resolve_value(d.value, env)
                for i in issues:
                    self.warnings.append(f"line {d.line}: {d.prop}: {i}")
                self.resolved.append((rule, d, rv, used))

    # -- report ------------------------------------------------------------
    def _build_report(self) -> dict:
        r = {"source": self.source}

        colors = defaultdict(lambda: {"count": 0, "properties": Counter(), "via": set(), "first_line": 10**9})
        for rule, d, rv, used in self.resolved:
            for hexv, alpha in extract_colors(d.prop, rv):
                c = colors[_color_key(hexv, alpha)]
                c["count"] += 1
                c["properties"][d.prop] += 1
                c["via"].update(used)
                c["first_line"] = min(c["first_line"], d.line)
        defined_as = defaultdict(list)
        for v in self.variables:
            col = parse_color(v["resolved"]) if v["is_color"] else None
            if col:
                defined_as[_color_key(*col)].append(v["name"])
        for key, names in defined_as.items():
            if key not in colors:  # declared in a variable but never used
                colors[key]["first_line"] = min(v["line"] for v in self.variables if v["name"] in names)
        r["colors"] = sorted(
            ({"hex": k, "count": v["count"], "properties": dict(sorted(v["properties"].items())),
              "via_variables": sorted(v["via"]), "defined_as": sorted(set(defined_as.get(k, []))),
              "first_line": v["first_line"]} for k, v in colors.items()),
            key=lambda c: (-c["count"], c["hex"]))

        r["variables"] = self.variables

        fam, sizes = Counter(), Counter()
        for rule, d, rv, used in self.resolved:
            if d.prop == "font-family":
                first = _split_top(rv, ",")
                if first:
                    fam[first[0].strip("\"' ")] += 1
            elif d.prop == "font-size":
                sizes[rv.strip()] += 1

        def px(v):
            m = re.fullmatch(r"([\d.]+)(px|rem|em)", v)
            return _to_px(m.group(1), m.group(2)) if m else None

        r["typography"] = {
            "font_families": [{"family": f, "count": c} for f, c in sorted(fam.items(), key=lambda x: (-x[1], x[0]))],
            "font_sizes": [{"value": v, "px": px(v), "count": c}
                           for v, c in sorted(sizes.items(), key=lambda x: (px(x[0]) is None, px(x[0]) or 0, x[0]))],
        }

        bps = {}
        for rule in self.sheet.rules:
            for ctx in rule.context:
                if ctx.lower().startswith("@media"):
                    bps[ctx] = bps.get(ctx, 0) + 1
        for at in self.sheet.at_rules:
            if at.name == "media":
                bps.setdefault(at.prelude, 0)
        bp_list = []
        for q, n in bps.items():
            found = [(f"{k.lower()}-width", _to_px(v, u)) for k, v, u in _BP_RE.findall(q)]
            found += [(f"width {op}", _to_px(v, u)) for op, v, u in _BP_RANGE_RE.findall(q)]
            bp_list.append({"query": q, "conditions": [{"kind": k, "px": p} for k, p in found], "rules": n})
        r["breakpoints"] = sorted(bp_list, key=lambda b: (b["conditions"][0]["px"] if b["conditions"] else 1e9, b["query"]))

        groups = defaultdict(list)
        for rule, d, rv, used in self.resolved:
            groups[(rule.selector, rule.context, d.prop)].append(d)
        overrides = []
        for (sel, ctx, prop), ds in groups.items():
            if len(ds) < 2:
                continue
            imp = [d for d in ds if d.important]
            win = (imp or ds)[-1]
            overrides.append({"selector": sel, "property": prop, "context": list(ctx),
                              "winner": {"value": win.value, "line": win.line},
                              "dead": [{"value": d.value, "line": d.line} for d in ds if d is not win]})
        r["overrides"] = sorted(overrides, key=lambda o: o["winner"]["line"])

        contrast = []
        for rule in self.sheet.rules:
            env = {**self.root_env, **{x.prop: x.value for x in rule.declarations if x.prop.startswith("--")}}
            fg = bg = None
            for d in rule.declarations:
                if d.prop in ("color", "background-color", "background"):
                    rv, _, _ = resolve_value(d.value, env)
                    cols = extract_colors(d.prop, rv)
                    if d.prop == "color" and len(cols) == 1:
                        fg = (cols[0], d.line)
                    elif d.prop != "color" and cols:
                        bg = (cols[0], d.line)
            if fg and bg and fg[0][1] >= 1 and bg[0][1] >= 1:
                ratio = contrast_ratio(fg[0][0], bg[0][0])
                contrast.append({"selector": rule.selector, "foreground": fg[0][0], "background": bg[0][0],
                                 "ratio": round(ratio, 2), "passes_aa_normal": ratio >= 4.5,
                                 "line": min(fg[1], bg[1])})
        r["contrast"] = contrast

        opaque = [c["hex"] for c in r["colors"] if "@" not in c["hex"]]
        drift = []
        for i, a in enumerate(opaque):
            for b in opaque[i + 1 :]:
                dist = sum((int(a[k : k + 2], 16) - int(b[k : k + 2], 16)) ** 2 for k in (1, 3, 5)) ** 0.5
                if 0 < dist <= 12:
                    drift.append({"a": a, "b": b, "distance": round(dist, 1)})
        r["palette_drift"] = sorted(drift, key=lambda x: (x["distance"], x["a"]))

        spec = []
        for rule in self.sheet.rules:
            if rule.selector.startswith("@"):
                continue
            for s in _split_top(rule.selector, ","):
                spec.append({"selector": s, "specificity": list(specificity(s)), "line": rule.line})
        r["specificity_top"] = sorted(spec, key=lambda x: (x["specificity"], -x["line"]), reverse=True)[:5]

        r["stats"] = {
            "rules": len(self.sheet.rules),
            "declarations": sum(len(x.declarations) for x in self.sheet.rules),
            "at_rules": len(self.sheet.at_rules),
            "variables": len(self.variables),
            "important_declarations": sum(1 for x in self.sheet.rules for d in x.declarations if d.important),
            "warnings": len(self.warnings),
        }
        r["warnings"] = self.warnings
        return r


def analyze(css: str, source: str = "<string>") -> Analysis:
    return Analysis(css, source)


# --------------------------------------------------------------------------
# Pretty printing for the CLI
# --------------------------------------------------------------------------


def format_summary(rep: dict) -> str:
    L = []
    s = rep["stats"]
    L.append(f"== CSS Design-Token Report: {rep['source']} ==")
    L.append(f"{s['rules']} rules | {s['declarations']} declarations | {s['variables']} variables | "
             f"{s['important_declarations']} !important | {s['warnings']} warnings")
    L.append("\n-- Variables (resolved) --")
    for v in rep["variables"]:
        extra = "" if v["status"] == "ok" else f"   [{v['status'].upper()}]"
        ctx = f" in {v['context'][0]}" if v["context"] else ""
        arrow = f" -> {v['resolved']}" if v["resolved"] != v["value"] else ""
        L.append(f"  {v['name']}: {v['value']}{arrow}  (line {v['line']}, {v['scope']}{ctx}){extra}")
    L.append("\n-- Colors by real usage --")
    for c in rep["colors"][:10]:
        via = f" via {', '.join(c['via_variables'])}" if c["via_variables"] else ""
        L.append(f"  {c['hex']:<16} x{c['count']:<3}{via}")
    if rep["palette_drift"]:
        L.append("  near-duplicates: " + ", ".join(f"{d['a']}~{d['b']}" for d in rep["palette_drift"]))
    L.append("\n-- Typography --")
    for f in rep["typography"]["font_families"]:
        L.append(f"  family {f['family']} x{f['count']}")
    L.append("  sizes: " + ", ".join(f"{z['value']}" + (f"({z['px']}px)" if z["px"] and not z["value"].endswith("px") else "")
                                      for z in rep["typography"]["font_sizes"]))
    L.append("\n-- Breakpoints --")
    for b in rep["breakpoints"]:
        L.append(f"  {b['query']}  ({b['rules']} rules)")
    L.append("\n-- Dead / overridden declarations --")
    for o in rep["overrides"]:
        dead = ", ".join(f"{d['value']} (line {d['line']})" for d in o["dead"])
        L.append(f"  {o['selector']} {{{o['property']}}} winner={o['winner']['value']} (line {o['winner']['line']}); dead: {dead}")
    L.append("\n-- Contrast (color vs background in same rule) --")
    for c in rep["contrast"]:
        L.append(f"  {c['selector']}: {c['foreground']} on {c['background']} = {c['ratio']}:1 "
                 + ("OK" if c["passes_aa_normal"] else "FAILS AA"))
    L.append("\n-- Most specific selectors --")
    for x in rep["specificity_top"][:3]:
        L.append(f"  {x['selector']}  {tuple(x['specificity'])}  (line {x['line']})")
    if rep["warnings"]:
        L.append("\n-- Warnings --")
        L += [f"  {w}" for w in rep["warnings"]]
    return "\n".join(L)
