# Food Fusion — Database Schema

PostgreSQL. Matches the architecture and menu decisions in `project-spec.md` and
`food-fusion-menu.md`.

Design principle carried over from the architecture doc: **the LLM never writes to this DB
directly** — every write goes through a backend tool function.

**Note on naming:** menu items originally seeded as "...Botti" were renamed to "...Boti"
(single t) across the DB, `scripts/seed_menu.py`, and `food-fusion-menu.md`, since no customer
was expected to type the double-t spelling. See `implementation-log.md` §4.3.

---

## Entity overview

```
users ──< orders ──< order_items >── menu_items ──< availability_override
  │
  └──< conversation_messages
```

- `users`: customers and admins (same table, distinguished by `role`)
- `menu_items`: the permanent catalog
- `availability_override`: today-only exceptions
- `orders` / `order_items`: an order and its line items
- `conversation_messages`: turn-by-turn WhatsApp history for multi-turn context

---

## 1. `users`

```sql
CREATE TYPE user_role AS ENUM ('customer', 'admin');

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    whatsapp_number VARCHAR(20) UNIQUE NOT NULL,
    name            VARCHAR(100),
    role            user_role NOT NULL DEFAULT 'customer',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- `name` is nullable — a first-time customer may not have given a name yet.
- Admins are rows with `role = 'admin'`, added manually — no self-service admin signup.
- **Known limitation**: `role` is only checked against `ADMIN_NUMBERS` the first time a number
  messages the bot (at row-creation time). A later change to `ADMIN_NUMBERS` does not
  retroactively update an existing user's `role` — requires a manual `UPDATE`. Fine for MVP;
  revisit if role changes need to become routine/self-service.

---

## 2. `menu_items`

```sql
CREATE TABLE menu_items (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    description     TEXT,
    category        VARCHAR(50) NOT NULL,
    price           NUMERIC(10, 2) NOT NULL,
    multiple_of     INTEGER NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **`category`**: `breakfast`, `lunch_offers`, `fast_food`, `chinese_corner`,
  `lunch_special_platters`, `drinks`, `extras`, `deal_addons`.
- **`price`**: the item's normal price, used for order totals — the LLM never computes this.
- **`multiple_of`**: enforces bundle-only items (Malai Boti / Grilled Boti add-ons, `= 4`).
  Validated by `add_to_cart` and `update_quantity` — **both** now enforce and price this
  correctly; `update_quantity` originally lacked the check, fixed alongside the pricing bug (see
  `implementation-log.md` §4.1). Subtotal for these items is
  `(quantity // multiple_of) * price`, not `quantity * price` — the latter overcharges by a
  factor of `multiple_of` for any bundle item.
- **`is_active`**: permanent on/off switch, separate from daily `availability_override`.
- Deals, add-ons, and drinks-per-size follow the same flat-row modeling as originally decided —
  unchanged.

---

## 3. `availability_override`

```sql
CREATE TABLE availability_override (
    id            SERIAL PRIMARY KEY,
    menu_item_id  INTEGER NOT NULL REFERENCES menu_items(id),
    override_date DATE NOT NULL,
    is_available  BOOLEAN NOT NULL,
    reason        VARCHAR(255),
    created_by    INTEGER REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (menu_item_id, override_date)
);
```

Unchanged from original design. Live-tested: mark-unavailable → customer correctly blocked from
ordering that item.

---

## 4. `orders`

```sql
CREATE TYPE order_status AS ENUM (
    'draft',
    'ready_for_confirmation',
    'pending_confirmation',
    'confirmed',
    'rejected',
    'modification_requested'
);

CREATE TABLE orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES users(id),
    status       order_status NOT NULL DEFAULT 'draft',
    total        NUMERIC(10, 2) NOT NULL DEFAULT 0,
    notes        TEXT,
    confirmed_by INTEGER REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- `confirmed_by` is now actively written by the `confirm_order` admin tool (see
  `agent-prompts.md` §2), rather than only being set via a manual out-of-band DB edit as
  originally planned. `status` transitions `pending_confirmation → confirmed` through that tool.
- Everything else unchanged from original design.

## 5. `order_items`

```sql
CREATE TABLE order_items (
    id           SERIAL PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    unit_price   NUMERIC(10, 2) NOT NULL,
    subtotal     NUMERIC(10, 2) NOT NULL,
    note         VARCHAR(255)
);
```

Unchanged. `ON DELETE CASCADE` on `order_id` means deleting an `orders` row (e.g. clearing a
stale test draft cart with `DELETE FROM orders WHERE status = 'draft'`) cleans up its
`order_items` automatically.

---

## 6. `conversation_messages`

Turn-by-turn WhatsApp history so the webhook can pass `chat_history` into the agent. Not a
Phase 2 dashboard — just enough memory for multi-turn cart talk.

```sql
CREATE TYPE message_role AS ENUM ('user', 'assistant');

CREATE TABLE conversation_messages (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    role           message_role NOT NULL,
    content        TEXT NOT NULL,
    wa_message_id  VARCHAR(128) UNIQUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- `wa_message_id` is Meta's inbound message id, stored only on the customer/admin **user** row
  for that turn — used both to ignore webhook retries (`already_processed()`) and, live-tested,
  confirmed to have zero duplicate rows after real traffic. Assistant rows leave it NULL
  (Postgres unique allows multiple NULLs).
- The webhook loads the last 16 rows per user as `{"role","content"}` dicts (`HISTORY_LIMIT` in
  `app/conversation.py`).
- **Caution learned in testing**: clearing `orders` for a fresh test without also clearing
  `conversation_messages` leaves stale totals in the agent's context, which a model can
  incorrectly treat as a starting point for arithmetic. Clear both together when resetting test
  state: `DELETE FROM orders WHERE status = 'draft'; DELETE FROM conversation_messages;`

---

## 7. Not included yet (deferred per roadmap Phase 2/3)

- `restaurant` info table (name, hours, location)
- Order history / repeat-order features
- Any bundle-decomposition table for deals — deliberately avoided
- Any inventory/stock columns
- Per-brand drink rows (currently one row per size across all brands, e.g.
  "Coke/Pepsi/etc. 500ml") — deferred, pure data work whenever the menu is next touched

---

## Companion files

- `project-spec.md` — problem statement, architecture, roadmap
- `food-fusion-menu.md` — full menu content this schema is designed to hold
- `implementation-log.md` — bug fixes and reasoning behind the changes noted above
