---
name: warehouse-sync
description: How the nightly warehouse sync reads orders
metadata:
  type: project
---

The nightly warehouse sync reads `orders_v2` from the replica at 02:00 UTC. It
fails when replica lag is over 10 minutes; rerun it with `dp sync orders --since yesterday`.
