---
name: payments
tables: [payments]
service: payment-gateway
owner: payments-team
updated: 2026-03-11
related: [adr-007-minor-units]
---
# payments

One row per settled charge. Amounts are stored in minor units (cents) in
`amount_minor`, with the ISO currency in `currency`.
