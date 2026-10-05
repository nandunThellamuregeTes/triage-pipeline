"""Invoice assembly -- the only current caller of calculate_discount, so the
blast radius of a fix here is narrow."""

from .discount import calculate_discount


def build_invoice(quantity: int, unit_price: float) -> dict:
    subtotal = quantity * unit_price
    discount = calculate_discount(quantity, unit_price)
    total = subtotal - discount
    return {"subtotal": subtotal, "discount": discount, "total": total}
