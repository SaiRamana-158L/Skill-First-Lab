# Skill First Labs - Shortlisting Tasks

Pure Python (3.9+), **no external dependencies**. Everything runs offline.

| Milestone | Idea | Entry point |
|---|---|---|
| 1 - Parser | **CSS Design-Token Extractor**: resolves `var()`, counts colours by real usage, finds dead overrides, contrast failures, palette drift | `python milestone1.py sample/site.css` |
| 2 - Agent  | **Evidence-First CSS Agent**: answers only through tools (built on Milestone 1), abstains when it has no data, rejects ungrounded answers, proves repeatability | `python milestone2.py sample/site.css --demo` |

The written 3-4 line explanations required by the task are in [`NOTES.md`](NOTES.md) (also in the PDF report).

## Run
```bash
python milestone1.py sample/site.css            # readable report
python milestone1.py sample/site.css --json     # full JSON
python milestone2.py sample/site.css "What is --brand?"
python milestone2.py sample/site.css "What is --brand?" --check   # asks 3x from scratch, compares
python milestone2.py sample/site.css --demo     # scripted demo (use this in the video)
python milestone2.py sample/site.css --repl     # interactive
python -m unittest discover -s tests -v         # 25 tests
```
Optional LLM mode (Claude picks the tools; the guard still validates its answer):
```bash
export ANTHROPIC_API_KEY=...        # optional; CLAUDE_MODEL overrides the model name
python milestone2.py sample/site.css "Which colors are used most?" --llm
```
Without a key, `--llm` is ignored and the offline planner is used.

## Layout
```
cssparse/parser.py   Milestone 1: scanner, parser, var() resolver, analysis
agent/tools.py       8 deterministic tools (get_variable, list_colors, list_fonts, list_breakpoints,
                     find_overrides, get_rule, contrast_check, file_stats)
agent/agent.py       planner -> tools -> composer -> GroundingGuard, fingerprints, consistency check
agent/llm.py         optional Anthropic tool-use backend (stdlib urllib)
sample/              site.css + captured outputs
tests/               25 unit tests
```

## Known limitations (honest list)
CSS nesting inside rules is skipped with a warning; `@keyframes` bodies are not analysed; specificity is an approximation
(`:not()/:is()` contents counted, `:where()` ignored); `font:` shorthand is not split; only a subset of named colours is built in;
the offline planner is keyword-based (the LLM mode handles free-form phrasing).

## Demo video script (about 2-3 minutes)
1. Show `sample/site.css` (point at the tricky `content: "}"`, the `url(data:...;...)`, the circular variables).
2. `python milestone1.py sample/site.css` - walk through variables, colours via `--brand`, near-duplicates, dead override, contrast failure.
3. `python milestone2.py sample/site.css --demo` - highlight: a normal answer with evidence, `--brand-dark` (abstains and lists real variables), "capital of France" (abstains), `--accent` (circular), and the `IDENTICAL` fingerprints.
4. `python -m unittest discover -s tests -v` - 25 passing, including the test where a fake model invents `#ff00ff` and the guard rejects it.
