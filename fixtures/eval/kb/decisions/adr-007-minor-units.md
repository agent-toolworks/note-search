---
name: ADR-007 money in minor units
tables: [payments, orders_v2]
updated: 2025-12-01
---
# ADR-007: store money as integers in minor units

Floating point lost cents in refunds. All amounts are integers in the
currency's minor unit, with the currency code beside them.
