# Food Fusion — Menu Reference

Source: `FoodFusion-Menu.pdf` (uploaded) + drinks/extras provided directly.
Location: Level 2, 346-B, Arfa Software Technology Park, Lahore.
Contact: 0300-949 6908 / 0302-415 3096.

> This file is a working reference for DB schema design (`menu_items` table etc.), not the final menu presented to customers. Edit freely.

---

## Breakfast

| Item | Price (Rs.) | Notes |
|---|---|---|
| Chicken Omelette | 280 | add Cheese (extra, +100) |
| Vegetable Omelette (Single) | 100 | add Cheese (extra, +100) |
| Vegetable Omelette (Double) | 180 | add Cheese (extra, +100) |
| Fried Egg (Double) | 200 | |
| Bread Slice | 40 | per slice |
| Paratha | 80 | |
| Aalu Paratha | 170 | |
| Mix Tea | 100 | |
| Coffee | 200 | |

## Lunch Offers

| Item | Price (Rs.) | Notes |
|---|---|---|
| Chicken Qorma | 250 | |
| Chicken Karahi | 250 | |
| Chicken Achari Masala | 300 | |
| Chicken Kabab Masala | 250 | |
| Chicken Boneless Handi | 400 | |
| Chicken Boneless Makhni Handi | 500 | |
| Chicken Jalfrezi | 350 | |
| Mix Vegetable / Daal | 200 | |
| Daal Chawal + Salad + Raita | 250 | |
| Chicken Qeema Naan | 250 | add Cheese (extra, +100) |
| Aalu Naan | 170 | |
| Roghni Naan | 80 | distinct from Roti/Rotti |

## Fast Food

| Item | Price (Rs.) | Notes |
|---|---|---|
| Chicken Patti Burger with Fries | 250 | |
| Zinger Burger with Fries | 300 | |
| Club Sandwich | 350 | |
| Bar.B.Q Sandwich | 400 | |
| Chicken Sandwich | 200 | |
| Chicken Shawarma | 200 | |
| Chicken Paratha Roll | 200 | |
| Shami Burger | 170 | |
| Plain Fries | 200 | |
| Masala Fries | 250 | |

## Chinese Corner

| Code | Item | Price (Rs.) | Notes |
|---|---|---|---|
| C1 | Chicken Manchurian | 390 | served with vegetable rice |
| C2 | Chicken Shashlik | 390 | served with vegetable rice |
| C3 | Chicken Chilli Dry | 450 | served with vegetable rice |
| C4 | Chicken Garlic | 390 | served with vegetable rice |
| C5 | Chicken Chowmein | 390 | |

## Lunch Special Platters (Deals)

Deals and Lunch Special Platters are the same category (a page break in the printed menu, not two categories). Each deal is its own flat `menu_item` — fixed bundle, fixed price.

**Rules for deals:**
- A customer can ask to remove a component of a deal (e.g. "no raita") — the price does **not** change.
- A customer can ask to add extra components (extra roti, extra roghni naan, extra seekh kabab, etc.) — priced using the **Deal Add-ons** price list below, added on top of the deal price.

| Code | Item | Price (Rs.) | Includes |
|---|---|---|---|
| Deal 1 | Chicken Tikka + Chicken Gola Kabab (3p) | 690 | Plain Pullao, Salad, Raita |
| Deal 2 | Chicken Boneless Handi + Chicken Seekh Kabab (1p) | 400 | Salad, Mint Sauce, 2 Roti |
| Deal 3 | Chicken Seekh Kabab (2p) | 350 | Salad, Mint Sauce, 1 Roti |
| Deal 4 | Chicken Malai Boti (4p) + Chicken Seekh Kabab (1p) | 500 | Salad, Mint Sauce, 1 Roti |
| Deal 5 | Chicken Grilled Boti (4p) + Chicken Seekh Kabab (1p) | 450 | Salad, Mint Sauce, 1 Roghni Naan |
| Deal 6 | Chicken Biryani/Pullao + Chicken Shami Kabab | 340 | Salad, Raita |
| Deal 7 | Chicken Biryani/Pullao | 290 | Salad, Raita |
| Deal 8 | Chicken Gola Kabab (6p) | 700 | Salad, Mint Sauce, 1 Roghni Naan |
| Deal 9 | Chicken Tawa Piece | 550 | Salad, Mint Sauce, 1 Roti |
| Deal 10 | Chicken Tikka | 450 | Salad, Mint Sauce, 1 Roti |
| Deal 11 | Chicken Seekh Kabab (4p) | 690 | Salad, Mint Sauce, 1 Roghni Naan |
| Deal 12 | Chicken Masala Rice + Chicken Tikka (1p) + Chicken Seekh Kabab (1p) | 690 | Salad, Mint Sauce |
| Deal 13 | Chicken Tikka + Chicken Seekh Kabab (1p) | 600 | Salad, Mint Sauce, 1 Roghni Naan |
| Deal 14 | Chicken Malai Boti (8p) | 850 | Salad, Raita, Roghni Naan |
| Deal 15 | Chicken Grilled Boti (8p) | 590 | Salad, Raita, Roghni Naan |

### Deal Add-ons (extra items added to a deal)

| Item | Price (Rs.) | Notes |
|---|---|---|
| Seekh Kabab | 180 | per piece |
| Chicken Tikka | 450 | per piece |
| Malai Boti | 450 | per 4 pieces — sold only in multiples of 4 |
| Grilled Boti | 350 | per 4 pieces — sold only in multiples of 4 |

*(more add-on items to be added later)*

## Drinks

| Item | Size | Price (Rs.) |
|---|---|---|
| Water | 500ml | 80 |
| Water | 1.5L | 150 |
| Coke / Pepsi / etc. | 350ml | 100 |
| Coke / Pepsi / etc. | 500ml | 150 |
| Coke / Pepsi / etc. | 1L | 180 |
| Coke / Pepsi / etc. | 1.5L | 250 |

*(modeled as separate menu_item rows per size, same pattern as Deals)*

## Extras

| Item | Price (Rs.) |
|---|---|
| Cheese | 100 |
| Disposable Glass / Cup / Plate | 10 each |
| Mint Sauce | 50 |
| Chapati / Roti | 25 |

---

## Notes for DB design

- **Categories** (final, flat list for the `category` column): `breakfast`, `lunch_offers`, `fast_food`, `chinese_corner`, `lunch_special_platters`, `drinks`, `extras`. No separate "deals" category — deals live under `lunch_special_platters`.
- **Cheese** is a normal `extras` menu item (Rs. 100), not a per-item modifier column — when a customer asks for "cheese omelette" or similar, the order is the base item + one Cheese extra, and the total is computed by summing both rows.
- **Deals are flat, fixed-price items.** Removing a bundled component never changes price. Adding a component uses the **Deal Add-ons** price list, added as an extra line item on top of the deal.
- **Roti = Rotti** (same item, spelling variant in the source PDF). **Roghni Naan is a distinct item.**
- **Drinks** are modeled as separate rows per size (e.g. "Water 500ml" and "Water 1.5L" are two distinct `menu_item` rows), same approach as everything else here — no separate variant/size table for MVP.
- Juice & Shakes removed from scope for now.
