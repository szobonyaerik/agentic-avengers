---
description: Scan the working diff for over-engineering against the ponytail ladder. Advisory only — no verdict artifact, no gate, nothing blocked.
allowed-tools: Bash, Read, Grep, Glob
argument-hint: "[git ref to diff against, default HEAD]"
---

Review the working diff for code that did not need to be written. Read `skills/ponytail/SKILL.md`
first — the ladder in it is the rubric.

1. Get the diff: `git diff $ARGUMENTS` (default `git diff HEAD`). If it is empty, try
   `git diff main...HEAD` and say which you used.
2. For each added hunk of **production** code, find the highest rung of the ladder that would have
   covered it: does it need to exist at all · already in this codebase · stdlib · native platform
   feature · already-installed dependency · one line.
3. Report only findings you can name a concrete replacement for. One line each:

   `path:line — <what was built> → <the rung that covers it>`

   No finding without a replacement. "This feels heavy" is not a finding.

**Out of scope, do not report:** anything under a test path, validation at trust boundaries, error
handling, security, accessibility, and anything an approved spec requires. Those are the ladder's
standing exemptions — flagging them is a bug in the review, not a finding.

This is **advisory**. Write no artifact, set no verdict, block nothing. It is not one of the
pipeline's gates and never routes a phase back — report to the user in chat and stop.

If nothing survives the rules above, say so in one line. A clean diff is the expected result.
