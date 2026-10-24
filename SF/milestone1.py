#!/usr/bin/env python3
"""Milestone 1 CLI:  python milestone1.py sample/site.css [--json] [--out report.json]"""
import argparse, json, sys
from cssparse import analyze, format_summary


def main():
    ap = argparse.ArgumentParser(description="Extract structured design information from a CSS file.")
    ap.add_argument("css_file")
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    ap.add_argument("--out", help="also write the JSON report to this path")
    a = ap.parse_args()
    try:
        with open(a.css_file, encoding="utf-8-sig", errors="replace") as f:
            css = f.read()
    except OSError as e:
        sys.exit(f"cannot read {a.css_file}: {e}")
    rep = analyze(css, a.css_file).report
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
    print(json.dumps(rep, indent=2) if a.json else format_summary(rep))


if __name__ == "__main__":
    main()
