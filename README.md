# Food Fusion — WhatsApp Order Assistant

An LLM-powered WhatsApp assistant that lets customers of a food-court restaurant (Food Fusion,
Lahore) browse the menu, ask about items and prices, build an order in English or Roman Urdu, and
submit it for confirmation. Restaurant staff approve or decline each order with one tap on
WhatsApp, and the customer is notified automatically.

> **The LLM interprets. The application decides.** The model turns natural language into tool
> calls; prices, totals, availability, order state and every database write are deterministic
> backend code. The AI never confirms an order — a human always does.

## Features

- **Customer ordering over WhatsApp:** menu search (with fuzzy matching for spelling variants),
  recommendations, cart building, quantity changes, per-line customization notes
  ("leg piece instead of breast"), and order submission.
- **Ambiguity handling:** if a request could mean several items, the bot asks instead of guessing.
- **Human-in-the-loop confirmation:** each submitted order reaches staff as an Approve / Decline
  button message; the customer gets the outcome automatically.
- **Admin commands on the same number:** mark items unavailable/available for the day, add a daily
  special, list pending orders. Admin status is a database lookup, never an LLM judgment.
- **Multi-turn memory:** conversation history is stored in Postgres and passed to the agent.
- **Resilience:** Groq primary model with automatic Gemini fallback, signed-webhook verification,
  duplicate-message protection, and a database-grounded fallback reply if the model output is unusable.

## Architecture

```
WhatsApp ──► Meta Cloud API ──► FastAPI webhook (Azure App Service)
                                     │  verify signature → dedupe → look up sender role
                          ┌──────────┴──────────┐
                     Customer agent         Admin agent        (LangChain agents)
                          └──────────┬──────────┘
                                     ▼
                  Tool functions (menu, cart/orders, admin)  ──►  Supabase Postgres
                                     │
                     Groq (gpt-oss-20b) ──fallback──► Gemini (flash-lite)
```

| Layer | Technology |
|---|---|
| API | FastAPI, Uvicorn |
| Agents | LangChain 1.x (`create_agent`), `ModelFallbackMiddleware` |
| LLMs | Groq `openai/gpt-oss-20b` (primary), Gemini `gemini-3.5-flash-lite` (fallback) |
| Database | PostgreSQL on Supabase, SQLAlchemy 2, `pg8000` driver |
| Messaging | WhatsApp Cloud API (text, interactive buttons, approved template) |
| Hosting / CI | Azure App Service (Linux), GitHub Actions |

## Project structure

```
app/
  main.py              FastAPI app, /health, /privacy-policy
  config.py            Settings from environment variables
  database.py          SQLAlchemy engine/session (pg8000, SSL context)
  models.py            users, menu_items, availability_override, orders, order_items, conversation_messages
  conversation.py      Chat-history load/append and message dedupe
  agents/
    customer_agent.py  Customer tools + agent
    admin_agent.py     Admin tools + agent
    prompts.py         System prompts
    resilience.py      Gemini fallback middleware
  tools/
    menu_tools.py      Search, availability, specials
    order_tools.py     Cart and order lifecycle
    admin_tools.py     Availability overrides, confirm/reject order
  routers/
    webhook.py         WhatsApp webhook: verification, routing, sending, admin buttons
scripts/
  seed_menu.py         Loads the menu into the database
.github/workflows/
  azure-deploy.yml     Deploy to Azure App Service on push to main
```

## Local setup

**Requirements:** Python 3.13, a Postgres database (Supabase works), a Groq API key, a Gemini API
key, and a Meta developer app with the WhatsApp product.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
cp .env.example .env              # then fill in the values below
python -m scripts.seed_menu       # load the menu
uvicorn app.main:app --reload --port 8000
```

Tables are created automatically on startup (`create_all`). To receive WhatsApp webhooks locally,
expose port 8000 with a tunnel such as `ngrok http 8000`.

### Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | `postgresql+pg8000://user:password@host:5432/dbname` (URL-encode special characters; no `sslmode` parameter — SSL is configured in `database.py`) |
| `WHATSAPP_TOKEN` | Permanent System User access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Phone Number ID of the number registered to the Cloud API (not the phone number itself) |
| `WHATSAPP_VERIFY_TOKEN` | A string you choose; must match the webhook configuration in Meta |
| `WHATSAPP_APP_SECRET` | Meta app secret, used to verify webhook signatures |
| `ADMIN_NUMBERS` | Comma-separated admin WhatsApp numbers, digits only with country code, no `+` |
| `GROQ_API_KEY` | Primary LLM provider |
| `GEMINI_API_KEY` | Fallback LLM provider |

Never commit `.env`. In production these are set as Azure App Service application settings.

## WhatsApp setup

1. Create a Meta app with the WhatsApp product and register a dedicated number to the Cloud API.
   A registered number can no longer be used as a normal WhatsApp account.
2. Set the webhook callback URL to `https://<your-host>/webhook` with your verify token, and
   subscribe to the `messages` field.
3. Create a Utility template named `order_pending_confirmation` with the body
   `New order #{{1}} ({{2}}) — Total: Rs. {{3}}. Customer: {{4}}. Please respond below.` and two
   quick-reply buttons, **Approve** and **Decline**.
4. Add a payment method to the WhatsApp Business Account (templates are blocked without one).
5. Switch the app to Live mode (a privacy policy URL is required — the app serves one at
   `/privacy-policy`).

Business Verification is not required to message customers; without it Meta caps you at 250 unique
customer conversations per rolling 24 hours.

## How admin notifications work

When a customer submits an order, each admin number receives Approve / Decline buttons. If the
admin has messaged the bot within the last 23 hours, the notice is a free-form interactive message;
otherwise the approved template is used. Tapping a button is handled directly in code (no LLM call).
Admins can also type commands such as "chicken burger unavailable today" or "confirm order 12".

## Deployment (Azure App Service)

```bash
az group create --name food-fusion-rg --location centralus
az appservice plan create --name food-fusion-plan --resource-group food-fusion-rg --sku B1 --is-linux --location centralus
az webapp create --name <app-name> --resource-group food-fusion-rg --plan food-fusion-plan --runtime "PYTHON:3.13"
az webapp config set --name <app-name> --resource-group food-fusion-rg --startup-file "uvicorn app.main:app --host 0.0.0.0 --port 8000"
az webapp config appsettings set --name <app-name> --resource-group food-fusion-rg --settings KEY="value" ...
```

Then add the app's publish profile (downloaded from the Azure portal) as the GitHub secret
`AZURE_WEBAPP_PUBLISH_PROFILE`; every push to `main` deploys. Note that new subscriptions may have
zero compute quota in some regions and Free Trial subscriptions cannot request more. Full details
and a troubleshooting table are in [`deployment-notes.md`](docs/deployment-notes.md).

## Known limitations

- Drinks are modeled as one row per size across brands ("Coke/Pepsi/etc. 500ml"), not per brand.
- The 24-hour window check reads the local conversation history, so clearing that table makes the
  app use the (billable) template path until the admin messages the bot again.
- Replying in Roman Urdu is prompt-driven and not perfectly consistent.
- The database connection is encrypted but does not verify the server certificate.
- Deployment currently uses a publish-profile secret; OIDC federation is planned.

## Documentation

Design and implementation records live in [`docs/`](docs/):
[`project-spec.md`](docs/project-spec.md) · [`db-schema.md`](docs/db-schema.md) ·
[`agent-prompts.md`](docs/agent-prompts.md) · [`food-fusion-menu.md`](docs/food-fusion-menu.md) ·
[`implementation-log.md`](docs/implementation-log.md) · [`deployment-notes.md`](docs/deployment-notes.md)

## Roadmap

Customer profiles and order history, a staff web dashboard, order-status notifications, RAG over
the menu PDF, analytics, multi-branch support, and payments — see `docs/project-spec.md`.
