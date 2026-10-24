"""
OPTIONAL LLM backend (Anthropic Messages API with tool use, stdlib only).

The agent works fully offline without this. When ANTHROPIC_API_KEY is set and
`--llm` is passed, the model chooses tools itself; its final text is still
checked by the GroundingGuard, and discarded if it contains unsupported facts.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .tools import TOOL_SCHEMAS

SYSTEM = (
    "You answer questions about ONE CSS file, using ONLY the provided tools. "
    "Never answer from general knowledge. If a tool returns not_found or no tool applies, "
    "say you do not have that data. Quote hex values, numbers and variable names exactly as "
    "returned by tools; never invent or round them. Be concise."
)


class LLMError(RuntimeError):
    pass


class AnthropicBackend:
    def __init__(self, model: str | None = None, api_key: str | None = None, max_turns: int = 5):
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_turns = max_turns
        if not self.key:
            raise LLMError("ANTHROPIC_API_KEY is not set")

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "x-api-key": self.key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise LLMError(str(e)) from e

    def run(self, question: str, toolbox):
        tools = [{"name": n, **s} for n, s in sorted(TOOL_SCHEMAS.items())]
        messages = [{"role": "user", "content": question}]
        trace = []
        for _ in range(self.max_turns):
            resp = self._post({"model": self.model, "max_tokens": 800, "temperature": 0,
                               "system": SYSTEM, "tools": tools, "messages": messages})
            blocks = resp.get("content", [])
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            if not calls:
                return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip(), trace
            messages.append({"role": "assistant", "content": blocks})
            results = []
            for c in calls:
                res = toolbox.call(c["name"], c.get("input") or {})
                trace.append({"tool": c["name"], "args": c.get("input") or {}, "result": res})
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(res)})
            messages.append({"role": "user", "content": results})
        raise LLMError("tool loop did not finish")
