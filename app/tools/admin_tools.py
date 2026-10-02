"""
Admin-only tool functions. Never exposed to the customer agent — the
router (app/routers/webhook.py) decides which tool set an incoming
sender gets, based on User.role, never based on anything the LLM decides.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.models import AvailabilityOverride, MenuItem, Order, OrderStatus


class AdminToolError(Exception):
    pass


def _find_item(db: Session, item_id: int) -> MenuItem:
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if item is None:
        raise AdminToolError("Item not found.")
    return item


def mark_item_unavailable(
    db: Session, item_id: int, admin_id: int | None = None, reason: str | None = None, on_date: date | None = None
) -> AvailabilityOverride:
    item = _find_item(db, item_id)
    on_date = on_date or date.today()

    override = (
        db.query(AvailabilityOverride)
        .filter(AvailabilityOverride.menu_item_id == item.id, AvailabilityOverride.override_date == on_date)
        .first()
    )
    if override:
        override.is_available = False
        override.reason = reason
        override.created_by = admin_id
    else:
        override = AvailabilityOverride(
            menu_item_id=item.id, override_date=on_date, is_available=False, reason=reason, created_by=admin_id
        )
        db.add(override)
    db.commit()
    db.refresh(override)
    return override


def mark_item_available(db: Session, item_id: int, admin_id: int | None = None, on_date: date | None = None) -> None:
    item = _find_item(db, item_id)
    on_date = on_date or date.today()
    db.query(AvailabilityOverride).filter(
        AvailabilityOverride.menu_item_id == item.id, AvailabilityOverride.override_date == on_date
    ).delete()
    db.commit()


def add_daily_special(
    db: Session, name: str, price: float, admin_id: int | None = None, on_date: date | None = None
) -> MenuItem:
    on_date = on_date or date.today()

    item = db.query(MenuItem).filter(MenuItem.name.ilike(name), MenuItem.category == "lunch_special_platters").first()
    if item is None:
        item = MenuItem(name=name, category="lunch_special_platters", price=price, is_active=True)
        db.add(item)
        db.commit()
        db.refresh(item)
    else:
        item.price = price  # allow re-activating a past special at a possibly new price

    override = AvailabilityOverride(
        menu_item_id=item.id, override_date=on_date, is_available=True, reason="daily special", created_by=admin_id
    )
    db.add(override)
    db.commit()
    return item


def get_pending_orders(db: Session) -> list[Order]:
    return db.query(Order).filter(Order.status == OrderStatus.pending_confirmation).order_by(Order.created_at).all()

def confirm_order(db: Session, order_id: int, admin_id: int | None = None) -> Order:
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        raise AdminToolError("Order not found.")
    if order.status != OrderStatus.pending_confirmation:
        raise AdminToolError(f"Order #{order.id} is {order.status.value}, not pending confirmation.")
    order.status = OrderStatus.confirmed
    order.confirmed_by = admin_id
    db.commit()
    db.refresh(order)
    return order

def reject_order(db: Session, order_id: int, admin_id: int | None = None) -> Order:
    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        raise AdminToolError("Order not found.")
    if order.status != OrderStatus.pending_confirmation:
        raise AdminToolError(f"Order #{order.id} is {order.status.value}, not pending confirmation.")
    order.status = OrderStatus.rejected
    order.confirmed_by = admin_id
    db.commit()
    db.refresh(order)
    return order