import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cssparse import analyze, parse_color, contrast_ratio
from cssparse.parser import specificity, parse


class ParserTests(unittest.TestCase):
    def test_delimiters_inside_strings_and_url_do_not_break_parse(self):
        css = '.a{content:"} ; /* x */";background:url(data:image/png;base64,AA;BB) red}.b{color:blue}'
        sheet = parse(css)
        self.assertEqual([r.selector for r in sheet.rules], [".a", ".b"])
        self.assertEqual(len(sheet.rules[0].declarations), 2)

    def test_comments_removed_and_line_numbers_preserved(self):
        rep = analyze("/* a\nb\nc */\n.x { color: red }").report
        self.assertEqual(rep["colors"][0]["first_line"], 4)

    def test_color_normalisation(self):
        self.assertEqual(parse_color("#FFF"), ("#ffffff", 1.0))
        self.assertEqual(parse_color("rgb(26, 115, 232)"), ("#1a73e8", 1.0))
        self.assertEqual(parse_color("rgba(0 0 0 / 50%)"), ("#000000", 0.5))
        self.assertEqual(parse_color("hsl(0, 100%, 50%)"), ("#ff0000", 1.0))
        self.assertIsNone(parse_color("var(--x)"))

    def test_url_fragment_is_not_a_color(self):
        rep = analyze(".a{fill:url(#abc)}").report
        self.assertEqual(rep["colors"], [])

    def test_var_resolution_chain_fallback_and_usage_attribution(self):
        css = ":root{--a:#111111;--b:var(--a)} .x{color:var(--b);background:var(--nope,#222222)}"
        rep = analyze(css).report
        c = {x["hex"]: x for x in rep["colors"]}
        self.assertEqual(c["#111111"]["via_variables"], ["--a", "--b"])
        self.assertIn("#222222", c)

    def test_circular_and_undefined_are_reported_not_crashing(self):
        rep = analyze(":root{--a:var(--b);--b:var(--a)} .x{color:var(--zzz)}").report
        self.assertTrue(all(v["status"] == "circular" for v in rep["variables"]))
        self.assertTrue(any("undefined variable --zzz" in w for w in rep["warnings"]))

    def test_media_queries_and_breakpoints(self):
        rep = analyze("@media (min-width: 40rem) and (max-width: 900px){.a{color:red}}").report
        px = [c["px"] for c in rep["breakpoints"][0]["conditions"]]
        self.assertEqual(px, [640.0, 900.0])
        self.assertEqual(rep["breakpoints"][0]["rules"], 1)

    def test_overrides_respect_important(self):
        rep = analyze(".a{color:red !important}.a{color:blue}").report
        self.assertEqual(rep["overrides"][0]["winner"]["value"], "red")

    def test_conditional_root_vars_do_not_replace_unconditional(self):
        css = ":root{--c:#000000}@media (prefers-color-scheme:dark){:root{--c:#ffffff}} .a{color:var(--c)}"
        rep = analyze(css).report
        self.assertEqual(rep["colors"][0]["hex"], "#000000")

    def test_contrast_math(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=1)

    def test_specificity(self):
        self.assertEqual(specificity("nav#main .item.active > a:not(.disabled)"), (1, 3, 2))
        self.assertEqual(specificity("*"), (0, 0, 0))

    def test_malformed_input_never_crashes(self):
        for css in ["", "}}}", ".a{", ".a{color:red", "/* unterminated", '.a{content:"oops}', "@media{", ".a{;;;}", "a{b:c;;d}"]:
            analyze(css)  # must not raise

    def test_unclosed_block_warns_but_keeps_data(self):
        rep = analyze(".a{color:red").report
        self.assertEqual(rep["colors"][0]["hex"], "#ff0000")
        self.assertTrue(any("never closed" in w for w in rep["warnings"]))


if __name__ == "__main__":
    unittest.main()
