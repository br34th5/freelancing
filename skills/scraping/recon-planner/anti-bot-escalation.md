---
name: anti-bot-escalation
description: When a target site blocks requests, apply the escalation ladder in order — classify the 403, slow down, rotate UA, proxy, then stop. Use on 403/429/captcha or sudden request failures while scraping.
owner: recon-planner
verified: false
---
still testing

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

### 4. Proxy (my infra)
**Providers:**
- WEB = Webshare.io — small pool (a few IPs), my default egress
- OXY = Oxylabs residential — 1 rotating residential IP, LIMITED FREE
  QUOTA. Treat as a scarce resource, not a default.

**Routing rules (in order):**
1. Default: direct connection. Only move to proxy after the direct
   cool-off test fails twice (see escalation ladder step 2).
2. Direct fails → WEB pool, sticky session: one proxy IP per logical
   browse session; keep the same IP for the whole site run.
3. One proxy IP per concurrent session. Never share one WEB IP across
   two rate-limited hosts at once.
4. WEB also failing (blocks at multiple IPs) → OXY, and BEFORE using it:
   check remaining quota. If run would consume >20% of remaining quota,
   STOP and ask me. If quota < 10% remaining, never start an OXY run —
   ask me instead.
5. Never rotate OXY mid-run unless a block is hit; residential rotation
   wastes quota.

**Cool-off validation:** after any egress change, re-run the step-2
cool-off test through the new egress before resuming the scrape.

**Per-site convention:** record the egress class used in
site-quirks-<domain>.md (direct / WEB / OXY) so the next session
starts at the tier that worked, instead of re-climbing the ladder.

**Quota logging:** every OXY run appends date + approx. usage to
knowledge/proxy/oxylabs-quota.md so the agent can check balance
history, not guess.

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
