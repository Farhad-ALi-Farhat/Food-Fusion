# Food Fusion — WhatsApp Order-Placement Assistant

Project specification: problem statement, architecture, decisions, and roadmap.
Restaurant: Food Fusion, Level 2, Arfa Software Technology Park, Lahore — located in a food-court setting.

---

## 1. Problem Statement

Food Fusion operates inside a large building. Customers currently have to physically walk to the 2nd-floor food court to browse the menu and place an order. This project builds a WhatsApp-based assistant that lets a customer:

- Browse the menu and ask questions about items
- Get recommendations ("something spicy under Rs. 500")
- Build an order and see the calculated total
- Submit the order for confirmation

The restaurant owner/manager then manually confirms the order with the customer (call or message). **The AI never independently confirms an order** — a human is always the final authority on whether an order is accepted.

The restaurant is manually run: no inventory tracking, no stock counts, no order history system. Most items are available every day; occasional exceptions (an item sold out, or a special added for the day) are handled by the restaurant staff themselves, live, through WhatsApp.

### Core design principle

> **The LLM interprets. The application decides.**

The LLM's job is to understand natural language and turn it into structured intent (which items, what admin action, what clarification is needed). It never independently:
- calculates prices or totals
- decides whether an item exists or is available
- writes to the database directly
- confirms an order

All of that is deterministic application logic sitting behind tool calls.

---

## 2. Research & Key Decisions

This section captures what was worked out during design discussion, before any code was written.

### 2.1 Not an "AI that takes orders" — an order-assistance + human-approval system

Framing the bot this way (rather than as an autonomous ordering agent) is what makes the architecture reliable: every order the AI helps build ends in a `PENDING_CONFIRMATION` state that only a human can move forward.

### 2.2 Menu vs. availability are separate concepts

- **Permanent menu** (the catalog: items, descriptions, prices) — stable, lives in the DB, rarely changes.
- **Daily availability overrides** — temporary, per-day exceptions: an item marked unavailable for today, or a special item added for today only (auto-expires at end of day).

This avoids ever pretending the system tracks real inventory ("17 burgers left") — it only tracks *normal state + today's exceptions*, which matches how the restaurant actually runs.

### 2.3 Admin and customer share the same WhatsApp number

Rather than a separate admin dashboard or number, the restaurant owner/manager can message the **same bot number** to run admin commands ("chicken burger unavailable today", "add special beef burger for 600 today"). The backend — not the LLM — determines whether a sender is an admin, by looking up the sender's phone number against a `users` table with a `role` column (`CUSTOMER` / `ADMIN`). Admin tools are never exposed to a customer session, and the LLM cannot be talked into treating a customer as an admin ("I'm the owner..." never works, because role is a backend lookup, not something the model is asked to judge).

Admin actions still go through a confirm-before-execute step (LLM interprets → shows back what it understood → admin confirms → backend executes), mirroring the customer order-confirmation pattern.

### 2.4 Order lifecycle needs real states, not just created → confirmed

```
DRAFT → READY_FOR_CONFIRMATION → PENDING_CONFIRMATION → CONFIRMED
                                         │
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                         REJECTED   MODIFICATION   (stays
                                     REQUESTED     pending)
```

The bot only ever says "submitted for confirmation" — never "confirmed." Confirmation is a human action.

### 2.5 Guardrail layering

Rather than one large "guardrail" prompt, guardrails are layered:

1. **Input validation** — is the incoming message a type the bot can handle (text vs image/voice/location)?
2. **Intent/business boundary** — is this something the bot should even try to answer (menu/order questions: yes; "what's the capital of France": politely redirect)?
3. **Tool constraints** — the LLM can only call tools; it cannot directly touch price, availability, or order status in the database.
4. **Human approval** — every order and every admin write ends with a human/explicit-confirmation gate the AI cannot bypass.

### 2.6 Ambiguity always routes to a clarifying question

For cases like "give me two burgers" (multiple burger types exist), "remove the Coke" (multiple Cokes in cart), "make that three" (what's "that"?), or "how much is it?" (what's "it"?) — the rule is: **the LLM never silently guesses when a reference is ambiguous.** This is enforced by:
- disambiguating whenever a lookup tool returns more than one match
- tracking `last_referenced_item_id` explicitly as conversation state, rather than re-inferring "that" from raw history each turn
- resolving "it" / cart-total questions based on the customer's current conversation state (see §4)

### 2.7 Menu PDF is a fallback, not a data source

The database is the source of truth for prices/availability. The PDF is only used when a customer explicitly asks to see "the menu" as a document. The bot never re-parses the PDF to answer a price question if the DB already has the answer. PDF-based Q&A (RAG) is explicitly deferred to a later phase.

### 2.8 Menu data decisions (from the actual Food Fusion menu)

- Deals/Lunch Special Platters are **one category**, not two — the printed menu just has a page break.
- Each deal is a **flat, fixed-price `menu_item`**. Removing a bundled component never changes the price; adding a component (extra roti, extra seekh kabab, etc.) is priced via a small **Deal Add-ons** price list and added as an extra line.
- **Cheese** is modeled as its own `extras` menu item (Rs. 100), not a per-item modifier — "cheese omelette" = base item + Cheese extra as a second line, so totals stay a simple sum. This keeps `menu_items` flat with no separate modifiers table needed for MVP.
- **Drinks** (and any sized item) are modeled as separate rows per size, the same pattern as everything else — no separate variants table for MVP.
- Full extracted menu with prices lives in `food-fusion-menu.md` (companion file) and will map directly onto the `menu_items` table.

### 2.9 What's explicitly out of scope for MVP

Inventory management, stock quantities, order history, customer loyalty, automatic order confirmation, payment processing, delivery management, kitchen management, POS integration, multi-restaurant/multi-branch support, full role hierarchy (only `CUSTOMER`/`ADMIN` for now).

---

## 3. Architecture

```
                         WhatsApp
                            │
                            ▼
                   ┌─────────────────┐
                   │ Meta Cloud API  │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐
                   │  FastAPI        │
                   │  Webhook        │
                   └────────┬────────┘
                            │
                     Identify sender
                    (phone → role lookup)
                            │
                    ┌───────┴───────┐
                    │               │
                CUSTOMER          ADMIN
                    │               │
                    ▼               ▼
             Customer Agent    Admin Agent
                    │               │
                    └───────┬───────┘
                            │
                      Guardrails / Router
                            │
                    ┌───────┴───────┐
                    ▼               ▼
              Hardcoded          LLM Agent
              Responses          (LangChain)
              (greeting,              │
               "show menu", etc.)     ▼
                                Tool Calling
                                      │
                     ┌────────────────┼────────────────┐
                     ▼                ▼                ▼
               Menu Tools       Order Tools       Admin Tools
                     │                │                │
                     └────────────────┼────────────────┘
                                      ▼
                                PostgreSQL
                     ┌────────────────┼────────────────┐
                     ▼                ▼                ▼
                  Menu            Orders          Availability
               (permanent)      (with state)       Overrides
                                                  (+ daily specials)
```

### Component responsibilities

| Layer | Responsibility |
|---|---|
| Meta WhatsApp Cloud API | Message transport in/out |
| FastAPI webhook | Receives messages, identifies sender, routes to customer/admin flow |
| Router / guardrails | Filters input, decides hardcoded vs. LLM path, enforces business boundary |
| LangChain + LLM (Groq `gpt-oss-20b` primary, Gemini fallback) | Natural-language understanding → structured tool calls; never touches the DB directly. Originally Gemini-only; changed after live testing (see `implementation-log.md` §6, §8). |
| Azure App Service | Hosts the FastAPI app (Linux, B1) with a stable HTTPS URL; deployed from GitHub via Actions (see `deployment-notes.md`) |
| Tool functions (backend) | Deterministic logic: pricing, availability checks, cart math, order state transitions, admin writes |
| PostgreSQL | Source of truth for menu, orders, overrides, users |

### LLM tool list

**Customer tools:** `search_menu(query)`, `get_item_details(item_id)`, `check_availability(item_id)`, `get_daily_specials()`, `add_to_cart(item_id, quantity)`, `remove_from_cart(item_id)`, `update_quantity(item_id, quantity)`, `get_cart_summary()`, `submit_order()`.

**Admin tools (backend-gated):** `mark_item_unavailable(item_id, reason?)`, `mark_item_available(item_id)`, `add_daily_special(name, price)`, `get_pending_orders()`.

### Database entities (initial)

`menu_items`, `availability_override`, `orders`, `order_items`, `users` (customers + admins, distinguished by `role`), and later `restaurant` (static info). Full schema to be drafted next, informed by the finalized menu.

### Conversation state (customer)

```
IDLE → BROWSING → BUILDING_ORDER → REVIEWING_ORDER → SUBMITTED
```

Tracked per phone number in the DB (not just in memory), so context survives across messages and app restarts.

### WhatsApp-specific constraints to design around

- Meta's Cloud API 24-hour free-form messaging window (template messages needed outside it)
- Non-text message types (image/voice/location) need a graceful fallback response
- Rapid multi-message bursts from a customer — handled via explicit conversation state rather than message batching, at least for MVP

---

## 4. Roadmap / Implementation Phases

### Phase 0 — Design (complete)
- [x] Problem statement and architecture (this document)
- [x] Menu extraction and pricing decisions (`food-fusion-menu.md`)
- [x] Finalize DB schema (tables + columns, order state enum) — `db-schema.md`
- [x] Write the customer-agent system prompt / tool-calling spec — `agent-prompts.md`
- [x] Define admin command list and confirmation wording — `agent-prompts.md`

### Phase 1 — MVP (complete; deployed and live)
- WhatsApp webhook → FastAPI → LangChain → tools → PostgreSQL, end to end — **done, running on Azure**
- Customer: greeting, restaurant info, view menu, ask about items/prices/availability, get recommendations, build/modify/review order, submit order, receive "awaiting confirmation" message — **done** (English and Roman Urdu; per-line customization notes added)
- Restaurant: receives each pending order as an **Approve / Decline** button message on WhatsApp and confirms or rejects with one tap; the customer is notified automatically — **done** (this pulled forward part of what was planned for the Phase 2 dashboard)
- Admin: mark items unavailable/available, add a daily special, check pending orders, confirm/decline by typing — **done**, via the same WhatsApp number

Progress:
- [x] FastAPI project scaffold — models, tools, agents, webhook router
- [x] LLM: Groq `gpt-oss-20b` primary with Gemini fallback (changed from Gemini-only after live testing)
- [x] Menu seed script (`scripts/seed_menu.py`) — all items from `food-fusion-menu.md`
- [x] Real WhatsApp send, webhook signature verification, delivery-status logging
- [x] DB-backed conversation history (`conversation_messages`)
- [x] End-to-end tests against live Postgres, real LLMs and real WhatsApp
- [x] Deployed to Azure App Service with GitHub Actions CI/CD
- [x] Meta app in Live mode on a dedicated number (Business Verification deliberately deferred)

Still to do before wider launch:
- [ ] Add a payment method to the WhatsApp Business Account (template fallback is blocked without it)
- [ ] Azure hardening: Always On, HTTPS Only, health check, budget alert; GitHub Actions via OIDC instead of a publish-profile secret
- [ ] Business Verification when nearing the 250-unique-conversations/24h cap
- [ ] Remaining items in `implementation-log.md` §19

### Phase 2
- Customer profiles, order history
- Structured deals/specials management
- Restaurant dashboard (web) for viewing orders and history (basic confirm/decline already works via WhatsApp buttons)
- Order status notifications
- Better conversation memory

### Phase 3
- RAG over the menu PDF for complex questions
- Analytics
- Multiple restaurants / branches
- Staff accounts (role hierarchy beyond CUSTOMER/ADMIN)
- Payment integration

---

## 5. Companion files

- `food-fusion-menu.md` — full extracted menu with prices, categories, and modeling notes; maps directly onto `menu_items`.
- `db-schema.md` — the PostgreSQL schema built from this document.
- `agent-prompts.md` — customer/admin system prompts and tool specs.
- `deployment-notes.md` — how the app was deployed to Azure and taken live on WhatsApp: concepts, commands, troubleshooting table, hardening checklist.
- `implementation-log.md` — what's actually been built, fixed, and tested since these design docs were finalized (LangChain 1.0 migration, Gemini model notes, latency investigation, test results). Read this before assuming something described as "not yet started" here still is.
