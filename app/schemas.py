from pydantic import BaseModel


class MenuItemOut(BaseModel):
    id: int
    name: str
    description: str | None
    category: str
    price: float
    multiple_of: int

    model_config = {"from_attributes": True}


class AvailabilityOut(BaseModel):
    item_id: int
    name: str
    available: bool
    price: float
    reason: str | None = None


class CartLineOut(BaseModel):
    menu_item_id: int
    name: str
    quantity: int
    unit_price: float
    subtotal: float


class CartSummaryOut(BaseModel):
    order_id: int
    lines: list[CartLineOut]
    total: float


class SubmitOrderOut(BaseModel):
    order_id: int
    status: str
    total: float
