---
name: Payment gateway
aka: [paygw, pay-gw]
service: payment-gateway
tables: [payments, payment_attempts]
owner: payments-team
updated: 2026-10-30
status: current
related: [orders-api, pay-504]
---
# Payment gateway

Takes card payments for checkout. Talks to the card processor over HTTPS and
records every attempt in `payment_attempts` and every settled charge in `payments`.

## Dependencies

- Card processor API (Stripe-like), timeout 8 s.
- `orders-api` calls `POST /pay` once per checkout.
- Redis for idempotency keys, 24 h expiry.

## Configuration

`PAYGW_POOL_SIZE` sets the outbound connection pool, 40 since October 2026.
`PAYGW_PROCESSOR_TIMEOUT_MS` is 8000.

## Incidents

### 2026-10 timeouts

The connection pool was exhausted under retry storms from `orders-api`. Raising
the pool alone did not help; the fix was jittered exponential backoff in the
orders-api client, released 2026-10-14. See [[2026-10-12-checkout-504s]].
