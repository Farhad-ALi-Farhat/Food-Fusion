"""
Cart + order lifecycle. A customer's "cart" is simply their most recent
DRAFT order — no separate cart table needed (matches the flat, minimal
schema in db-schema.md).

State machine (project-spec.md §2.4):
  draft -> ready_for_confirmation -> pending_confirmation -> confirmed
                                            |-> rejected
                                            |-> modification_requested
For MVP, submit_order() jumps draft -> pending_confirmation directly;
ready_for_confirmation is available if a separate "review" step is
later split out from "submit."
"""

from sqlalchemy.orm import Session

from app.models import MenuItem, Order, OrderItem, OrderStatus
from app.tools.menu_tools import check_availability


class OrderToolError(Exception):
    """Raised for user-facing failures (item not found, invalid quantity, etc.)."""


def get_or_create_draft_order(db: Session, customer_id: int) -> Order:
    order = (
        db.query(Order)
        .filter(Order.customer_id == customer_id, Order.status == OrderStatus.draft)
        .order_by(Order.created_at.desc())
        .first()
    )
    if order is None:
        order = Order(customer_id=customer_id, status=OrderStatus.draft, total=0)
        db.add(order)
        db.commit()
        db.refresh(order)
    return order


def _recalculate_total(db: Session, order: Order) -> None:
    order.total = sum(line.subtotal for line in order.items)
    db.commit()


def add_to_cart(db: Session, customer_id: int, item_id: int, quantity: int, note: str | None = None) -> Order:
    item = db.query(MenuItem).filter(MenuItem.id == item_id, MenuItem.is_active.is_(True)).first()
    if item is None:
        raise OrderToolError("Item not found.")
    if quantity <= 0 or quantity % item.multiple_of != 0:
        raise OrderToolError(f"{item.name} must be ordered in multiples of {item.multiple_of}.")

    availability = check_availability(db, item_id)
    if not availability["available"]:
        raise OrderToolError(f"{item.name} is unavailable right now.")

    order = get_or_create_draft_order(db, customer_id)

    existing_line = next((l for l in order.items if l.menu_item_id == item_id), None)
    if existing_line:
        existing_line.quantity += quantity
        existing_line.subtotal = (existing_line.quantity // item.multiple_of) * float(existing_line.unit_price)
        if note:
            existing_line.note = note
    else:
        order.items.append(
            OrderItem(
                menu_item_id=item_id,
                quantity=quantity,
                unit_price=item.price,
                subtotal=(quantity // item.multiple_of) * float(item.price),
                note=note,
            )
        )
    db.commit()
    _recalculate_total(db, order)
    return order


def remove_from_cart(db: Session, customer_id: int, item_id: int) -> Order:
    order = get_or_create_draft_order(db, customer_id)
    order.items = [l for l in order.items if l.menu_item_id != item_id]
    db.commit()
    _recalculate_total(db, order)
    return order


def update_quantity(db: Session, customer_id: int, item_id: int, quantity: int) -> Order:
    order = get_or_create_draft_order(db, customer_id)
    line = next((l for l in order.items if l.menu_item_id == item_id), None)
    if line is None:
        raise OrderToolError("That item isn't in the cart.")
    if quantity <= 0:
        return remove_from_cart(db, customer_id, item_id)

    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    multiple_of = item.multiple_of if item else 1
    if quantity % multiple_of != 0:
        raise OrderToolError(f"That item must be ordered in multiples of {multiple_of}.")

    line.quantity = quantity
    line.subtotal = (quantity // multiple_of) * float(line.unit_price)
    db.commit()
    _recalculate_total(db, order)
    return order


def get_cart_summary(db: Session, customer_id: int) -> Order:
    return get_or_create_draft_order(db, customer_id)


def submit_order(db: Session, customer_id: int) -> Order:
    order = get_or_create_draft_order(db, customer_id)
    if not order.items:
        raise OrderToolError("Cart is empty — add items before submitting.")
    order.status = OrderStatus.pending_confirmation
    db.commit()
    db.refresh(order)
    return order
