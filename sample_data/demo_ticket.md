# DEMO-101: Bulk discount missing for orders of exactly 10 units

**Priority:** Medium
**Labels:** billing, discount
**Reporter:** Jane Reporter
**Product:** billing-service

## Description

Customers who order exactly 10 units of a product are not receiving the
10% bulk discount, even though Sales confirmed the discount should apply
starting at 10 units (inclusive). Orders of 11+ units get the discount
correctly -- only exactly-10 orders are affected.

The relevant code is likely in `calculate_discount` in the billing package,
possibly an off-by-one in the quantity comparison (`quantity > 10` versus
`quantity >= 10`).

## Comments

- Confirmed with Sales: the discount policy is ">= 10 units", not "> 10 units".
- Reproduced on staging: ordering exactly 10 units of SKU-4821 shows a $0 discount on the invoice preview.
