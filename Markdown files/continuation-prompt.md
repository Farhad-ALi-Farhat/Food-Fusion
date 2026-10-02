# Food Fusion — Continuation Prompt

Paste this at the start of a new session, along with the five companion files
(`project-spec.md`, `food-fusion-menu.md`, `db-schema.md`, `agent-prompts.md`,
`implementation-log.md`), to pick up exactly where this session left off.

---

I'm continuing work on Food Fusion, a WhatsApp order-placement assistant for a restaurant in
Lahore. Full design and implementation context is in the five attached files — please read all
five before responding. `implementation-log.md` is the most important one to read carefully: it
documents everything built, tested, and fixed in the last working session, including a full model
resilience saga that ended in a specific, deliberate configuration. Don't re-litigate or
re-diagnose anything documented there as already resolved.

**Current live configuration, load-bearing — don't change without a reason:**
- Primary LLM: Groq, `openai/gpt-oss-20b`
- Fallback LLM: Gemini, `gemini-3.5-flash-lite` (not full `flash` — free-tier RPM too tight)
- Fallback wired via `ModelFallbackMiddleware` in `app/agents/resilience.py`
- WhatsApp token: permanent Business System User token (not the short-lived temporary one)
- Webhook signature verification is live (`WHATSAPP_APP_SECRET`, HMAC-SHA256)

**What's implemented and live-tested:**
- Full webhook → agent → DB → WhatsApp loop, including background-task processing and
  duplicate-message dedupe
- Customer ordering flow: search, ambiguity handling, cart operations, `multiple_of` bundle
  pricing, order submission
- Admin flow: mark item unavailable/available, add daily special, `confirm_order` (added ahead of
  original roadmap — notifies the customer automatically)
- Fuzzy menu search fallback for spelling variants
- Agent-failure fallback reply (customer never gets silence on an LLM/tool error)

**Still open — pick up here:**
1. Full soak test: one continuous live run of submit_order → admin confirm_order → customer
   receives the confirmation notification, rather than testing the pieces separately.
2. Decide whether the duplicate-webhook-delivery race window needs hardening beyond the current
   DB unique-constraint safety net (no duplicates observed in testing, but the window isn't fully
   closed).
3. Everything else listed under "Open items" in `implementation-log.md` §10.

**Working preferences, unchanged:**
- Code changes delivered as per-file diffs or full-file content in chat — not zipped, since files
  are edited directly on my machine.
- Don't re-explain or re-litigate settled architectural decisions (order state machine, "LLM
  interprets, application decides", deals as flat-priced items, admin/customer sharing one
  WhatsApp number) unless I explicitly ask to revisit something.
