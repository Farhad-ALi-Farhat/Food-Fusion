"""
Menu-related tool functions. Plain Python, no LLM here — these are what
get wrapped as LangChain tools in app/agents/*.py.

Per db-schema.md: is_active is a permanent on/off switch, while
availability_override provides today-only exceptions on top of that.
"""

import difflib
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import AvailabilityOverride, MenuItem

def _fuzzy_score(query: str, name: str) -> float:
    q = query.lower().strip()
    n = name.lower()
    best = difflib.SequenceMatcher(None, q, n).ratio()
    words = n.split()
    q_len = max(len(q.split()), 1)
    for i in range(max(len(words) - q_len + 1, 1)):
        window = " ".join(words[i : i + q_len])
        best = max(best, difflib.SequenceMatcher(None, q, window).ratio())
    return best


def search_menu(db: Session, query: str, limit: int = 5) -> list[MenuItem]:
    """Substring search on name/category; falls back to fuzzy name matching
    when that finds nothing (handles spelling variants like 'rogni naan')."""
    query = query.strip()
    if not query:
        return []
    pattern = f"%{query}%"
    results = (
        db.query(MenuItem)
        .filter(MenuItem.is_active.is_(True))
        .filter(or_(MenuItem.name.ilike(pattern), MenuItem.category.ilike(pattern)))
        .limit(limit)
        .all()
    )
    if results:
        return results

    items = db.query(MenuItem).filter(MenuItem.is_active.is_(True)).all()
    scored = sorted(((i, _fuzzy_score(query, i.name)) for i in items), key=lambda p: p[1], reverse=True)
    return [i for i, score in scored if score >= 0.72][:limit]


def get_item_details(db: Session, item_id: int) -> MenuItem | None:
    return db.query(MenuItem).filter(MenuItem.id == item_id, MenuItem.is_active.is_(True)).first()


def check_availability(db: Session, item_id: int, on_date: date | None = None) -> dict:
    """
    Returns {"available": bool, "item": str, "price": float, "reason": str|None}.
    Override (if present for today) wins; otherwise default to available
    as long as the item is active — matches the restaurant's actual
    workflow of "assume available unless marked otherwise."
    """
    on_date = on_date or date.today()
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if item is None or not item.is_active:
        return {"available": False, "item": None, "price": None, "reason": "Item not found"}

    override = (
        db.query(AvailabilityOverride)
        .filter(AvailabilityOverride.menu_item_id == item_id, AvailabilityOverride.override_date == on_date)
        .first()
    )
    if override is not None:
        return {
            "available": override.is_available,
            "item": item.name,
            "price": float(item.price),
            "reason": override.reason,
        }

    return {"available": True, "item": item.name, "price": float(item.price), "reason": None}


def get_daily_specials(db: Session, on_date: date | None = None) -> list[MenuItem]:
    """Items with an availability_override marking them available today
    AND whose category is deal/special in nature — for MVP we treat any
    item with an active 'available=True' override for today as a special
    worth surfacing, since permanent-menu items don't normally need one."""
    on_date = on_date or date.today()
    return (
        db.query(MenuItem)
        .join(AvailabilityOverride, AvailabilityOverride.menu_item_id == MenuItem.id)
        .filter(AvailabilityOverride.override_date == on_date, AvailabilityOverride.is_available.is_(True))
        .all()
    )
