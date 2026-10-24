# Written explanations (as required by the task)

## Milestone 1 - CSS Parser
I extract design tokens, not text: variables with every `var()` chain resolved, colours normalised to hex and counted by *real* usage (`color: var(--brand)` counts as #1a73e8), fonts and sizes, @media breakpoints, declarations killed by a later override, text/background contrast, and near-duplicate colours. These are the questions a developer actually asks about a stylesheet ("what is our palette, is it consistent, is it accessible?"), and resolving variables first keeps the counts honest.
Edge case: `}`, `;` and `/*` can appear *inside strings and `url(data:...;base64,...)`*, which breaks the usual split-on-`;`/`}` approach. My scanner is string-, paren- and comment-aware (and `fill: url(#abc)` is not mistaken for the colour #abc); circular variables and unclosed blocks produce warnings instead of crashes.

## Milestone 2 - Agent
It never makes up an answer: the tools are the only source of facts, and if no tool applies or every tool returns `not_found`, the agent abstains ("I don't have that data") and lists what does exist. A GroundingGuard also checks that every hex colour, number and `--variable` in the final answer appears in the tool output; otherwise the answer is rejected, and an LLM answer with no tool call at all is discarded.
Same answer twice: tools are pure functions, the offline planner/composer have no randomness (LLM mode uses temperature 0), and every answer carries a fingerprint of file + tool trace + text. `--check` rebuilds everything from the raw file and asks N times, comparing fingerprints; the test-suite does this for answerable, missing-data and out-of-scope questions.
