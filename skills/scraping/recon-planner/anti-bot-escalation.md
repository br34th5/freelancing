---
name: anti-bot-escalation
description: When a target site blocks requests, apply the escalation ladder in order — classify the 403, slow down, rotate UA, proxy, then stop. Use on 403/429/captcha or sudden request failures while scraping.
owner: recon-planner
verified: false
---

# Anti-bot escalation ladder

Apply steps in order. Never skip ahead. Never run a step more than twice without
progress. Log every step with timestamps so a later session can resume.

## Ladder

### 1. 403 / block received — classify, do not hammer
- Log the full response: status, headers (Server, Via, CF-Ray, x-*), body snippet,
  and request fingerprint (UA, IP, TLS profile if known).
- Distinguish:
  - **Rate limit (429 / Retry-After)**: respect Retry-After, back off → step 2.
  - **Hard block (403 / captcha interstitial / empty JS shell)**: stop the current
    request pattern immediately. Do not resend the same request.
  - **Auth wall**: not a bot block — check the session/login flow instead.
- Record the block signature in the site's knowledge file
  (see site-quirks-template.md).

### 2. Slow down
- Add per-request delay: start 2-5s with jitter; never fixed intervals.
- Drop concurrency to 1 for that host.
- Remove bursty patterns (parallel detail-page hits).
- Cool off at least 60s, then re-test with a single manual request.

### 3. Rotate User-Agent (and headers)
- Rotate through a pool of realistic desktop UAs; one UA per logical session, not
  per request.
- Match the rest of the header fingerprint to the UA (Accept-Language, Sec-CH-UA,
  Accept order). No contradictory fingerprints (Chrome UA + Firefox header order).
- Still blocked → the site fingerprints beyond UA (TLS/JA3, JS challenge). Do not
  rotate faster; go to step 4.

### 4. Proxy
- Move to a residential/mobile proxy pool; sticky session per logical browse session.
- One proxy IP per concurrent session; never share across rate-limited hosts.
- Re-run the step-2 cool-off test through the new egress.
- TODO: document my proxy provider, pool size, and sticky-session conventions here.

### 5. STOP
- Halt all automated traffic to that host.
- No captcha-solving services, account farming, or stealth-browser patches without
  explicit human approval.
- Escalate: append everything tried to this agent's known-pitfalls.md, then output
  an escalation summary per /skills/shared/escalation-rules.md.
- Write a dated UNVERIFIED note in /knowledge/raw-notes/ with the block signature
  and the ladder position reached.

## Hard rules
- Never pass step 3 on a host whose ToS is unverified (legal-tos-check.md).
- Never rotate accounts/credentials to bypass blocks.
- Every ladder step gets a timestamped log line — an unlogged step is a wasted step.
