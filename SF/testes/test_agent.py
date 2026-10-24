import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import Agent
from agent.agent import GroundingGuard

CSS = open(os.path.join(ROOT, "sample", "site.css"), encoding="utf-8").read()


class FakeLLM:
    """Pretends to be a model that hallucinates a value after a real tool call."""
    def __init__(self, text, call=True):
        self.text, self.call = text, call

    def run(self, question, toolbox):
        trace = []
        if self.call:
            trace.append({"tool": "get_variable", "args": {"name": "--brand"},
                          "result": toolbox.call("get_variable", {"name": "--brand"})})
        return self.text, trace


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(CSS, "site.css")

    def test_answers_from_tools(self):
        a = self.agent.ask("What is --brand?")
        self.assertIn("#1a73e8", a.text)
        self.assertFalse(a.abstained)
        self.assertEqual(a.trace[0]["tool"], "get_variable")

    def test_abstains_when_data_missing(self):
        a = self.agent.ask("What is --brand-dark?")
        self.assertTrue(a.abstained)
        self.assertIn("don't have that data", a.text)
        self.assertNotRegex(a.text.split("Defined variables")[0], r"#[0-9a-f]{3,6}")

    def test_abstains_when_out_of_scope(self):
        a = self.agent.ask("What is the capital of France?")
        self.assertTrue(a.abstained)
        self.assertEqual(a.trace, [])

    def test_circular_variable_is_not_resolved(self):
        a = self.agent.ask("What does --accent resolve to?")
        self.assertIn("CIRCULAR", a.text)

    def test_named_color_contrast(self):
        a = self.agent.ask("contrast between --muted and white")
        self.assertIn("2.64", a.text)
        self.assertIn("fails", a.text)

    def test_compound_selector_is_not_split(self):
        a = self.agent.ask("What does the .btn.muted rule set?")
        self.assertEqual([t["args"]["selector"] for t in a.trace], [".btn.muted"])

    def test_same_answer_every_time(self):
        for q in ["What is --brand?", "Which colors are used the most?", "Which breakpoints are used?",
                  "Are any declarations overridden?", "What is --nope?", "Tell me a joke"]:
            r = self.agent.consistency_check(q, runs=4)
            self.assertTrue(r["consistent"], q)

    def test_guard_rejects_hallucinated_llm_text(self):
        agent = Agent(CSS, "site.css", llm=FakeLLM("--brand is #ff00ff (line 99)."))
        a = agent.ask("What is --brand?")
        self.assertTrue(a.guard_rejected)
        self.assertIn("#1a73e8", a.text)
        self.assertNotIn("#ff00ff", a.text)

    def test_guard_accepts_faithful_llm_text(self):
        agent = Agent(CSS, "site.css", llm=FakeLLM("--brand is #1a73e8, defined on line 6."))
        a = agent.ask("What is --brand?")
        self.assertEqual(a.guard_rejected, [])
        self.assertEqual(a.text, "--brand is #1a73e8, defined on line 6.")

    def test_llm_answer_without_any_tool_call_is_discarded(self):
        agent = Agent(CSS, "site.css", llm=FakeLLM("Paris.", call=False))
        a = agent.ask("What is the capital of France?")
        self.assertTrue(a.abstained)
        self.assertNotIn("Paris", a.text)

    def test_unknown_tool_and_bad_args_are_errors_not_crashes(self):
        self.assertEqual(self.agent.toolbox.call("rm_rf")["status"], "error")
        self.assertEqual(self.agent.toolbox.call("get_variable", {"wrong": 1})["status"], "error")

    def test_guard_unit(self):
        trace = [{"tool": "t", "args": {}, "result": {"status": "found", "data": {"x": "#abc123", "n": 4.5}, "message": ""}}]
        self.assertEqual(GroundingGuard.unsupported("x is #abc123 and 4.5", trace), [])
        self.assertEqual(GroundingGuard.unsupported("x is #abc124", trace), ["#abc124"])


if __name__ == "__main__":
    unittest.main()
