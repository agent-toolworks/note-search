---
name: Auth service
service: auth-service
tables: [sessions, users]
owner: identity-team
updated: 2026-09-05
related: [rotate-jwt-keys]
---
# Auth service

Issues JSON web tokens signed with a rotating key and stores refresh sessions in
`sessions`.

## Signing keys

Keys rotate every 90 days. The old key must stay valid for verification for
24 hours after a new one starts signing (the overlap window).
