"""
One-off seed script: loads the finalized menu (food-fusion-menu.md) into
the menu_items table. Idempotent — safe to re-run; matches on
(name, category) and updates the price if the row already exists rather
than creating duplicates.

Usage:
    python -m scripts.seed_menu
"""

from app.database import Base, SessionLocal, engine
from app.models import MenuItem

# (name, category, price, multiple_of)
# multiple_of only set where >1; defaults to 1 otherwise.
MENU_ITEMS: list[tuple[str, str, float, int]] = [
    # --- Breakfast ---
    ("Chicken Omelette", "breakfast", 280, 1),
    ("Vegetable Omelette (Single)", "breakfast", 100, 1),
    ("Vegetable Omelette (Double)", "breakfast", 180, 1),
    ("Fried Egg (Double)", "breakfast", 200, 1),
    ("Bread Slice", "breakfast", 40, 1),
    ("Paratha", "breakfast", 80, 1),
    ("Aalu Paratha", "breakfast", 170, 1),
    ("Mix Tea", "breakfast", 100, 1),
    ("Coffee", "breakfast", 200, 1),
    # --- Lunch Offers ---
    ("Chicken Qorma", "lunch_offers", 250, 1),
    ("Chicken Karahi", "lunch_offers", 250, 1),
    ("Chicken Achari Masala", "lunch_offers", 300, 1),
    ("Chicken Kabab Masala", "lunch_offers", 250, 1),
    ("Chicken Boneless Handi", "lunch_offers", 400, 1),
    ("Chicken Boneless Makhni Handi", "lunch_offers", 500, 1),
    ("Chicken Jalfrezi", "lunch_offers", 350, 1),
    ("Mix Vegetable / Daal", "lunch_offers", 200, 1),
    ("Daal Chawal + Salad + Raita", "lunch_offers", 250, 1),
    ("Chicken Qeema Naan", "lunch_offers", 250, 1),
    ("Aalu Naan", "lunch_offers", 170, 1),
    ("Roghni Naan", "lunch_offers", 80, 1),
    # --- Fast Food ---
    ("Chicken Patti Burger with Fries", "fast_food", 250, 1),
    ("Zinger Burger with Fries", "fast_food", 300, 1),
    ("Club Sandwich", "fast_food", 350, 1),
    ("Bar.B.Q Sandwich", "fast_food", 400, 1),
    ("Chicken Sandwich", "fast_food", 200, 1),
    ("Chicken Shawarma", "fast_food", 200, 1),
    ("Chicken Paratha Roll", "fast_food", 200, 1),
    ("Shami Burger", "fast_food", 170, 1),
    ("Plain Fries", "fast_food", 200, 1),
    ("Masala Fries", "fast_food", 250, 1),
    # --- Chinese Corner ---
    ("Chicken Manchurian (with Vegetable Rice)", "chinese_corner", 390, 1),
    ("Chicken Shashlik (with Vegetable Rice)", "chinese_corner", 390, 1),
    ("Chicken Chilli Dry (with Vegetable Rice)", "chinese_corner", 450, 1),
    ("Chicken Garlic (with Vegetable Rice)", "chinese_corner", 390, 1),
    ("Chicken Chowmein", "chinese_corner", 390, 1),
    # --- Lunch Special Platters (Deals 1-15) ---
    ("Deal 1: Chicken Tikka + Gola Kabab (3p), Pullao, Salad, Raita", "lunch_special_platters", 690, 1),
    ("Deal 2: Chicken Boneless Handi + Seekh Kabab (1p), Salad, Mint Sauce, 2 Roti", "lunch_special_platters", 400, 1),
    ("Deal 3: Chicken Seekh Kabab (2p), Salad, Mint Sauce, 1 Roti", "lunch_special_platters", 350, 1),
    ("Deal 4: Chicken Malai Boti (4p) + Seekh Kabab (1p), Salad, Mint Sauce, 1 Roti", "lunch_special_platters", 500, 1),
    ("Deal 5: Chicken Grilled Boti (4p) + Seekh Kabab (1p), Salad, Mint Sauce, 1 Roghni Naan", "lunch_special_platters", 450, 1),
    ("Deal 6: Chicken Biryani/Pullao + Shami Kabab, Salad, Raita", "lunch_special_platters", 340, 1),
    ("Deal 7: Chicken Biryani/Pullao, Salad, Raita", "lunch_special_platters", 290, 1),
    ("Deal 8: Chicken Gola Kabab (6p), Salad, Mint Sauce, 1 Roghni Naan", "lunch_special_platters", 700, 1),
    ("Deal 9: Chicken Tawa Piece, Salad, Mint Sauce, 1 Roti", "lunch_special_platters", 550, 1),
    ("Deal 10: Chicken Tikka, Salad, Mint Sauce, 1 Roti", "lunch_special_platters", 450, 1),
    ("Deal 11: Chicken Seekh Kabab (4p), Salad, Mint Sauce, 1 Roghni Naan", "lunch_special_platters", 690, 1),
    ("Deal 12: Chicken Masala Rice + Tikka (1p) + Seekh Kabab (1p), Salad, Mint Sauce", "lunch_special_platters", 690, 1),
    ("Deal 13: Chicken Tikka + Seekh Kabab (1p), Salad, Mint Sauce, 1 Roghni Naan", "lunch_special_platters", 600, 1),
    ("Deal 14: Chicken Malai Boti (8p), Salad, Raita, Roghni Naan", "lunch_special_platters", 850, 1),
    ("Deal 15: Chicken Grilled Boti (8p), Salad, Raita, Roghni Naan", "lunch_special_platters", 590, 1),
    # --- Drinks (one row per size) ---
    ("Water 500ml", "drinks", 80, 1),
    ("Water 1.5L", "drinks", 150, 1),
    ("Coke/Pepsi/etc. 350ml", "drinks", 100, 1),
    ("Coke/Pepsi/etc. 500ml", "drinks", 150, 1),
    ("Coke/Pepsi/etc. 1L", "drinks", 180, 1),
    ("Coke/Pepsi/etc. 1.5L", "drinks", 250, 1),
    # --- Extras ---
    ("Cheese", "extras", 100, 1),
    ("Disposable Glass/Cup/Plate", "extras", 10, 1),
    ("Mint Sauce", "extras", 50, 1),
    ("Chapati/Roti", "extras", 25, 1),
    # --- Deal Add-ons (extra pieces added on top of a deal) ---
    ("Seekh Kabab (add-on)", "deal_addons", 180, 1),
    ("Chicken Tikka (add-on)", "deal_addons", 450, 1),
    ("Malai Boti (add-on)", "deal_addons", 450, 4),
    ("Grilled Boti (add-on)", "deal_addons", 350, 4),
]


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    created, updated = 0, 0
    try:
        for name, category, price, multiple_of in MENU_ITEMS:
            existing = db.query(MenuItem).filter(MenuItem.name == name, MenuItem.category == category).first()
            if existing:
                if float(existing.price) != float(price) or existing.multiple_of != multiple_of:
                    existing.price = price
                    existing.multiple_of = multiple_of
                    updated += 1
            else:
                db.add(MenuItem(name=name, category=category, price=price, multiple_of=multiple_of, is_active=True))
                created += 1
        db.commit()
    finally:
        db.close()
    print(f"Seed complete: {created} items created, {updated} items updated, {len(MENU_ITEMS)} total in list.")


if __name__ == "__main__":
    seed()
