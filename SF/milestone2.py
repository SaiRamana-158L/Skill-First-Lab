#!/usr/bin/env python3
"""
Milestone 2 CLI

  python milestone2.py sample/site.css "What is --brand?"
  python milestone2.py sample/site.css --demo          # scripted demo (good for the video)
  python milestone2.py sample/site.css --repl          # interactive
  python milestone2.py sample/site.css "..." --check   # ask 3x from scratch, compare fingerprints
  python milestone2.py sample/site.css "..." --llm     # optional: Claude chooses tools (needs ANTHROPIC_API_KEY)
"""
import argparse, sys
from agent import Agent

DEMO = [
    "What is --brand?",
    "What does --brand-hover resolve to and is it the same as --brand?",
    "Which colors are used the most?",
    "What is the contrast between --muted and white?",
    "Is there any contrast problem in this stylesheet?",
    "Which breakpoints does the site use?",
    "Are any declarations overridden?",
    "What does the .btn.muted rule set?",
    "What is --brand-dark?",              # not defined  -> must NOT invent a value
    "What is the capital of France?",     # out of scope -> must abstain
    "What does --accent resolve to?",     # circular     -> must say it is unresolvable
]


def show(agent, q, check=False):
    a = agent.ask(q)
    print(f"\nQ: {q}")
    print(f"A: {a.text}")
    if a.abstained:
        print("   [abstained - no supporting data]")
    if a.note:
        print(f"   [{a.note}]")
    for e in a.evidence_lines():
        print(f"   evidence: {e}")
    print(f"   fingerprint: {a.fingerprint}")
    if check:
        c = agent.consistency_check(q, 3)
        print(f"   repeat x3 from scratch: {'IDENTICAL' if c['consistent'] else 'DIFFERENT'} {c['fingerprints']}")
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("css_file")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--repl", action="store_true")
    ap.add_argument("--check", action="store_true", help="repeat the question 3x and compare")
    ap.add_argument("--llm", action="store_true", help="use Claude (optional, needs ANTHROPIC_API_KEY)")
    a = ap.parse_args()
    try:
        css = open(a.css_file, encoding="utf-8-sig", errors="replace").read()
    except OSError as e:
        sys.exit(f"cannot read {a.css_file}: {e}")
    llm = None
    if a.llm:
        from agent.llm import AnthropicBackend, LLMError
        try:
            llm = AnthropicBackend()
        except LLMError as e:
            print(f"[--llm ignored: {e}]")
    agent = Agent(css, a.css_file, llm)
    if a.demo:
        for q in DEMO:
            show(agent, q, check=True)
    elif a.repl:
        print("Ask about the CSS file (empty line to quit).")
        while True:
            try:
                q = input("\n> ").strip()
            except EOFError:
                break
            if not q:
                break
            show(agent, q, a.check)
    elif a.question:
        show(agent, a.question, a.check)
    else:
        ap.error("give a question, --demo or --repl")


if __name__ == "__main__":
    main()
