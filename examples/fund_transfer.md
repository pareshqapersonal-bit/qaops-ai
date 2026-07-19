# Feature: Fund Transfer

## Description
Authenticated banking customers can transfer money from their own account to a
saved beneficiary.

## Requirements
1. The customer selects a source account, a saved beneficiary, and enters an
   amount and an optional remark.
2. Transfers require the available balance to cover the amount; otherwise the
   transfer is rejected with an "Insufficient balance" message.
3. The amount must be greater than zero.
4. Each customer has a daily transfer limit of 100000 rupees across all
   accounts.
5. Before execution, the customer confirms the transfer by entering a
   one-time password sent to their registered mobile number.
6. On success, both a debit entry on the source account and a transaction
   reference number are shown, and an SMS confirmation is sent.
7. Transfers to beneficiaries added within the last 24 hours are capped at
   25000 rupees.
8. A failed or rejected transfer must not debit the source account.

## Notes
- New beneficiaries are added in a separate flow.
- Large transfers may require additional verification.
