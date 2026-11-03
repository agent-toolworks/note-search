---
name: Search indexer
service: search-indexer
tables: [products]
owner: discovery-team
updated: 2026-05-10
---
# Search indexer

Reads product changes from the catalog and writes them to the search cluster.
A full reindex takes about 40 minutes.

## Lag

The indexer falls behind during bulk catalog imports; the storefront then
shows prices and titles from before the import until it catches up.
