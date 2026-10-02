# Food Fusion — Implementation Log & Findings

Chronological record of what's been built, broken, fixed, and tested since the design docs
(`project-spec.md`, `db-schema.md`, `agent-prompts.md`, `food-fusion-menu.md`) were finalized.
Read this alongside those four — this file covers *implementation reality*, not design decisions.

**Status as of this update:** the full webhook → agent → DB → WhatsApp loop is live and has been
manually tested end-to-end, including the customer ordering flow, admin menu management, and a
model-resilience fallback. Still open: a full soak test of submit → admin confirm → customer
notification, and a decision on hardening the duplicate-delivery race window.

---

## 1. Environment (as currently set up)

- **Dev machine**: Windows, VS Code, Python 3.13
- **Database**: Supabase Postgres — connection string in `.env` as `DATABASE_URL`
- **LLM — see §6, this changed significantly.** Current: Groq (`openai/gpt-oss-20b`) as primary,
  Gemini (`gemini-3.5-flash-lite`) as fallback, via a `ModelFallbackMiddleware`.
- **WhatsApp**: Meta Cloud API. Moved off the short-lived temporary access token (which expired
  unpredictably, sometimes in under an hour) to a **permanent Business System User token** —
  see §7. `WHATSAPP_APP_SECRET` added for webhook signature verification — see §5.
- **`WHATSAPP_VERIFY_TOKEN`**: unchanged from original setup.
- **Project scaffold**: user now edits files directly on their machine; changes delivered as
  diffs/full-file content in chat, not zipped.
- Menu seeded (all items from `food-fusion-menu.md`), later corrected for the Boti spelling —
  see §4.

---

## 2. LangChain 1.0 migration (fixed)

`create_agent(model=llm, tools=tools, system_prompt=...)` from `langchain.agents`, replacing the
old `AgentExecutor`/`create_tool_calling_agent` pattern. `run_customer_turn()`/`run_admin_turn()`
build a `{"messages": [...]}` list from `chat_history` + the new message, and read the reply via
`result["messages"][-1].text` (not `.content` — see §3).

## 3. `.text` vs `.content` (fixed)

Newer Gemini models return structured content blocks rather than a plain string. `.text` reliably
extracts readable text regardless of format. Applied in both `run_*_turn()` functions.

---

## 4. Bugs found and fixed during live testing

### 4.1 `multiple_of` pricing bug (fixed)

`add_to_cart`'s subtotal calculation used `quantity * item.price`, but for bundle items
(Malai Boti / Grilled Boti add-ons, `multiple_of = 4`), `item.price` is priced **per portion**,
not per piece. Ordering 4 pieces computed `4 × Rs. 450 = Rs. 1800` instead of the correct
`1 portion × Rs. 450 = Rs. 450`. Every item with `multiple_of = 1` was unaffected, which is why
this stayed invisible until the bundle items were specifically tested.

**Fix applied** in `order_tools.py`, both `add_to_cart` and `update_quantity`:
subtotal is now `(quantity // item.multiple_of) * unit_price`. `update_quantity` also gained a
`multiple_of` validation check it was previously missing entirely.

### 4.2 `search_menu` — exact substring search missed common spelling variants (fixed)

Customer queries like "malai boti" (single t) failed to match the DB's "Malai Botti" (double t)
because substring matching requires an exact character match. This produced **inconsistent,
confidently-wrong answers** ("we don't have Malai Boti") rather than a "no results" state, since
the LLM treated an empty tool result as ground truth. The same typo pattern on "grilled boti"
happened to succeed in one run because the model auto-corrected the spelling itself before
calling the tool — not something to rely on.

**Fix applied**: `search_menu` now falls back to fuzzy matching (`difflib`, word-window scoring,
threshold 0.72) when the exact substring search returns nothing. Also tightened the tool's
docstring so the model passes only the item name, not quantities/filler words, into the query.

### 4.3 Botti → Boti rename (done)

Renamed across `menu_items` rows (DB `UPDATE ... REPLACE`), `scripts/seed_menu.py`, and both
`food-fusion-menu.md` and `db-schema.md`, since no customer was expected to type the double-t
spelling. Substring search still catches "boti"; the new fuzzy fallback catches "botti" typos.

### 4.4 Model stating a computed total instead of the tool's returned total (fixed)

After a cart was cleared in the DB directly (for testing), the *chat history* still contained the
agent's own earlier "your total is Rs. 3350" message. On the next cart-modifying tool call, the
model added the new item's price to that **remembered** number instead of using the tool's
actual returned total — producing a total that didn't match the DB at all.

**Fix applied**: strengthened `CUSTOMER_SYSTEM_PROMPT` rule 1 to explicitly forbid computing a
total from a number mentioned earlier in the conversation; the tool's returned `total` field must
be stated verbatim. Also a process lesson: clearing `orders` without also clearing
`conversation_messages` leaves stale totals in context.

### 4.5 Model mislabeling which item it acted on (fixed, prompt-level)

During one Gemini run, the model's own confirmation text named a nonexistent item
("Chicken Achari Masala") when removing something from the cart, and separately mislabeled a
Grilled Boti add-on as if it were Malai Boti in a different turn. Reconciling the totals suggests
the underlying tool calls were likely correct — the *narration* was wrong, which is arguably worse
than a pure hallucination, since the cart could be numerically right while telling the customer
something false about what changed.

**Fix applied**: added rule 9 to `CUSTOMER_SYSTEM_PROMPT` — confirmations must use the exact item
name as returned by the relevant tool call, never a name recalled from earlier context.

### 4.6 Currency formatting inconsistency (fixed)

Neither system prompt specified a price format. Different models defaulted differently — Gemini
mostly used "Rs. X", but drifted to "X.0 PKR" in one run; the Groq/Qwen fallback test used the
Indian Rupee symbol (₹) instead of Rupees, despite the restaurant being Pakistani.

**Fix applied**: added an explicit formatting rule to both system prompts — `"Rs. <amount>"`, no
decimals, never ₹.

### 4.7 Admin number role only checked at first contact (known limitation, not yet fixed)

`_get_or_create_user` only checks `ADMIN_NUMBERS` when a `User` row is first created; later edits
to the env var don't retroactively change an existing row's `role`. Not a bug in the strict sense
(role changes were never designed to be live), but a real gap if admin roles ever need to change
after a number has already messaged the bot once. Current workaround: edit the `role` column
directly in the DB. Left as-is for MVP; flag if this needs to become self-service later.

---

## 5. Webhook security & reliability (added)

- **Signature verification**: `receive_message` now validates Meta's `X-Hub-Signature-256` header
  (HMAC-SHA256 over the raw body, using `WHATSAPP_APP_SECRET`) before processing anything, and
  returns `403` on mismatch. Previously, any POST that mimicked Meta's payload shape — including
  one spoofing an admin's phone number — would have been processed as legitimate. Live-tested
  with a forged/unsigned request; correctly rejected.
- **Agent-failure fallback reply**: `_process_inbound` previously let an unhandled exception from
  `_route_message` propagate into the outer catch-all, meaning a customer got *silence* on any
  agent-layer failure (Gemini/Groq error, tool exception, etc.) with no record and no reply. Now
  wrapped in its own try/except with a friendly fallback ("Sorry, I hit a glitch — could you try
  that again?"), which also gets saved to `conversation_messages` so the next turn has coherent
  context. Confirmed live during a genuine Gemini `503` outage.
- **Duplicate-delivery dedupe**: `already_processed()` checks `wa_message_id` before processing;
  confirmed via direct query that no duplicate `wa_message_id` rows exist after real testing
  under fallback-induced latency. A theoretical race (two near-simultaneous deliveries of the
  same message both passing the check before either commits) is not fully closed — the DB's
  `UNIQUE` constraint on `wa_message_id` would catch it at the `IntegrityError` level, but a
  customer could still see two replies in that scenario. Not yet hardened further; not observed
  in practice.

---

## 6. LLM resilience — the full story (this took several rounds; read before touching model config)

### 6.1 Original latency investigation (superseded — see 6.2)

Original theory: `langchain-google-genai` probing for Application Default Credentials over gRPC,
hanging on Windows when no route to GCP's metadata server exists. `transport="rest"` was applied
and initially appeared to fix it (consistent sub-5s responses).

**This diagnosis was likely wrong**, or at best incomplete. `transport` turned out not to be a
real constructor argument on the installed `langchain-google-genai` version — it silently landed
in `model_kwargs` and was never read building the request (a known upstream issue). The apparent
fix likely coincided with a quiet period rather than resolving anything. Root cause of the
original intermittent slowness was never conclusively identified, but §6.2 makes it moot.

### 6.2 The actual problem: Gemini capacity, not a connection bug

Live testing surfaced a `503 UNAVAILABLE` — "model is currently experiencing high demand" — on
`gemini-3.5-flash-lite`. This is a Google-side availability issue, unrelated to transport,
credentials, or Windows networking. It explains both the "glitch" reply the customer received in
that test and, retroactively, casts doubt on whether the earlier latency issue was ever a hanging
connection rather than slow/queued responses during high demand.

### 6.3 Decision: add a fallback model, not a full provider switch

Rather than abandoning Gemini or committing fully to Groq, added `ModelFallbackMiddleware` (from
`langchain.agents.middleware`) so a primary-model failure (503, timeout, rate limit) automatically
retries the same turn on a different model. New file: `app/agents/resilience.py`.

### 6.4 First fallback attempt: Groq `llama-3.3-70b-versatile` — abandoned

This model is deprecated on Groq (confirmed via Groq's own deprecation notices). Not used.

### 6.5 Second attempt: Groq `qwen/qwen3.8-27b` — abandoned

Hit a `429` rate-limit error: this is a reasoning model with hidden chain-of-thought output that
counts against Groq's **output tokens per minute** limit, which is only **1000 OTPM** on the free
tier for this model — far too tight for reliable use, even with short WhatsApp-length replies.
Structural mismatch, not something worth tuning around.

### 6.6 Third attempt: Groq `openai/gpt-oss-20b` — passed, adopted

Chosen deliberately over `gpt-oss-120b` because of an existing finding from a separate project
(Research & Learning Companion): `gpt-oss-120b` had documented tool-call hallucination in
multi-step flows, a directly analogous workload to this project's search→add→disambiguate→submit
chain. `gpt-oss-20b` was untested but avoided that specific known risk. Passed multiple live runs:
correct ambiguity handling (listed multiple matches, asked rather than guessed), correct
`multiple_of` portioning and pricing, correct running totals, no hallucinated items or tool calls.
Noticeably faster than Gemini in practice, consistent with Groq's inference speed advantage.

### 6.7 Gemini's own failure, live — the case for the final swap

With Gemini still primary at the time, a live customer-flow test surfaced **two hard-rule
violations in a single conversation**: it silently picked one of two ambiguous matches instead of
asking (violating the core "never guess" rule), and it fabricated that an item didn't exist
despite finding it moments later in the same conversation (violating "never state from memory").
A separate run also produced the item-mislabeling bug in §4.5. This, combined with `gpt-oss-20b`'s
clean results, motivated flipping the assignment.

### 6.8 Final configuration: Groq primary, Gemini fallback

- **Primary**: `openai/gpt-oss-20b` on Groq (both `customer_agent.py` and `admin_agent.py`).
- **Fallback**: Gemini `gemini-3.5-flash-lite` (not full `gemini-3.5-flash` — that model's free
  tier is only 5 RPM, which was exhausted almost immediately during a forced-fallback test that
  triggered multiple Gemini calls per turn; `flash-lite`'s 15 RPM gives real headroom). Accepting
  `flash-lite`'s earlier-observed weaknesses as an acceptable tradeoff *specifically because* it's
  now a rarely-invoked fallback rather than the model handling every customer interaction.
- Both `get_customer_llm()`/`get_admin_llm()` now use `timeout=20, max_retries=1` — no
  `transport` argument (removed, was a no-op), no `temperature` on the Groq models (Gemini 3.5
  models also ignore it and warn).

### 6.9 A note on reading fallback logs

`ModelFallbackMiddleware` wraps **each individual LLM call**, not each customer message. A single
WhatsApp message that requires several ReAct steps (search → evaluate → add → compose reply) will
produce that many separate primary-fail/fallback-success pairs in the logs if the primary is down
— this looks like "flip-flopping" but is expected, correct behavior. Also: there is currently no
cooldown/circuit-breaker — every new message re-attempts the primary model first even during a
known outage, since each webhook call builds a fresh agent with no memory of prior failures.
Acceptable for now; worth revisiting if Groq/Gemini outages become frequent enough that the
wasted latency per message starts to matter.

---

## 7. WhatsApp token lifecycle (resolved)

Hit repeated `401`/token-expired errors during testing — sometimes well under the expected ~24h
window, traced to using a Graph API Explorer token (≈1h default lifespan) rather than the
WhatsApp app's own API Setup page token. Resolved by creating a **Business System User** in Meta
Business Settings, assigning the WhatsApp app to it, and generating a token with
`whatsapp_business_messaging` scope — this does not expire on the same rolling basis and has
removed this class of interruption from testing.

---

## 8. Admin order confirmation (added, ahead of original roadmap)

`db-schema.md` originally deferred order confirmation to a manual DB edit outside the bot
entirely (Phase 2 territory). Live-testing the admin flow surfaced this as a real gap once seen
in an actual chat — "confirm it" had no tool to call and the agent correctly (per spec) refused.

**Added `confirm_order(order_id, ready_in_minutes=0)`** as a new admin tool: validates the order
is `pending_confirmation`, sets it to `confirmed`, and sends the customer a WhatsApp confirmation
message directly (written by application code, not the LLM — keeps "LLM interprets, application
decides" intact). `ADMIN_SYSTEM_PROMPT` updated to require calling `get_pending_orders` first to
identify the order (never guessing a number) and confirming before executing, same pattern as
other admin writes. This can be removed/reverted to the original manual-DB-edit design later if
desired — it was a deliberate scope pull-forward, not a spec change forced by a bug.

---

## 9. Multi-turn / live conversation testing — results

All tested live via the real webhook + WhatsApp, not just in a Python shell:

- ✅ Webhook signature rejection (forged/unsigned request → `403`, nothing processed)
- ✅ Duplicate-message dedupe (no duplicate `wa_message_id` rows after testing under load)
- ✅ Customer ambiguity handling — multiple menu matches → asks, doesn't guess (on `gpt-oss-20b`;
  failed on Gemini in one run, see §4.5/§6.7)
- ✅ `multiple_of` enforcement and correct bundle pricing (post-fix)
- ✅ Deal component removal ("no raita") does not change price
- ✅ Admin mark-item-unavailable → confirm → customer correctly blocked from ordering that item
- ✅ Forced Groq→Gemini fallback under real conditions (primary deliberately broken)
- ✅ Roman Urdu input ("Eik deal 10 kr dein") correctly understood with no special handling needed
- ⚠️ Not yet done: full soak test of submit_order → admin confirm_order → customer notification,
  chained together in one live run
- ⚠️ Not yet done: deliberate stress test of the duplicate-delivery race window under genuinely
  concurrent (not just fallback-slowed) requests

---

## 10. Open items

- Full end-to-end soak test: customer builds and submits an order → admin receives notice →
  admin runs `confirm_order` → customer receives the confirmation message — as one continuous
  live run rather than pieces tested separately.
- Decide whether the duplicate-delivery race window (§5) needs hardening beyond the DB unique
  constraint, or is acceptable as-is for current volume.
- Decide whether to add a Groq-outage cooldown/circuit-breaker (§6.9) if fallback frequency
  increases.
- DB-backed conversation history is implemented (`conversation_messages`, last 16 turns loaded
  per user) — no longer an open item, but worth revisiting the 16-turn window if longer
  conversations start losing useful context.
- Drinks modeled as one row per size across all brands ("Coke/Pepsi/etc.") rather than per-brand
  — a known, deliberate tradeoff (see `agent-prompts.md` discussion); splitting into real
  per-brand rows is pure data work, deferred until there's another reason to touch the menu data.
- `admin_number_set` is only checked at first contact per number (§4.7) — fine for MVP, revisit
  if admin roles need to change after first contact becomes routine.

---

## Companion files

- `project-spec.md` — problem statement, architecture, roadmap
- `db-schema.md` — database schema (includes `conversation_messages`, Boti rename note)
- `food-fusion-menu.md` — menu content
- `agent-prompts.md` — system prompts and tool specs (updated: currency rule, exact-name rule,
  admin order-confirmation intent)
- `continuation-prompt.md` — handoff prompt for a new session
