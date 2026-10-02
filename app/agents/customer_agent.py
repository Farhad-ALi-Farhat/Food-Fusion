"""
Wires the plain-Python tool functions in app/tools/ into LangChain tools
for a single customer conversation, then builds a tool-calling agent
backed by Gemini (project-spec.md's Groq-vs-Gemini decision: Gemini, for now).
"""

import re
import logging

logger = logging.getLogger(__name__)

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from sqlalchemy.orm import Session

from app.agents.resilience import build_resilience_middleware
from app.agents.prompts import CUSTOMER_SYSTEM_PROMPT
from app.config import settings
from app.tools import menu_tools, order_tools


def build_customer_tools(db: Session, customer_id: int) -> list:
    """Returns LangChain tools bound to this specific db session + customer,
    so the LLM only ever sees simple string/int arguments — it never
    touches the session or another customer's data."""

    @tool
    def search_menu(query: str) -> list[dict]:
        """Search the menu by item name or category. Pass ONLY the item name (e.g. 'malai boti'), never quantities or filler words like 'add' or 'pieces'. Returns matching items with id, name, price."""
        items = menu_tools.search_menu(db, query)
        return [{"id": i.id, "name": i.name, "price": float(i.price), "category": i.category} for i in items]

    @tool
    def get_item_details(item_id: int) -> dict:
        """Get full details (name, description, price) for a specific menu item id."""
        item = menu_tools.get_item_details(db, item_id)
        if item is None:
            return {"error": "not found"}
        return {"id": item.id, "name": item.name, "description": item.description, "price": float(item.price)}

    @tool
    def check_availability(item_id: int) -> dict:
        """Check whether a menu item is available today, including today's overrides."""
        return menu_tools.check_availability(db, item_id)

    @tool
    def get_daily_specials() -> list[dict]:
        """List today's specials, if any."""
        items = menu_tools.get_daily_specials(db)
        return [{"id": i.id, "name": i.name, "price": float(i.price)} for i in items]

    @tool
    def add_to_cart(item_id: int, quantity: int, note: str = "") -> dict:
        """Add a quantity of a menu item to the cart. Use 'note' for a customization
        request on this specific line (e.g. 'leg piece instead of breast', 'no onions',
        'extra spicy') — only when the customer asks for something not already a
        separate menu item."""
        try:
            order = order_tools.add_to_cart(db, customer_id, item_id, quantity, note=note or None)
            return {"order_id": order.id, "total": float(order.total)}
        except order_tools.OrderToolError as e:
            return {"error": str(e)}
    
    @tool
    def remove_from_cart(item_id: int) -> dict:
        """Remove a menu item entirely from the customer's cart."""
        order = order_tools.remove_from_cart(db, customer_id, item_id)
        return {"order_id": order.id, "total": float(order.total)}

    @tool
    def update_quantity(item_id: int, quantity: int) -> dict:
        """Change the quantity of an item already in the cart."""
        try:
            order = order_tools.update_quantity(db, customer_id, item_id, quantity)
            return {"order_id": order.id, "total": float(order.total)}
        except order_tools.OrderToolError as e:
            return {"error": str(e)}

    @tool
    def get_cart_summary() -> dict:
        """Get the current cart contents and total."""
        order = order_tools.get_cart_summary(db, customer_id)
        return {
            "order_id": order.id,
            "lines": [
                {"name": l.menu_item.name, "quantity": l.quantity, "subtotal": float(l.subtotal), "note": l.note}
                for l in order.items
            ],
            "total": float(order.total),
        }

    @tool
    def submit_order() -> dict:
        """Submit the current cart as an order for restaurant confirmation. Only call this after the customer has explicitly confirmed the reviewed total."""
        try:
            order = order_tools.submit_order(db, customer_id)
            return {"order_id": order.id, "status": order.status.value, "total": float(order.total)}
        except order_tools.OrderToolError as e:
            return {"error": str(e)}

    return [
        search_menu,
        get_item_details,
        check_availability,
        get_daily_specials,
        add_to_cart,
        remove_from_cart,
        update_quantity,
        get_cart_summary,
        submit_order,
    ]

_llm_cache: ChatGroq | None = None

def get_customer_llm() -> ChatGroq:
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = ChatGroq(
            model="openai/gpt-oss-20b",
            api_key=settings.groq_api_key,
            temperature=0.2,
            timeout=20,
            max_retries=1,
        )
    return _llm_cache


def build_customer_agent(db: Session, customer_id: int):
    tools = build_customer_tools(db, customer_id)
    llm = get_customer_llm()
    return create_agent(model=llm, tools=tools, system_prompt=CUSTOMER_SYSTEM_PROMPT, middleware=build_resilience_middleware())

def looks_degenerate(text: str) -> bool:
    """Catch repetition-loop garbage before it reaches a customer."""
    if len(text) > 2000:  # a real WhatsApp reply should never need this much
        return True
    ellipsis_count = text.count("...") + text.count("…")
    if ellipsis_count > 15:
        return True
    return False

def _safe_cart_fallback(db: Session, customer_id: int) -> str:
    """The model's reply was unusable — don't tell the customer to retry,
    since a mutating tool call may have already succeeded. Show the real
    cart state instead, built directly from the DB, not from the model."""
    order = order_tools.get_cart_summary(db, customer_id)
    if not order.items:
        return "Sorry, I hit a glitch there. Your cart is currently empty — what would you like to add?"
    lines = [f"- {l.quantity}x {l.menu_item.name} — Rs. {float(l.subtotal):.0f}" for l in order.items]
    return (
        "Sorry, I hit a glitch with that last reply, but here's your current cart:\n\n"
        + "\n".join(lines)
        + f"\n\nTotal: Rs. {float(order.total):.0f}\n\nLet me know if anything here needs fixing."
    )

def run_customer_turn(db: Session, customer_id: int, message: str, chat_history: list | None = None) -> str:
    """Entry point called from the webhook for a customer message.

    chat_history is a list of {"role": ..., "content": ...} dicts loaded
    from conversation_messages by the webhook.
    """
    agent = build_customer_agent(db, customer_id)
    messages = list(chat_history or [])
    messages.append({"role": "user", "content": message})
    result = agent.invoke({"messages": messages})
    reply = result["messages"][-1].text
    if not reply or not reply.strip() or looks_degenerate(reply):
        logger.error("Agent returned a bad reply for customer_id=%s: %r", customer_id, (reply or "")[:200])
        reply = _safe_cart_fallback(db, customer_id)
    return reply