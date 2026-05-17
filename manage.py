#!/usr/bin/env python3
"""Management CLI with retired marketplace commands fail-closed."""

import argparse
import sys

from app import app
from old_path_shutdown import disabled_response


def _print_disabled(command: str) -> int:
    result = disabled_response(f"manage:{command}")
    print(result["error"])
    print("OLD_SYNC_DISABLED=true")
    print("MARKETPLACE_EXECUTION_DISABLED=true")
    print("GOVERNED_PATH_REQUIRED=true")
    return 2


def main():
    """Main entry point for management commands."""
    parser = argparse.ArgumentParser(description="Inventory System Management Commands")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    price_parser = subparsers.add_parser("fix_ebay_prices", help="DISABLED: old eBay price remediation")
    price_parser.add_argument("--min", type=float, default=0.99)
    price_parser.add_argument("--dry-run", action="store_true")
    price_parser.add_argument("--apply", action="store_true")
    price_parser.add_argument("--store", type=str)
    price_parser.add_argument("--batch-size", type=int, default=20)

    amazon_parser = subparsers.add_parser("update_amazon_qty", help="DISABLED: old Amazon quantity update")
    amazon_parser.add_argument("sku", type=str)
    amazon_parser.add_argument("quantity", type=int)
    amazon_parser.add_argument("--store", type=str, required=True)
    amazon_parser.add_argument("--marketplace", type=str, default="A1F83G8C2ARO7P")

    ebay_parser = subparsers.add_parser("ebay_preflight", help="DISABLED: old eBay API preflight")
    ebay_parser.add_argument("item_ids", type=str, nargs="+")
    ebay_parser.add_argument("--store", type=str, required=True)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    with app.app_context():
        if args.command in {"fix_ebay_prices", "update_amazon_qty", "ebay_preflight"}:
            sys.exit(_print_disabled(args.command))

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
