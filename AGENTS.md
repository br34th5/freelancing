# Agent instructions

This repo holds multi-agent infrastructure. Agents are not separate programs: one model
serves everything, and specialization = skill files selected via the catalog below.

Domain in this repo: **scraping** (agents: recon-planner, scraper-builder, site-sentinel).

## Catalog rule
At the start of every task, read /skills/_index.md. Pick the owning agent for the task,
load only that agent's skills plus /skills/shared/. Never load everything.

## Escalation rule
If a fix attempt fails 3 times, or the task touches engine core/rendering/novel
architecture: STOP. Append what you tried to the agent's known-pitfalls skill, then
output an escalation summary for a frontier-model session (what was tried, current
state, hypothesis). Never keep flailing.

Full ladder and summary format: /skills/shared/escalation-rules.md.

## Debrief rule (MANDATORY, end of every session)
Append what you learned about the tools/sites/codebase to the relevant skill or
knowledge file, with today's date. Never end a session without it.

Protocol and routing table: /skills/shared/session-debrief.md.

## Verification rule
Any claimed behavior of software must be reproduced before being written as verified
into a skill. Raw findings go to /knowledge/raw-notes/ marked UNVERIFIED.

A skill's frontmatter `verified:` flag flips to true only after its behaviors were
reproduced in isolation.

## Skill file format
Every skill file under /skills/ uses this frontmatter:

```
---
name: <kebab-case>
description: 1-3 sentences: when to use this skill (trigger phrases)
owner: <agent-name>
verified: false
---
```

`verified: false` flips to true only after behaviors were reproduced in isolation.

## Knowledge
/knowledge/ holds raw facts, exploration notes (UNVERIFIED until reproduced), and
selector maps. Per-site files are created from
/skills/scraping/recon-planner/site-quirks-template.md.
