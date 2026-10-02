"""
Same pattern as customer_agent.py, but for admin senders only. The
webhook router is what guarantees only a verified admin's messages reach
this module — never the LLM's own judgment.
"""
import re
import logging

logger = logging.getLogger(__name__)

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from sqlalchemy.orm import Session

from app.agents.resilience import build_resilience_middleware
from app.agents.prompts import ADMIN_SYSTEM_PROMPT
from app.config import settings
from app.tools import admin_tools, menu_tools


def build_admin_tools(db: Session, admin_id: int) -> list:
    @tool
    def find_menu_item(query: str) -> list[dict]:
        """Look up a menu item by name before acting on it, to resolve exact spelling/id."""
        items = menu_tools.search_menu(db, query)
        return [{"id": i.id, "name": i.name, "price": float(i.price)} for i in items]

    @tool
    def mark_item_unavailable(item_id: int, reason: str = "") -> dict:
        """Mark a menu item unavailable for today only."""
        try:
            override = admin_tools.mark_item_unavailable(db, item_id, admin_id=admin_id, reason=reason or None)
            return {"item_id": override.menu_item_id, "available": override.is_available}
        except admin_tools.AdminToolError as e:
            return {"error": str(e)}

    @tool
    def mark_item_available(item_id: int) -> dict:
        """Clear today's unavailable override for a menu item, making it available again."""
        try:
            admin_tools.mark_item_available(db, item_id, admin_id=admin_id)
            return {"item_id": item_id, "available": True}
        except admin_tools.AdminToolError as e:
            return {"error": str(e)}

    @tool
    def add_daily_special(name: str, price: float) -> dict:
        """Add (or re-activate) a same-day special at the given price."""
        item = admin_tools.add_daily_special(db, name=name, price=price, admin_id=admin_id)
        return {"item_id": item.id, "name": item.name, "price": float(item.price)}

    @tool
    def get_pending_orders() -> list[dict]:
        """List orders awaiting confirmation, with their items and the customer's number."""
        orders = admin_tools.get_pending_orders(db)
        return [
            {
                "order_id": o.id,
                "customer_number": o.customer.whatsapp_number,
                "items": [
                    f"{l.quantity}x {l.menu_item.name}" + (f" (note: {l.note})" if l.note else "")
                    for l in o.items
                ],
                "total": float(o.total),
            }
            for o in orders
        ]

    @tool
    def reject_order(order_id: int) -> dict:
        """Reject a pending order and notify the customer it couldn't be processed."""
        from app.routers.webhook import send_whatsapp_message

        try:
            order = admin_tools.reject_order(db, order_id, admin_id=admin_id)
        except admin_tools.AdminToolError as e:
            return {"error": str(e)}

        send_whatsapp_message(
            order.customer.whatsapp_number,
            f"Sorry, your Food Fusion order #{order.id} couldn't be processed. Please contact the restaurant.",
        )
        return {"order_id": order.id, "status": order.status.value}


    @tool
    def confirm_order(order_id: int, ready_in_minutes: int = 0) -> dict:
        """Confirm a pending order and automatically notify the customer on WhatsApp.
        ready_in_minutes is optional (0 = don't mention a time)."""
        from app.routers.webhook import send_whatsapp_message  # lazy import avoids a circular import

        try:
            order = admin_tools.confirm_order(db, order_id, admin_id=admin_id)
        except admin_tools.AdminToolError as e:
            return {"error": str(e)}

        text = f"Your Food Fusion order #{order.id} is confirmed ✅ Total: Rs. {float(order.total):.0f}."
        if ready_in_minutes > 0:
            text += f" It'll be ready in about {ready_in_minutes} minutes."
        send_whatsapp_message(order.customer.whatsapp_number, text)
        return {"order_id": order.id, "status": order.status.value}

    return [find_menu_item, mark_item_unavailable, mark_item_available, add_daily_special, get_pending_orders, confirm_order, reject_order]

_admin_llm_cache: ChatGroq | None = None

def get_admin_llm() -> ChatGroq:
    global _admin_llm_cache
    if _admin_llm_cache is None:
        _admin_llm_cache = ChatGroq(
            model="openai/gpt-oss-120b",
            api_key=settings.groq_api_key,
            temperature=0.1,
            timeout=20,
            max_retries=1,
        )
    return _admin_llm_cache

def build_admin_agent(db: Session, admin_id: int):
    tools = build_admin_tools(db, admin_id)
    llm = get_admin_llm()
    return create_agent(model=llm, tools=tools, system_prompt=ADMIN_SYSTEM_PROMPT, middleware=build_resilience_middleware())


def looks_degenerate(text: str) -> bool:
    """Catch repetition-loop garbage before it reaches a customer."""
    if len(text) > 2000:  # a real WhatsApp reply should never need this much
        return True
    ellipsis_count = text.count("...") + text.count("…")
    if ellipsis_count > 15:
        return True
    return False

def run_admin_turn(db: Session, admin_id: int, message: str, chat_history: list | None = None) -> str:
    agent = build_admin_agent(db, admin_id)
    messages = list(chat_history or [])
    messages.append({"role": "user", "content": message})
    result = agent.invoke({"messages": messages})
    reply = result["messages"][-1].text
    if not reply or not reply.strip() or looks_degenerate(reply):
        logger.error("Agent returned a bad reply (empty or degenerate) for admin_id=%s: %r", admin_id, reply[:200])
        reply = "Sorry, I couldn't process that — could you try again?"
    return reply