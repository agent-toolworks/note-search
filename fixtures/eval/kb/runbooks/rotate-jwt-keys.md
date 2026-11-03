---
name: Runbook - rotate JWT signing keys
service: auth-service
updated: 2026-09-05
status: current
related: [auth-service]
---
# Rotate JWT signing keys

1. Generate the new key in the secrets manager.
2. Add it as a verification key and wait 10 minutes.
3. Switch signing to the new key.
4. Keep the old key for verification for 24 hours (overlap window), then remove it.
