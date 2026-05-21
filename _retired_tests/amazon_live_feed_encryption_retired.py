#!/usr/bin/env python3
"""
Standalone test to verify Amazon feed encryption works correctly
"""
import os
from amazon_service import AmazonService

# Create service instance
service = AmazonService(
    store_id=27,
    refresh_token=os.environ.get('AMAZON_REFRESH_TOKEN'),
    client_id=os.environ.get('AMAZON_LWA_CLIENT_ID'),
    client_secret=os.environ.get('AMAZON_LWA_CLIENT_SECRET'),
    seller_id=os.environ.get('AMAZON_SELLER_ID'),
    marketplace_id='A1F83G8C2ARO7P'  # UK marketplace
)

# Create a simple test feed
test_sku = 'FBA-CR-RV-URU-200ml'
test_quantity = 19

print(f"Testing Amazon feed encryption...")
print(f"SKU: {test_sku}, Quantity: {test_quantity}")
print("=" * 60)

# Push quantity update
success, message = service.push_quantity_update(test_sku, test_quantity)

print(f"\nResult: {'SUCCESS' if success else 'FAILED'}")
print(f"Message: {message}")

if success:
    print("\n✅ Encryption fix is WORKING!")
else:
    print("\n❌ Encryption fix FAILED!")
