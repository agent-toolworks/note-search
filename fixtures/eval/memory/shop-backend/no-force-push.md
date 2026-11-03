---
name: no-force-push
description: Never force-push shared branches in the shop repos
metadata:
  type: feedback
---

Never force-push `main` or a release branch. **Why:** the deploy bot builds from
the branch head and a force-push once shipped an old build.
