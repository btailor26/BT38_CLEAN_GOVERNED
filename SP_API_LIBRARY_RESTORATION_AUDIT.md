# SP-API Library Restoration Audit

## Proven Findings

The repository already contains a previous working SP-API implementation path.

Key findings:

- `python_amazon_sp_api` package already installed
- previous ListingsItems implementation exists in backup
- governed PATCH architecture already wired
- LWA runtime binding already wired
- seller_id and marketplace_id flows already wired

## Critical Discovery

The following backup contains the previous SP-API implementation:

```text
_bt38_backups/amazon_service.before-fba-guard.20260516-144347.bak
```

Contains:

```python
from sp_api.api import CatalogItems, Inventories, Feeds, ListingsItems
from sp_api.base import Marketplaces
```

## Important Conclusion

The `python-amazon-sp-api` library may already handle SigV4 internally.

This means the next governed implementation should:

- restore governed execution using the SP-API library
- avoid raw manual signing implementation where possible
- preserve governed runtime gate
- preserve no-worker/no-scheduler architecture
- preserve FBA read-only rules

## Next Implementation Direction

Target path:

```text
Governed execution
→ Amazon adapter
→ amazon_service.py
→ python-amazon-sp-api ListingsItems PATCH
```

No legacy sync restoration.
No workers.
No schedulers.
No queue restoration.
