"""Discount calculation for the billing service."""


def calculate_discount(quantity: int, unit_price: float) -> float:
    """Return the bulk discount amount for an order.

    Policy: orders of 10 units or more receive a 10% discount on the
    subtotal.
    """
    subtotal = quantity * unit_price
    if quantity > 10:  # BUG: policy is ">= 10 units", this excludes exactly 10
        return subtotal * 0.10
    return 0.0
