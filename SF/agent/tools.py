"""
Milestone 2 - the agent's tools.

Every tool is a PURE, DETERMINISTIC function over the Milestone-1 analysis.
Each returns the same envelope so the agent can reason about missing data:

    {"status": "found" | "not_found" | "error", "data": ..., "message": str, "lines": [int]}

`not_found` always carries a human-readable message that lists what DOES exist,
so "I don't have that" is itself grounded in data.
"""
from __future__ import annotations

import re

from cssparse.parser import (Analysis, _split_top, contrast_ratio, extract_colors,
                             parse_color, resolve_value, specificity)

AA_NORMAL, AA_LARGE = 4.5, 3.0


def _ok(data, lines=()):
    return {"status": "found", "data": data, "message": "", "lines": sorted(set(lines))}


def _missing(message, data=None):
    return {"status": "not_found", "data": data, "message": message, "lines": []}


def _err(message):
    return {"status": "error", "data": None, "message": message, "lines": []}


def _norm_sel(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


class ToolBox:
    def __init__(self, analysis: Analysis):
        self.a = analysis
        self.rep = analysis.report

    # ---- dispatcher -------------------------------------------------------
    def call(self, name: str, args: dict | None = None) -> dict:
        fn = getattr(self, f"t_{name}", None) if name in TOOL_SCHEMAS else None
        if fn is None:
            return _err(f"unknown tool '{name}'. Available: {', '.join(sorted(TOOL_SCHEMAS))}")
        try:
            return fn(**(args or {}))
        except TypeError as e:
            return _err(f"bad arguments for {name}: {e}")

    # ---- tools ------------------------------------------------------------
    def t_get_variable(self, name: str):
        name = name.strip()
        if not name.startswith("--"):
            name = "--" + name
        entries = [v for v in self.rep["variables"] if v["name"] == name]
        if not entries:
            names = sorted({v["name"] for v in self.rep["variables"]})
            return _missing(f"{name} is not defined in {self.rep['source']}. Defined variables: "
                            + (", ".join(names) or "none"), {"available": names})
        return _ok({"name": name, "entries": [
            {"value": v["value"], "resolved": v["resolved"], "scope": v["scope"],
             "context": v["context"], "status": v["status"], "line": v["line"]} for v in entries]},
            [v["line"] for v in entries])

    def t_list_colors(self, top_n: int = 0):
        cols = self.rep["colors"]
        if not cols:
            return _missing(f"No colors found in {self.rep['source']}.")
        used = [c for c in cols if c["count"] > 0]
        shown = used[:top_n] if top_n and top_n > 0 else used
        return _ok({"distinct_colors": len(used), "colors": shown,
                    "near_duplicates": self.rep["palette_drift"]},
                   [c["first_line"] for c in shown])

    def t_list_fonts(self):
        t = self.rep["typography"]
        if not t["font_families"] and not t["font_sizes"]:
            return _missing("No font-family or font-size declarations found.")
        return _ok(t)

    def t_list_breakpoints(self):
        bps = self.rep["breakpoints"]
        if not bps:
            return _missing("No @media queries found.")
        return _ok({"breakpoints": bps})

    def t_find_overrides(self, property: str = ""):
        ov = self.rep["overrides"]
        if property:
            ov = [o for o in ov if o["property"] == property.lower()]
        if not ov:
            return _missing("No overridden (dead) declarations found" + (f" for '{property}'." if property else "."))
        lines = [o["winner"]["line"] for o in ov] + [d["line"] for o in ov for d in o["dead"]]
        return _ok({"overrides": ov}, lines)

    def t_get_rule(self, selector: str):
        want = _norm_sel(selector)
        hits = []
        for rule in self.a.sheet.rules:
            if want in [_norm_sel(s) for s in _split_top(rule.selector, ",")] or _norm_sel(rule.selector) == want:
                env = {**self.a.root_env, **{x.prop: x.value for x in rule.declarations if x.prop.startswith("--")}}
                hits.append({"selector": _norm_sel(rule.selector), "context": list(rule.context),
                             "line": rule.line, "specificity": list(specificity(want)),
                             "declarations": [{"property": d.prop, "value": d.value,
                                               "resolved": resolve_value(d.value, env)[0],
                                               "important": d.important, "line": d.line}
                                              for d in rule.declarations]})
        if not hits:
            avail = sorted({_norm_sel(s) for r in self.a.sheet.rules if not r.selector.startswith("@")
                            for s in _split_top(r.selector, ",")})
            return _missing(f"No rule with selector '{want}' in {self.rep['source']}. "
                            f"Selectors present: {', '.join(avail[:15])}", {"available": avail})
        return _ok({"rules": hits}, [h["line"] for h in hits])

    def t_contrast_check(self, foreground: str = "", background: str = ""):
        if not foreground and not background:
            pairs = self.rep["contrast"]
            if not pairs:
                return _missing("No rule has both a text color and a background color.")
            return _ok({"pairs": pairs, "wcag_aa_normal_min": AA_NORMAL, "wcag_aa_large_min": AA_LARGE},
                       [p["line"] for p in pairs])

        def to_hex(token):
            t = token.strip()
            if t.startswith("--"):
                resolved = resolve_value(f"var({t})", self.a.root_env)[0]
                if resolved.startswith("var("):
                    return None, f"{t} is not defined or cannot be resolved to a color"
                t = resolved
            c = parse_color(t)
            if not c:
                return None, f"'{token}' is not a color I can parse"
            if c[1] < 1:
                return None, f"'{token}' is semi-transparent; contrast needs an opaque color"
            return c[0], ""

        fg, e1 = to_hex(foreground) if foreground else (None, "foreground missing")
        bg, e2 = to_hex(background) if background else (None, "background missing")
        if not fg or not bg:
            return _missing("; ".join(e for e in (e1, e2) if e))
        ratio = round(contrast_ratio(fg, bg), 2)
        return _ok({"foreground": fg, "background": bg, "ratio": ratio,
                    "passes_aa_normal": ratio >= AA_NORMAL, "passes_aa_large": ratio >= AA_LARGE,
                    "wcag_aa_normal_min": AA_NORMAL, "wcag_aa_large_min": AA_LARGE})

    def t_file_stats(self):
        return _ok({**self.rep["stats"], "source": self.rep["source"],
                    "media_queries": len(self.rep["breakpoints"]),
                    "most_specific": self.rep["specificity_top"][:3]})


TOOL_SCHEMAS = {
    "get_variable": {"description": "Look up a CSS custom property (e.g. --brand): raw value, fully resolved value, scope, line.",
                     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    "list_colors": {"description": "All colors used in the stylesheet (normalised to hex) with usage counts and near-duplicates.",
                    "input_schema": {"type": "object", "properties": {"top_n": {"type": "integer"}}}},
    "list_fonts": {"description": "Font families and font sizes used.",
                   "input_schema": {"type": "object", "properties": {}}},
    "list_breakpoints": {"description": "@media queries and their pixel widths.",
                         "input_schema": {"type": "object", "properties": {}}},
    "find_overrides": {"description": "Declarations made dead by a later declaration; optional CSS property filter.",
                       "input_schema": {"type": "object", "properties": {"property": {"type": "string"}}}},
    "get_rule": {"description": "Declarations of a rule by selector (e.g. .btn), with specificity.",
                 "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]}},
    "contrast_check": {"description": "WCAG contrast ratio of two colors (hex/rgb/var name). With no arguments: every color/background pair in the file.",
                       "input_schema": {"type": "object", "properties": {"foreground": {"type": "string"}, "background": {"type": "string"}}}},
    "file_stats": {"description": "Counts: rules, declarations, variables, !important, warnings.",
                   "input_schema": {"type": "object", "properties": {}}},
}
