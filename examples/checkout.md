# Feature: Checkout

## Description
Customers with at least one item in their cart can complete a purchase through
a three-step checkout: shipping address, payment, review and place order.

## Requirements
1. Checkout is available only to customers whose cart contains at least one
   in-stock item.
2. Step 1 collects a shipping address: full name, street, city, postal code,
   and phone number. All fields are mandatory.
3. Step 2 offers payment by card or cash on delivery. Cash on delivery is
   available only for orders below 5000 rupees.
4. Step 3 shows an order summary: items, quantities, prices, shipping cost,
   and total. The customer places the order from this step.
5. On successful order placement, the customer sees an order confirmation
   number and receives a confirmation email.
6. Order totals above 500 rupees qualify for free shipping; otherwise a flat
   shipping fee applies.
7. Applying a valid coupon code recalculates the total immediately.

## Notes
- If an item goes out of stock during checkout, the customer should be
  informed.
- Guest checkout may be added later.
