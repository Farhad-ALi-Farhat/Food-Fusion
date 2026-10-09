# Food Fusion — Implementation Log & Findings

Chronological record of what's been built, broken, fixed, and tested since the design docs
(`project-spec.md`, `db-schema.md`, `agent-prompts.md`, `food-fusion-menu.md`) were finalized.
Read this alongside those four — this file covers *implementation reality*, not design decisions.

**Status as of this update (2026-10-08):** Food Fusion is **deployed on Azure App Service and live on a
real, dedicated WhatsApp Business number**, and the full loop has been tested end to end on that
setup: customer orders (English and Roman Urdu) → order submitted → admin receives Approve/Decline
buttons → admin approves → customer receives the confirmation. Primary LLM is `openai/gpt-oss-20b`
on Groq for both agents, with Gemini (`gemini-3.5-flash-lite`) as fallback. The Azure and Meta
go-live story (including every quota, auth and billing obstacle) is in `deployment-notes.md`; this
file covers the application-level changes made around it.

---

## 1. Environment (as currently set up)

- **Hosting**: Azure App Service (Linux, B1, Central US), `food-fusion-bot.azurewebsites.net`;
  deploys automatically from GitHub on push to `main` via GitHub Actions. Secrets live in App
  Service application settings, not a `.env` file. See `deployment-notes.md`.
- **Dev machine**: Windows, VS Code, Python 3.13 (local dev only now; production is Azure Linux).
- **Database**: Supabase Postgres via `pg8000` (not `psycopg2`) — see §9.
- **LLM**: Groq (`openai/gpt-oss-20b`) primary for both agents, Gemini (`gemini-3.5-flash-lite`)
  fallback via `ModelFallbackMiddleware`. Full history of how this was arrived at is in §6
  (original log) and §8 — don't change without reading both.
- **WhatsApp**: Meta app in **Live mode**; a dedicated number registered to the Cloud API as the
  bot's number (it can no longer be used as a normal WhatsApp account). Permanent Business System
  User token. `WHATSAPP_APP_SECRET` for webhook signature verification. Admin notices go out as
  interactive Approve/Decline buttons when the admin's 24-hour window is open, otherwise via the
  approved `order_pending_confirmation` template — see §7 and §16.
- **Admin / customer numbers**: `ADMIN_NUMBERS` (Azure app setting, digits only, no `+`) is the
  list of admin numbers; a separate personal number is used as the test customer.
- Menu corrected for the Boti spelling (prior session); no menu-data changes since.

---

## 2–6. Prior session content — unchanged, not reproduced here

Covers: LangChain 1.0 migration, `.text` vs `.content`, the `multiple_of` pricing bug, the
`search_menu` fuzzy-match fix, the Botti→Boti rename, the stale-total bug, the item-mislabeling
bug, currency formatting, webhook signature verification, the agent-failure fallback reply, the
full Gemini-latency-to-503-to-resilience-middleware saga, and the WhatsApp token lifecycle fix.
**All still accurate and still in effect.** See the previous version of this file (same filename,
earlier revision) if you need the detailed narrative — not repeated here to keep this update
focused on what's new.

---

## 7. Admin order confirmation via WhatsApp template buttons (new this session)

### 7.1 Why this changed from the original `confirm_order`-via-chat design

The typed `confirm_order` admin tool (added in the prior session) worked, but live testing
surfaced a structural problem: a tool-binding bug (see §7.2) meant it silently had no tools at
all for a while, and separately, the conversational flow ("confirm it" → "which order?" →
"let me fetch pending orders" → often stalling there, see §8) was fragile and depended on the LLM
correctly chaining `get_pending_orders` → disambiguation → `confirm_order` in one turn.

**Decision**: move the primary confirm/decline path to a **Meta-approved message template** with
tappable Approve/Decline buttons, handled **deterministically in `webhook.py` — no LLM call at
all** for that specific action. This also solves an unrelated but real problem: Meta's 24-hour
re-engagement window (error `131047`) was silently killing plain-text admin notices whenever the
admin hadn't messaged the bot recently. Templates are exempt from that window.

> **Update:** the button flow below is still the design, but admin notices are now sent as
> free-form interactive buttons when the admin's 24-hour window is open and as the template only
> as a fallback — see §16.

### 7.2 Tool-binding bug found and fixed (blocked ALL admin tools, not just confirm_order)

In `admin_agent.py`, the `return [...]` statement meant to close `build_admin_tools` was
misindented — it landed **inside** the `confirm_order` nested function, after that function's own
`return`. This made it dead code, and meant `build_admin_tools` had **no return statement at
all**, implicitly returning `None`. Every admin agent build therefore had `tools=None` — the admin
agent had zero tools bound, for every admin interaction, until this was found. Fixed by correcting
the indentation so the list-return belongs to `build_admin_tools`.

A second bug in the same file: `from fastapi import logger` is not a working `logging.Logger` —
it's a module. Any call to `logger.error(...)` using it would have raised `AttributeError` and
crashed the turn instead of logging and falling back gracefully. Fixed with a proper
`logging.getLogger(__name__)`.

### 7.3 Template setup

- **Template name**: `order_pending_confirmation`, category Utility, body:
  `"New order #{{1}} ({{2}}) — Total: Rs. {{3}}. Please respond below."` plus two quick-reply
  buttons (Approve / Decline). Approved by Meta.
- `{{1}}` = order id, `{{2}}` = joined item summary (one line, no newlines — Meta templates don't
  allow them in parameters), `{{3}}` = total.
- Button taps arrive as `type: "button"` messages with a `payload` field
  (`APPROVE_<id>` / `DECLINE_<id>`), distinct from normal text messages.
- `_extract_inbound` in `webhook.py` now has a `"button"` kind; `_process_inbound` routes it to
  `_handle_admin_button` before the text/non-text branching. `_handle_admin_button` checks
  `user.role == admin`, parses the payload, calls `admin_tools.confirm_order` or
  `admin_tools.reject_order` directly (plain Python, no agent invocation), sends the
  customer-facing result message, and returns an admin-facing acknowledgment — all logged to
  `conversation_messages` same as any other turn.
- **`reject_order`** added to `admin_tools.py` (mirrors `confirm_order`: validates
  `pending_confirmation` status, sets `rejected`, records `confirmed_by`) and exposed as a typed
  fallback tool in `admin_agent.py` for admins who'd rather type than tap.
- `format_staff_order_notice` (the old plain-text notice) is now dead code — nothing calls it
  since `_notify_admins_of_order` switched to `send_order_confirmation_template`. Left in place,
  harmless, candidate for deletion whenever convenient.

### 7.4 Confirmed live, DB-verified

Order #19: Roman Urdu customer order with a customization note → `submit_order` →
`_notify_admins_of_order` sent the template (item list and note correctly visible, not a
placeholder) → admin tapped **Approve** → customer received the real confirmation text →
`orders.status = 'confirmed'`, `confirmed_by` set. First fully closed, DB-verified, real
end-to-end run of the whole system.

---

## 8. The gpt-oss-120b degenerate-output bug — a real data-corruption risk (new this session)

### 8.1 What happened

After the §7.2 tool-binding fix, `confirm_order` still appeared to stall on a live test — the
agent would say "let me fetch the current pending orders" and never produce a final answer.
Separately, while testing Roman Urdu phrasing on the customer side with `gpt-oss-120b` as primary,
replies started coming back as hundreds of lines of repeated, garbled text
(`"We… … …… ..."` etc.) — sometimes with a correct answer buried at the very end, sometimes not.

A guard was added (`looks_degenerate()` in both `customer_agent.py` and `admin_agent.py`,
checking reply length and ellipsis-character density) to catch this before it reached WhatsApp,
replacing it with a generic "Sorry, I couldn't process that — could you try again?" message.

### 8.2 The real bug this exposed: the tool call had already succeeded

Closer inspection of the garbled text showed fragments like *"Added Deal 10: Chicken Tikka..."*
buried inside the garbage — meaning `add_to_cart` had been called and had **succeeded**, and only
the model's subsequent text generation degenerated. The generic "try again" fallback message was
therefore actively misleading: the customer, reasonably, retried the exact same request, which
added the item **again** for real. One test cart ended up with 4x Deal 10 from two user messages
and two retries, confirmed via direct DB inspection.

**This is a materially worse failure mode than garbled text alone** — it's silent, repeated,
real cart corruption, and it was specific to `gpt-oss-120b`: every reproduction (a "deal 10"
message, and separately "eik zinger burger kr dein" with no "deal" in it) occurred immediately
after a successful mutating tool call, in both English and Roman Urdu phrasing.

### 8.3 Fixes applied

1. **Fallback behavior changed** from a generic retry prompt to a DB-grounded cart summary.
   `_safe_cart_fallback()` in `customer_agent.py` queries the actual current cart via
   `order_tools.get_cart_summary` and shows the customer their real state instead of inviting a
   retry that could double an action. This stays in place regardless of which model is primary —
   it's a correctness guard, not a model-specific patch.
2. **Customer agent switched back to `openai/gpt-oss-20b`** as primary. Retested against both
   trigger phrases (plain "deal 10" and "eik zinger burger kr dein") — clean on `20b`, no
   degenerate output observed. Admin agent also switched to `20b` for consistency (not yet
   independently stress-tested to the same depth as the customer path).
3. **Separately found and fixed**: both `gpt-oss-20b` and `120b` misread "Deal 10" (and Deal-N
   generally) as a request for a price *discount* rather than a menu item name, refusing with
   "I can't apply discounts..." before ever calling `search_menu`. This is a factual gap, not a
   behavioral one — fixed with a one-line menu-fact clarification in both system prompts (see
   `agent-prompts.md`), not a new constraint.

### 8.4 Open question

Why `120b` degenerates specifically after a successful tool call is not understood — no root
cause identified, only the trigger pattern (post-tool-call text generation) and the practical fix
(don't use `120b` as primary; ground the fallback in real DB state regardless of model). Not worth
further investigation unless `120b` becomes necessary again for some other reason.

---

## 9. Windows Smart App Control blocked psycopg2's DLL (new this session, unrelated to the above)

`uvicorn` startup began failing with `ImportError: DLL load failed... An Application Control
policy has blocked this file`, pointing at a `delvewheel`-vendored `libcrypto` DLL inside
`psycopg2-binary`. Windows Smart App Control blocked it as untrusted — same class of issue
previously hit with Jupyter Lab, but this time not avoidable via a launch-method change (`python
-m uvicorn` did not help; the block is on the DLL itself, not the launcher).

**Fix**: switched the Postgres driver from `psycopg2` to **`pg8000`**, a pure-Python driver with
no compiled extensions — nothing for SAC to block. Required two changes:
- `DATABASE_URL` driver prefix: `postgresql+psycopg2://` → `postgresql+pg8000://`, and the
  `?sslmode=require` query parameter removed (not a `pg8000`-recognized parameter).
- `database.py`: SSL now configured via an explicit `ssl.SSLContext` passed through
  `connect_args={"ssl_context": ...}`, since `pg8000` doesn't use `sslmode`. First attempt used
  `ssl.create_default_context()` (full certificate verification), which failed against Supabase's
  pooler with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`. Resolved
  by explicitly setting `check_hostname = False` and `verify_mode = ssl.CERT_NONE` — this matches
  (not weakens) the security posture `psycopg2`'s `sslmode=require` already had: encrypted
  transport, no certificate chain validation. **Known residual gap, not new**: no protection
  against a MITM on the DB connection path; Supabase does publish a CA cert that could be loaded
  via `load_verify_locations()` for proper verification later, deferred as non-urgent since it's
  the same risk level as what shipped before this switch, not a regression.

---

## 10. Order-line customization notes (new this session)

Live testing surfaced a real request a customer actually made ("leg piece instead of breast" on a
Deal 10) that the system had no way to handle — `db-schema.md`'s `order_items.note` column existed
from the original design but was never wired into any tool.

**Added**: `note: str | None` parameter threaded through `order_tools.add_to_cart` →
`customer_agent.py`'s `add_to_cart` tool → surfaced in `get_cart_summary`'s returned line dicts →
surfaced in `admin_agent.py`'s `get_pending_orders` → surfaced in `webhook.py`'s
`_notify_admins_of_order` template parameter construction. A note is purely descriptive — **it
does not change pricing**, consistent with the original "deals are flat-priced, don't decompose
components" design principle. Confirmed live: a leg-piece request was correctly captured, visible
in the admin's template notice (not silently dropped), and visible via the typed
`get_pending_orders` path too.

---

## 11. Prompt rules added this session

Two new hard rules, mirrored across `CUSTOMER_SYSTEM_PROMPT` and `ADMIN_SYSTEM_PROMPT`:

- **"Deal" items are regular menu items, not discount requests** — see §8.3 point 3. A factual
  clarification about the menu, not a behavioral constraint.
- **Reply in Roman Urdu/Urdu when the customer/admin writes in it** — followed inconsistently in
  practice (confirmed via logs: Gemini followed it correctly on a fallback turn triggered by two
  consecutive Groq `400 Bad Request` errors; whether `gpt-oss-20b` follows it as reliably across
  multiple consecutive turns is not yet cleanly isolated). Accepted as-is since both languages are
  mutually understood by real users — not worth further prompt engineering unless it starts
  causing genuine confusion rather than stylistic drift.

---

## 12. New, unexplained: Groq `400 Bad Request`

Observed twice consecutively in one log capture, immediately triggering the Gemini fallback (which
then succeeded). Response body was not captured/logged at the time, so the actual cause is
unknown — distinct from the previously-diagnosed `429` (rate limit) and `503` (capacity) failure
modes. If this recurs, capture and log the response body (similar to how `WhatsApp send failed`
already logs `response.text`) before assuming it's the same root cause as past Groq issues.

---

## 13. Live testing — cumulative results

All tested through the real webhook and real WhatsApp, not just a Python shell:

- ✅ Webhook signature rejection, duplicate-message dedupe, customer ambiguity handling
- ✅ `multiple_of` enforcement and correct bundle pricing
- ✅ Deal component removal ("no raita") does not change price
- ✅ Admin mark-item-unavailable → customer correctly blocked from ordering that item
- ✅ Forced Groq→Gemini fallback under real conditions
- ✅ Roman Urdu input, including an added item and a correctly applied order note
- ✅ Approve button → `confirm_order` fires, customer notified, DB shows `confirmed`
- ✅ Full loop running on **Azure** (not ngrok) with the original test number
- ✅ Full loop on the **real registered number in Live mode**, customer on a separate phone
- ✅ Interactive Approve/Decline buttons (free-form, no template) render for the admin and the tap
  is parsed and routed correctly (§16)
- ⚠️ Decline button path: implemented, not yet explicitly tested live
- ⚠️ Template fallback (admin window closed): cannot succeed until a WABA payment method is added
- ⚠️ Non-text message fallback (image/voice) re-check after the §18 indentation fix
- ⚠️ Admin agent on `gpt-oss-20b` not stress-tested as deeply as the customer agent
- ⚠️ Groq `400 Bad Request` still undiagnosed (seen again on Azure)
- ⚠️ Duplicate-webhook-delivery race window still theoretical, never observed

---

## 14. Azure deployment (summary — full narrative in `deployment-notes.md`)

Resources: resource group `food-fusion-rg`, Linux App Service plan `food-fusion-plan` (B1), Web App
`food-fusion-bot`, all in **Central US**. Startup command:
`uvicorn app.main:app --host 0.0.0.0 --port 8000`. Env vars set as App Service settings.
`requirements.txt` was regenerated with exact pinned versions (it still listed `psycopg2-binary`
and old LangChain constraints and omitted `langchain-groq`) and verified in a clean venv.

Obstacles worth remembering: a Free Trial subscription has zero compute quota and cannot request
more (upgrade to Pay-As-You-Go); UAE North and East US still showed 0 quota while Central US worked;
the GitHub deploy failed with "publish profile invalid" until the profile was re-downloaded from the
portal instead of copied out of a terminal. Details and commands are in `deployment-notes.md`.

---

## 15. Going live on Meta

- App switched from Development to **Live mode**. A privacy policy page is required first; it is
  served from the app itself at `/privacy-policy` (a static HTML route in `main.py`).
- **Business Verification and App Review were deliberately skipped.** For a single business using
  its own app and WABA they are not required to message real customers; the only consequence is
  the cap of 250 unique customer conversations per rolling 24 hours, accepted for now. Revisit when
  volume approaches that.
- A dedicated number was registered to the Cloud API. Once registered, a number cannot be used as
  a normal WhatsApp account (no phone app, no chat history on the handset) — everything goes
  through the API. The Phone Number ID (not the number itself) goes into
  `WHATSAPP_PHONE_NUMBER_ID`.
- The template was recreated under the new WABA with a **fourth body parameter, the customer's
  number**, so staff can call the customer: `New order #{{1}} ({{2}}) — Total: Rs. {{3}}.
  Customer: {{4}}. Please respond below.` plus the two quick-reply buttons.
- The `RAW PAYLOAD` debug logging was removed from `webhook.py`; it logged customer names and
  numbers, which conflicts with the published privacy policy. Delivery-status logging stays.

---

## 16. WhatsApp billing and the admin notice (read before changing notification code)

**What happened.** The first admin template sent from the new WABA failed asynchronously with
`131042` ("business eligibility payment issue") — first because no billing currency was configured,
then, after the currency was set, because no payment method was attached. The send API still
returned 200; the failure only appears in the status webhook, which is why status logging matters.
The card used for Azure was not accepted by Meta's billing.

**Pricing context (third-party summaries, verify against Meta's rate card).** Since 1 Oct 2026,
utility templates are charged even inside an open customer-service window, and free-form replies get
the first 1,000 delivered messages per business number per month free, then are charged at the
utility rate. Rates quoted were fractions of a cent to about a cent per message; the exact Pakistan
rate was not confirmed. More admins means more template messages per order.

**Decision: hybrid notification.** `_notify_admins_of_order` now checks, per admin, whether the admin
has messaged the bot in the last 23 hours (`_admin_window_open`, reading the admin's latest
user-role row in `conversation_messages`). If so it sends a free-form **interactive reply-button
message** (`send_order_interactive`, same Approve/Decline buttons, no template); otherwise it falls
back to the template (`send_order_confirmation_template`). Button taps and any admin message renew
the window, so in normal use the template is only needed after a long quiet gap.

**Caveats.**
- If the window is closed and no payment method is attached, the template fallback fails. Practical
  mitigation: the admin sends any message to the bot at the start of the day.
- The window check reads **our** database, not Meta's state. Wiping `conversation_messages` during
  testing makes the app think the window is closed even when Meta's is still open, which sends the
  template and triggers `131042`. After any wipe, send a message from the admin number first.
- The customer replies share the same 1,000/month free allowance, so billing will be needed at
  volume regardless.
- Taps on interactive buttons arrive as `type: "interactive"` (`button_reply.id`), not
  `type: "button"` (template quick replies, `button.payload`). `_extract_inbound` handles both and
  both map to the same `kind: "button"` handler.

---

## 17. Admin role not re-checked after first contact (hit again; fix recommended)

A number that had been used as a customer was later added to `ADMIN_NUMBERS`, but its existing
`users` row stayed `customer`, so the bot greeted it as a customer and the Approve tap returned
"You're not authorized to do that." (the role check working correctly on stale data). Clearing the
`users` table fixed the instance. **Recommended code fix:** have `_get_or_create_user` compute the
expected role from `ADMIN_NUMBERS` on every message and update the row if it differs, making
`ADMIN_NUMBERS` the single source of truth (this also demotes numbers removed from the list, and
overrides any manual role edit in the DB). **Verify this is in the repo** — see open items.

---

## 18. Inbound parsing bug caught in review

When the `interactive` branch was added to `_extract_inbound`, an `if reply:` line was dedented
outside the branch. It would not raise an `IndentationError`, but any message type other than
`interactive` (image, voice note, location, sticker) that reached it would raise
`UnboundLocalError`, which the surrounding `except (KeyError, IndexError, TypeError)` does not
catch — a 500 from the webhook and Meta retries. Fix: indent `if reply:` and its return inside the
`interactive` branch. **Verify in the repo.**

---

## 19. Open items

- Attach a payment method to the WhatsApp Business Account (needed for the template fallback and
  for volume beyond the free allowance).
- Verify in the repo: role sync in `_get_or_create_user` (§17) and the `if reply:` indentation
  fix (§18); then re-test an image message from the customer number.
- Azure hardening: turn on **Always On** (it was `false` at creation; an idle app can unload and
  cold-start slowly on the next webhook), set **HTTPS Only**, add a health-check path, and create a
  cost budget alert.
- GitHub Actions authentication: move from the publish-profile secret to OIDC federation with a
  service principal (no stored secret) — the "proper way", deliberately deferred.
- Decline-button live test; stress-test the admin agent on `gpt-oss-20b`.
- Capture the Groq `400` response body to diagnose it instead of only falling back.
- Business Verification when approaching the 250-conversation cap.
- Decide whether to harden the duplicate-webhook race (still theoretical).
- Clean-ups: delete dead `format_staff_order_notice`; load the Supabase CA certificate to replace
  `CERT_NONE`; optionally truncate only the items list (not the whole body) in
  `send_order_interactive` so the customer line is never cut off.

---

## Companion files

- `project-spec.md` — problem statement, architecture, roadmap (updated to reflect deployment)
- `db-schema.md` — database schema
- `food-fusion-menu.md` — menu content
- `agent-prompts.md` — system prompts and tool specs
- `deployment-notes.md` — Azure + Meta go-live narrative, concepts learned, troubleshooting table
- `continuation-prompt.md` — handoff prompt for a new session
