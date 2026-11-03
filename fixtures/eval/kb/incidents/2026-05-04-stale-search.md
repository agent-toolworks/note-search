# 2026-05-04: search showed old prices

After the Monday catalog import the storefront search showed last week's prices
for two hours while search-indexer caught up. Product pages were correct.
Conclusion: imports now pause the storefront price badge until the indexer lag
is under 5 minutes.
