"""
Milestone 2 - "Evidence-First" CSS agent.

Pipeline:   question -> planner -> tools -> composer -> GroundingGuard -> Answer

Design rules that answer the two assignment questions:

1. NEVER MAKE UP AN ANSWER
   * Tools are the only source of facts. If no tool applies, or every tool says
     `not_found`, the agent ABSTAINS ("I don't have that data") and lists what it can answer.
   * GroundingGuard: every hex colour, --variable name and number in the final
     answer must also appear in the tool outputs. Otherwise the answer is rejected
     and replaced by the deterministic evidence-only answer.
   * An LLM answer with ZERO tool calls is discarded outright.

2. SAME ANSWER TWICE
   * Tools are pure functions; the offline planner/composer have no randomness;
     LLM calls use temperature 0.
   * Every answer carries a fingerprint = sha256(file + tool trace + text).
   * `consistency_check()` re-parses the file and re-asks N times from scratch and
     compares fingerprints (CLI: --check).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from cssparse import analyze
from cssparse.parser import NAMED
from .tools import ToolBox

# ---------------------------------------------------------------- planner ---
_COLOR_TOKEN = r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)"


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        k = json.dumps(x, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


class RuleBasedPlanner:
    """Deterministic keyword/regex router: question -> [(tool, args), ...]."""

    def plan(self, q: str):
        ql = q.lower()
        calls = []
        vars_ = re.findall(r"--[\w-]+", q)
        colors = re.findall(_COLOR_TOKEN, q)
        contrast = re.search(r"contrast|readab|legib|accessib|wcag", ql)
        selectors = [s for s in re.findall(r"(?<![\w-])((?:[.#][A-Za-z_][\w-]*)+|:root)", q) if s not in colors]
        m = re.findall(r"\b(?:rule|selector)s? (?:for|of) ([\w.#:-]+)|\bthe ([\w.#:-]+) (?:rule|selector)", q, re.I)
        selectors += [a or b for a, b in m if (a or b).lower() not in {"css", "this", "that", "the"}]
        selectors = list(dict.fromkeys(selectors))

        if contrast:
            toks = [t for _, t in sorted(
                [(m.start(), m.group()) for m in re.finditer(_COLOR_TOKEN + r"|--[\w-]+", q)]
                + [(m.start(), m.group()) for m in re.finditer(r"(?<![\w#-])(?:" + "|".join(NAMED) + r")(?![\w-])", q, re.I)])]
            if len(toks) >= 2:
                calls.append(("contrast_check", {"foreground": toks[0], "background": toks[1]}))
            else:
                calls.append(("contrast_check", {}))
        else:
            for v in vars_:
                calls.append(("get_variable", {"name": v}))
        for s in selectors:
            calls.append(("get_rule", {"selector": s}))
        if not selectors and not vars_:
            if re.search(r"\b(colou?rs?|palette|hex|brand)\b", ql) and not contrast:
                calls.append(("list_colors", {}))
            if re.search(r"font|typeface|typograph", ql):
                calls.append(("list_fonts", {}))
        if re.search(r"breakpoint|media quer|responsive|screen width", ql):
            calls.append(("list_breakpoints", {}))
        if re.search(r"overrid|duplicate|conflict|redefin|dead|unused declaration", ql):
            calls.append(("find_overrides", {}))
        if re.search(r"how many|stats|statistic|overview|summary|how big|size of", ql):
            calls.append(("file_stats", {}))
        return _uniq(calls)[:4]


# --------------------------------------------------------------- composer ---
def _fmt_entries(d):
    out = []
    for e in d["entries"]:
        ctx = f" inside {e['context'][0]}" if e["context"] else ""
        s = f"`{d['name']}` = `{e['value']}` (line {e['line']}, scope {e['scope']}{ctx})"
        if e["status"] == "circular":
            s += " - it is a CIRCULAR reference and cannot be resolved"
        elif e["resolved"] != e["value"]:
            s += f", which resolves to `{e['resolved']}`"
        out.append(s)
    return out


def compose_one(tool: str, res: dict) -> str:
    """Evidence-only sentence(s) for one tool result."""
    if res["status"] != "found":
        return f"I don't have that data: {res['message']}"
    d = res["data"]
    if tool == "get_variable":
        return " ".join(_fmt_entries(d)[:3]) + "."
    if tool == "list_colors":
        top = d["colors"][:5]
        s = f"{d['distinct_colors']} distinct colors are used. Top by usage: " + ", ".join(
            f"{c['hex']} (x{c['count']}" + (f", via {', '.join(c['via_variables'])}" if c["via_variables"] else "") + ")"
            for c in top) + "."
        if d["near_duplicates"]:
            s += " Near-duplicate pairs (possible palette drift): " + ", ".join(
                f"{p['a']}/{p['b']}" for p in d["near_duplicates"]) + "."
        return s
    if tool == "list_fonts":
        fam = ", ".join(f"{f['family']} (x{f['count']})" for f in d["font_families"]) or "none declared"
        sz = ", ".join(z["value"] + (f" (~{z['px']:g}px)" if z["px"] and not z["value"].endswith("px") else "")
                       for z in d["font_sizes"]) or "none declared"
        return f"Font families: {fam}. Font sizes: {sz}."
    if tool == "list_breakpoints":
        return "Media queries: " + "; ".join(f"{b['query']} ({b['rules']} rules)" for b in d["breakpoints"]) + "."
    if tool == "find_overrides":
        return " ".join(
            f"{o['selector']} sets {o['property']} more than once; `{o['winner']['value']}` (line {o['winner']['line']}) wins, "
            f"dead: " + ", ".join(f"`{x['value']}` (line {x['line']})" for x in o["dead"]) + "."
            for o in d["overrides"])
    if tool == "get_rule":
        parts = []
        for r in d["rules"]:
            decls = "; ".join(f"{x['property']}: {x['resolved']}" + (" !important" if x["important"] else "")
                              for x in r["declarations"])
            parts.append(f"{r['selector']} (line {r['line']}, specificity {tuple(r['specificity'])}) sets {decls}.")
        return " ".join(parts)
    if tool == "contrast_check":
        if "pairs" in d:
            return "Contrast per rule: " + "; ".join(
                f"{p['selector']} {p['foreground']} on {p['background']} = {p['ratio']}:1 "
                f"({'passes' if p['passes_aa_normal'] else 'FAILS'} AA, min {d['wcag_aa_normal_min']})"
                for p in d["pairs"]) + "."
        return (f"{d['foreground']} on {d['background']} has a contrast ratio of {d['ratio']}:1 - "
                f"{'passes' if d['passes_aa_normal'] else 'fails'} WCAG AA for normal text (min {d['wcag_aa_normal_min']}) and "
                f"{'passes' if d['passes_aa_large'] else 'fails'} for large text (min {d['wcag_aa_large_min']}).")
    if tool == "file_stats":
        ms = ", ".join(f"{m['selector']} {tuple(m['specificity'])}" for m in d["most_specific"])
        return (f"{d['source']} has {d['rules']} rules, {d['declarations']} declarations, {d['variables']} variables, "
                f"{d['important_declarations']} !important, {d['media_queries']} media queries and {d['warnings']} warnings. "
                f"Most specific selectors: {ms}.")
    return json.dumps(d)


def compose(trace) -> str:
    found = [t for t in trace if t["result"]["status"] == "found"]
    missing = [t for t in trace if t["result"]["status"] != "found"]
    parts = [compose_one(t["tool"], t["result"]) for t in found]
    parts += [compose_one(t["tool"], t["result"]) for t in missing]
    return "\n".join(parts)


# ------------------------------------------------------------------ guard ---
_FACT_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|--[\w-]+|\d+(?:\.\d+)?")


def _facts(text: str) -> set:
    return {f.lower() for f in _FACT_RE.findall(text)}


class GroundingGuard:
    """Every hex/number/--variable in an answer must be present in tool evidence."""

    @staticmethod
    def unsupported(answer: str, trace) -> list:
        evidence = json.dumps([t["result"]["data"] for t in trace] + [t["args"] for t in trace]
                              + [t["result"]["message"] for t in trace], sort_keys=True)
        ev = _facts(evidence)
        return sorted(f for f in _facts(answer) if f not in ev)


# ------------------------------------------------------------------ agent ---
SCOPE_HELP = ("I can only answer questions about the CSS file I was given: its variables (--x), colors, fonts, "
              "breakpoints, overridden declarations, contrast, individual rules (e.g. .btn) and overall stats.")


@dataclass
class Answer:
    question: str
    text: str
    trace: list = field(default_factory=list)
    abstained: bool = False
    guard_rejected: list = field(default_factory=list)
    fingerprint: str = ""
    note: str = ""

    def evidence_lines(self):
        out = []
        for t in self.trace:
            args = ", ".join(f"{k}={v!r}" for k, v in t["args"].items())
            lines = f" lines {t['result']['lines']}" if t["result"]["lines"] else ""
            out.append(f"{t['tool']}({args}) -> {t['result']['status']}{lines}")
        return out


class Agent:
    def __init__(self, css: str, source: str = "<css>", llm=None):
        self.css, self.source, self.llm = css, source, llm
        self.analysis = analyze(css, source)
        self.toolbox = ToolBox(self.analysis)
        self.planner = RuleBasedPlanner()
        self.css_hash = hashlib.sha256(css.encode()).hexdigest()[:12]

    def _run_offline(self, question):
        return [{"tool": n, "args": a, "result": self.toolbox.call(n, a)} for n, a in self.planner.plan(question)]

    def ask(self, question: str) -> Answer:
        note, llm_text, trace = "", None, []
        if self.llm:
            try:
                llm_text, trace = self.llm.run(question, self.toolbox)
            except Exception as e:  # network / key problems -> fall back, stay offline-safe
                note = f"LLM unavailable ({e}); used offline planner."
                trace = self._run_offline(question)
        else:
            trace = self._run_offline(question)

        rejected = []
        if not trace:
            ans = Answer(question, f"I don't have data to answer that, so I won't guess. {SCOPE_HELP}",
                         abstained=True, note=note or ("discarded unsupported LLM text" if llm_text else ""))
        elif all(t["result"]["status"] != "found" for t in trace):
            ans = Answer(question, compose(trace), trace, abstained=True, note=note)
        else:
            text = compose(trace)
            if llm_text:
                rejected = GroundingGuard.unsupported(llm_text, trace)
                if rejected:
                    note = f"LLM answer rejected by GroundingGuard (unsupported: {', '.join(rejected)}); evidence-only answer used."
                else:
                    text = llm_text
            ans = Answer(question, text, trace, guard_rejected=rejected, note=note)
        payload = json.dumps({"css": self.css_hash, "trace": ans.trace, "text": ans.text}, sort_keys=True)
        ans.fingerprint = hashlib.sha256(payload.encode()).hexdigest()[:12]
        return ans

    def consistency_check(self, question: str, runs: int = 3) -> dict:
        """Rebuild everything from the raw file each run, then compare fingerprints."""
        prints = []
        for _ in range(runs):
            fresh = Agent(self.css, self.source, self.llm)
            prints.append(fresh.ask(question).fingerprint)
        return {"question": question, "runs": runs, "fingerprints": prints, "consistent": len(set(prints)) == 1}
