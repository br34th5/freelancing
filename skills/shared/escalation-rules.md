---
name: escalation-rules
description: When to stop working and escalate — the 3-fail rule and out-of-scope triggers. Read this whenever a fix is stuck or a task touches core infrastructure.
owner: shared
verified: false
---

# Escalation rules

## When to STOP immediately
1. A fix attempt has failed **3 times** with no new information between attempts.
2. The task touches **engine core, rendering, or novel architecture** (not covered by existing skills).
3. You are about to escalate past rotate-UA on the anti-bot ladder without ToS
   clearance (see /skills/scraping/recon-planner/anti-bot-escalation.md, step 5).

## What to do when STOP fires
1. Do not try a fourth variation of the same idea.
2. Append to the owning agent's `known-pitfalls.md`:
   - date, target, each failed attempt (ordered),
   - exact failure signal (error text, status code, screenshot path),
   - current hypothesis and why it failed or stayed untested.
3. Output an **escalation summary** for a frontier-model session:
   - Task goal (1-2 sentences)
   - What was tried (ordered list)
   - Current state (files touched, services running, last error)
   - Hypothesis / open question
   - Explicit ask (what decision or insight is needed)
4. Park the task. Do not resume until the escalation is answered.

## Anti-patterns (never do these)
- Re-running the same failing command "just in case".
- Broadly rewriting working code to route around one unknown.
- Installing random packages / switching frameworks as a first response.
- Asking the user to debug without first producing an escalation summary.

## After escalation is answered
- Record the resolution in known-pitfalls.md (dated).
- Only mark related skill entries verified if behavior was reproduced
  (AGENTS.md verification rule).
