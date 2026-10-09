# Food Fusion — Continuation Prompt

Paste this at the start of a new session, along with the companion files (`project-spec.md`,
`food-fusion-menu.md`, `db-schema.md`, `agent-prompts.md`, `implementation-log.md`,
`deployment-notes.md`), to pick up exactly where this session left off.

---

I'm continuing work on Food Fusion, a WhatsApp order-placement assistant for a restaurant in
Lahore. Full context is in the attached files — please read all of them before responding.
`implementation-log.md` (application changes) and `deployment-notes.md` (Azure + Meta go-live) are
the most important. Don't re-litigate or re-diagnose anything they document as resolved.

**Where things stand (2026-10-08):** the system is **deployed on Azure App Service and live on a
real, dedicated WhatsApp Business number** (Meta app in Live mode), and has been tested end to
end there: customer order (English or Roman Urdu) → submit → admin gets Approve/Decline buttons →
approve → customer gets the confirmation.

**Live configuration, load-bearing — don't change without a reason:**
- Hosting: Azure App Service (Linux, B1, Central US), `food-fusion-bot`; GitHub Actions deploys on
  push to `main` using a publish-profile secret; secrets are App Service app settings (no `.env`).
- LLMs: Groq `openai/gpt-oss-20b` primary for both agents; Gemini `gemini-3.5-flash-lite` fallback
  via `ModelFallbackMiddleware`. `gpt-oss-120b` is deliberately not used (garbled output after tool
  calls caused duplicate cart additions).
- Database: Supabase Postgres via `pg8000` (psycopg2 was blocked by Windows Smart App Control
  locally), SSL context with `CERT_NONE`.
- WhatsApp: dedicated number registered to the Cloud API (no longer usable as a normal WhatsApp
  account); permanent System User token; HMAC webhook verification. Business Verification and App
  Review were skipped on purpose (250 unique conversations/24h cap accepted).
- Admin notices: free-form interactive Approve/Decline buttons when the admin messaged the bot in
  the last 23 hours (window check reads `conversation_messages`), otherwise the approved template
  `order_pending_confirmation` (4 params incl. customer number). Taps of both kinds go to
  `_handle_admin_button` with no LLM call.

**Gotchas to remember:**
- Wiping `conversation_messages` makes the app think the admin's window is closed → template path
  → error `131042` until a payment method exists. Send a message from the admin number after any
  wipe.
- Admin and customer numbers must differ; `ADMIN_NUMBERS` is digits only (no `+`).

**Still open — pick up here:**
1. Add a payment method to the WhatsApp Business Account (template fallback is blocked without it).
2. Verify in the repo: role re-sync from `ADMIN_NUMBERS` in `_get_or_create_user`, and the
   `if reply:` indentation fix in `_extract_inbound`; re-test an image message.
3. Azure hardening: Always On, HTTPS Only, health check, budget alert.
4. GitHub Actions → OIDC with a service principal instead of the publish-profile secret.
5. Decline-button live test; admin-agent stress test on `20b`; capture the Groq `400` response body.
6. Business Verification when approaching the 250-conversation cap; the rest of §19 in the log.

**Working preferences, unchanged:**
- Code changes as per-file diffs or full-file content in chat, not zipped (files are edited
  directly on my machine).
- Don't re-explain or re-litigate settled decisions unless I ask to revisit them.
- Prefer code/data-level fixes over piling on system-prompt rules; prompt rules so far were for
  genuine factual gaps (e.g. "Deal items aren't discounts").
- This deployment is also a cloud-learning exercise: explain Azure concepts, give exact actions,
  wait for results. Never ask me to paste secrets — I fill those in myself.
