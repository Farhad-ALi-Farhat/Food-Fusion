"""
Meta WhatsApp Cloud API webhook.

Flow: incoming message -> 200 ack immediately -> background: look up
sender role in `users` (never LLM judgment) -> load chat history ->
customer or admin agent -> persist turn -> send reply via Cloud API.
"""

from __future__ import annotations

import logging

import hmac, hashlib
import httpx
from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session, joinedload

from app.config import normalize_whatsapp_number, settings
from app.conversation import already_processed, append_turn, load_history
from app.database import SessionLocal
from app.models import Order, OrderItem, OrderStatus, User, UserRole

logger = logging.getLogger(__name__)

WHATSAPP_TEXT_LIMIT = 4096
NON_TEXT_FALLBACK = "I can currently only read text messages — could you type that?"

router = APIRouter()


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))

@router.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Meta's one-time verification handshake when you register the webhook URL."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Verification failed", status_code=403)


@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    logger.info("=== WEBHOOK HIT ===")
    raw_body = await request.body()
    logger.info("RAW PAYLOAD: %s", raw_body.decode("utf-8"))
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(raw_body, signature, settings.whatsapp_app_secret):
        logger.warning("Rejected webhook POST with invalid signature")
        return PlainTextResponse("Invalid signature", status_code=403)

    payload = await request.json()

    try:
        statuses = payload["entry"][0]["changes"][0]["value"].get("statuses")
        if statuses:
            for s in statuses:
                logger.info(
                    "WA delivery status id=%s status=%s recipient=%s errors=%s",
                    s.get("id"), s.get("status"), s.get("recipient_id"), s.get("errors"),
                )
    except (KeyError, IndexError, TypeError):
        pass    
    
    inbound = _extract_inbound(payload)
    if inbound is None:
        return {"status": "ignored"}

    background_tasks.add_task(_process_inbound, inbound)
    return {"status": "ok"}


def _extract_inbound(payload: dict) -> dict | None:
    """Parse Meta payload. None for status callbacks / unusable body."""
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")
        if not messages:
            return None
        msg = messages[0]
        sender = normalize_whatsapp_number(msg["from"])
        wa_message_id = msg.get("id")
        if msg.get("type") == "text":
            return {"kind": "text", "sender": sender, "text": msg["text"]["body"], "wa_message_id": wa_message_id}
        if msg.get("type") == "button":
            return {"kind": "button", "sender": sender, "payload": msg["button"]["payload"], "wa_message_id": wa_message_id}
        return {"kind": "non_text", "sender": sender, "text": None, "wa_message_id": wa_message_id}
    except (KeyError, IndexError, TypeError):
        return None

def _handle_admin_button(db: Session, user: User, payload: str) -> str:
    if user.role != UserRole.admin:
        logger.warning("Non-admin user_id=%s attempted a button action: %s", user.id, payload)
        return "You're not authorized to do that."
    try:
        action, order_id_str = payload.split("_", 1)
        order_id = int(order_id_str)
    except (ValueError, AttributeError):
        return "Sorry, I didn't recognize that action."

    from app.tools import admin_tools

    if action == "APPROVE":
        try:
            order = admin_tools.confirm_order(db, order_id, admin_id=user.id)
        except admin_tools.AdminToolError as e:
            return str(e)
        send_whatsapp_message(
            order.customer.whatsapp_number,
            f"Your Food Fusion order #{order.id} is confirmed ✅ Total: Rs. {float(order.total):.0f}.",
        )
        return f"Order #{order.id} confirmed and customer notified."

    if action == "DECLINE":
        try:
            order = admin_tools.reject_order(db, order_id, admin_id=user.id)
        except admin_tools.AdminToolError as e:
            return str(e)
        send_whatsapp_message(
            order.customer.whatsapp_number,
            f"Sorry, your Food Fusion order #{order.id} couldn't be processed. Please contact the restaurant.",
        )
        return f"Order #{order.id} declined and customer notified."

    return "Unknown action."


def _process_inbound(inbound: dict) -> None:
    db = SessionLocal()
    try:
        wa_message_id = inbound.get("wa_message_id")
        if already_processed(db, wa_message_id):
            logger.info("Skipping duplicate WhatsApp message id=%s", wa_message_id)
            return

        sender = inbound["sender"]
        if inbound["kind"] == "button":
            user = _get_or_create_user(db, sender)
            reply = _handle_admin_button(db, user, inbound["payload"])
            append_turn(db, user.id, f"[button tap: {inbound['payload']}]", reply, wa_message_id=wa_message_id)
            send_whatsapp_message(sender, reply)
            return

        if inbound["kind"] != "text":
            user = _get_or_create_user(db, sender)
            append_turn(db, user.id, "[non-text message]", NON_TEXT_FALLBACK, wa_message_id=wa_message_id)
            send_whatsapp_message(sender, NON_TEXT_FALLBACK)
            return

        user = _get_or_create_user(db, sender)
        pending_before = _pending_order_ids(db, user.id)
        history = load_history(db, user.id)

        try:
            reply = _route_message(db, user, inbound["text"], history)
        except Exception:
            logger.exception("Agent turn failed for user_id=%s", user.id)
            reply = "Sorry, I hit a glitch — could you try that again?"

        db.expire_all()
        append_turn(db, user.id, inbound["text"], reply, wa_message_id=wa_message_id)
        send_whatsapp_message(sender, reply)

        if user.role == UserRole.customer:
            newly_pending = _pending_order_ids(db, user.id) - pending_before
            for order_id in newly_pending:
                _notify_admins_of_order(db, user, order_id)
    except Exception:
        logger.exception("Failed processing inbound WhatsApp message")
    finally:
        db.close()


def _get_or_create_user(db: Session, whatsapp_number: str) -> User:
    number = normalize_whatsapp_number(whatsapp_number)
    user = db.query(User).filter(User.whatsapp_number == number).first()
    if user is None:
        role = UserRole.admin if number in settings.admin_number_set else UserRole.customer
        user = User(whatsapp_number=number, role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _route_message(db: Session, user: User, message_text: str, chat_history: list) -> str:
    if user.role == UserRole.admin:
        from app.agents.admin_agent import run_admin_turn

        return run_admin_turn(db, user.id, message_text, chat_history=chat_history)
    from app.agents.customer_agent import run_customer_turn

    return run_customer_turn(db, user.id, message_text, chat_history=chat_history)


def _pending_order_ids(db: Session, customer_id: int) -> set[int]:
    rows = (
        db.query(Order.id)
        .filter(Order.customer_id == customer_id, Order.status == OrderStatus.pending_confirmation)
        .all()
    )
    return {row[0] for row in rows}


def format_staff_order_notice(order: Order, customer: User) -> str:
    lines = [f"New order #{order.id} from {customer.whatsapp_number}"]
    for line in order.items:
        name = line.menu_item.name if line.menu_item is not None else f"item {line.menu_item_id}"
        lines.append(f"- {line.quantity}x {name} — Rs. {float(line.subtotal):.0f}")
    lines.append(f"Total: Rs. {float(order.total):.0f}")
    lines.append("Status: pending confirmation")
    return "\n".join(lines)


def _notify_admins_of_order(db: Session, customer: User, order_id: int) -> None:
    order = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.menu_item))
        .filter(Order.id == order_id)
        .first()
    )
    if order is None:
        return
    items_summary = ", ".join(
        f"{l.quantity}x {l.menu_item.name}" + (f" ({l.note})" if l.note else "")
        for l in order.items
    )
    for admin_number in settings.admin_number_set:
        send_order_confirmation_template(admin_number, order, items_summary)


def send_order_confirmation_template(to_number: str, order: Order, items_summary: str) -> None:
    to_number = normalize_whatsapp_number(to_number)
    customer_display_number = "+" + order.customer.whatsapp_number
    url = f"https://graph.facebook.com/v20.0/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "template",
        "template": {
            "name": "order_pending_confirmation",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(order.id)},
                        {"type": "text", "text": items_summary},
                        {"type": "text", "text": f"{float(order.total):.0f}"},
                        {"type": "text", "text": customer_display_number},
                    ],
                },
                {
                    "type": "button", "sub_type": "quick_reply", "index": "0",
                    "parameters": [{"type": "payload", "payload": f"APPROVE_{order.id}"}],
                },
                {
                    "type": "button", "sub_type": "quick_reply", "index": "1",
                    "parameters": [{"type": "payload", "payload": f"DECLINE_{order.id}"}],
                },
            ],
        },
    }
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code >= 400:
            logger.error("Template send failed to=%s status=%s body=%s", to_number, response.status_code, response.text)
        else:
            logger.info("Template send ok to=%s status=%s", to_number, response.status_code)
    except httpx.HTTPError:
        logger.exception("Template send HTTP error to=%s", to_number)

def send_whatsapp_message(to_number: str, text: str) -> None:
    """Send a text message via WhatsApp Cloud API. Truncates to 4096 chars."""
    to_number = normalize_whatsapp_number(to_number)
    body_text = text if len(text) <= WHATSAPP_TEXT_LIMIT else text[: WHATSAPP_TEXT_LIMIT - 1] + "…"
    url = f"https://graph.facebook.com/v20.0/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": body_text}}
    for attempt in range(2):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code >= 400:
                logger.error("WhatsApp send failed to=%s status=%s body=%s", to_number, response.status_code, response.text)
            else:
                logger.info("WhatsApp send ok to=%s status=%s", to_number, response.status_code)
            return
        except httpx.HTTPError:
            logger.exception("WhatsApp send HTTP error to=%s (attempt %s)", to_number, attempt + 1)
    logger.error("WhatsApp send permanently failed after retries to=%s", to_number)
