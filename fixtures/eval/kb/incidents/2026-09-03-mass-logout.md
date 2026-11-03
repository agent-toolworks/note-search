---
name: Everyone signed out
service: auth-service
tables: [sessions]
updated: 2026-09-05
---
# 2026-09-03: users signed out

## What happened

At 09:00 UTC every web and app user was signed out at once.

## Cause

The JWT signing key was rotated without an overlap window, so tokens signed with
the previous key failed verification immediately.

## Conclusion

Keep the previous key valid for verification for 24 hours. The runbook now
says so.
