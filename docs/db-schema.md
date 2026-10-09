# Food Fusion — Database Schema

PostgreSQL. Matches the architecture and menu decisions in `project-spec.md` and
`food-fusion-menu.md`.

Design principle carried over from the architecture doc: **the LLM never writes to this DB
directly** — every write goes through a backend tool function.

**Note on naming:** menu items originally seeded as "...Botti" were renamed to "...Boti"
(single t) across the DB, `scripts/seed_menu.py`, and `food-fusion-menu.md`.

**Note on connection setup:** the app connects via the pure-Python `pg8000` driver, not
`psycopg2` (switched to avoid a Windows Smart App Control DLL block — see
`implementation-log.md` §9). This doesn't change anything in this file — it's a connection-layer
detail in `database.py`, not a schema change. SSL is explicitly configured with
`verify_mode = ssl.CERT_NONE` (encrypted, not certificate-verified), matching the security
posture the previous `sslmode=require` setup already had.

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
- **Role vs `ADMIN_NUMBERS`**: originally `role` was only derived from `ADMIN_NUMBERS` when the row
  was created, so changing the env var later left stale roles (hit twice in testing). Recommended
  fix: `_get_or_create_user` recomputes the expected role on every message and updates the row, so
  `ADMIN_NUMBERS` is the single source of truth (also demotes removed admins; overrides manual DB
  edits). See `implementation-log.md` §17 and confirm it's in the code. Until then, fix a stale row
  with a manual `UPDATE users SET role = ...`.

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
  Validated and correctly priced by both `add_to_cart` and `update_quantity` — subtotal is
  `(quantity // multiple_of) * price`.
- **`is_active`**: permanent on/off switch, separate from daily `availability_override`.
- **Note on "Deal" items**: Deal 1–15 are regular, fixed-price `menu_items` rows like anything
  else — this needed an explicit prompt clarification after both LLM models in use misread
  "Deal 10" as a discount request rather than an item name. No schema implication, purely an
  LLM-prompt fact; see `agent-prompts.md`.

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

- `confirmed_by` is actively written by both `confirm_order` (status → `confirmed`) and
  `reject_order` (status → `rejected`) — the same column records whoever actioned the order either
  way.
- `status = 'confirmed'` has been reached and verified live (order #19), via both the admin's
  typed path and the Approve button path.

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

- **`note` is now actively used** — threaded through `add_to_cart(item_id, quantity, note=...)`,
  surfaced in `get_cart_summary`, `get_pending_orders`, and the admin notification template. Used
  for customization requests ("leg piece instead of breast", "no onions") that don't map to a
  separate menu item or change price. Confirmed live.
- `ON DELETE CASCADE` on `order_id` means deleting an `orders` row (e.g.
  `DELETE FROM orders WHERE status = 'draft'`) cleans up its `order_items` automatically.

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

- `wa_message_id` is Meta's inbound message id — used both to ignore webhook retries
  (`already_processed()`) and confirmed, via direct query, to have zero duplicate rows after real
  traffic.
- The webhook loads the last 16 rows per user as `{"role","content"}` dicts (`HISTORY_LIMIT` in
  `app/conversation.py`).
- Button taps (Approve/Decline) are also logged here, as
  `"[button tap: APPROVE_19]"` / the resulting reply — same `append_turn` call as any other turn,
  so button-driven actions are visible in conversation history too.
- **This table also drives the admin notification path.** `_admin_window_open` reads the admin's
  latest `role = 'user'` row to decide between free-form buttons and the (billable) template.
  Wiping the table makes the app believe the admin's WhatsApp window is closed even if Meta's is
  still open — after any wipe, send a message from the admin number before placing a test order.
- **Caution learned in testing**: clearing `orders` for a fresh test without also clearing
  `conversation_messages` leaves stale totals in the agent's context. Clear both together when
  resetting test state: `DELETE FROM orders WHERE status = 'draft'; DELETE FROM conversation_messages;`

---

## 7. Not included yet (deferred per roadmap Phase 2/3)

- `restaurant` info table (name, hours, location)
- Order history / repeat-order features
- Any bundle-decomposition table for deals — deliberately avoided
- Any inventory/stock columns
- Per-brand drink rows (currently one row per size across all brands) — deferred

---

## Companion files

- `project-spec.md` — problem statement, architecture, roadmap
- `food-fusion-menu.md` — full menu content this schema is designed to hold
- `implementation-log.md` — bug fixes and reasoning behind the changes noted above
