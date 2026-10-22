---
name: known-pitfalls
description: Rolling dated log of failed approaches for site-sentinel work — what was tried, the failure signal, and the hypothesis. Append on every escalation; read before retrying anything.
owner: site-sentinel
verified: false
---

# site-sentinel known pitfalls

Append-only. Newest entries on top. Written when the escalation rule fires
(see /skills/shared/escalation-rules.md) and after escalations are resolved.

## Entry format
```
## <YYYY-MM-DD> - <target>
- Tried: <ordered attempts>
- Failed with: <exact signal>
- Hypothesis: <what I think is happening>
- Resolution: <open | answered: what fixed it or what to avoid>
```

## Entries

- (none yet)
