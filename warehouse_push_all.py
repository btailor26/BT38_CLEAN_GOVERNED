#!/usr/bin/env python3
"""Retired warehouse direct push script.

Direct warehouse-to-marketplace push execution is disabled until the governed
runtime gate, dispatcher, executor, and marketplace adapter path is implemented.
"""

from old_path_shutdown import disabled_response

OLD_SYNC_DISABLED = True
MARKETPLACE_EXECUTION_DISABLED = True
GOVERNED_PATH_REQUIRED = True


def main() -> int:
    result = disabled_response("warehouse_push_all")
    print(result["error"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
