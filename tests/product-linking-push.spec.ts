import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const template = readFileSync('templates/product_linking.html', 'utf8');
const propagation = readFileSync('governed_group_propagation_routes.py', 'utf8');
const pushService = readFileSync('services/governed_push_execution.py', 'utf8');

test('Product Linking declares display quantity before visible Push button', async () => {
  const renderStart = template.indexOf('function renderWarehouseProducts(products)');
  const renderEnd = template.indexOf('function renderProductLinkingPagination()', renderStart);
  expect(renderStart).toBeGreaterThanOrEqual(0);
  expect(renderEnd).toBeGreaterThan(renderStart);

  const renderBlock = template.slice(renderStart, renderEnd);
  const declaration = renderBlock.indexOf('const displayStockQuantity =');
  const visiblePush = renderBlock.indexOf('const visiblePushBtn =');

  expect(declaration).toBeGreaterThanOrEqual(0);
  expect(visiblePush).toBeGreaterThanOrEqual(0);
  expect(declaration).toBeLessThan(visiblePush);
});

test('Product Linking shortcut posts group and permanent Warehouse identity', async () => {
  expect(template).toContain('/governed/actions/groups/${encodeURIComponent(groupId)}/push');
  expect(template).toContain('warehouse_stock_id: warehouseId');
  expect(template).toContain("source: 'product_linking_warehouse_shortcut'");
});

test('manual shortcut delegates to the single governed group service', async () => {
  expect(propagation).toContain('from services.governed_push_execution import push_group_listings');
  expect(propagation).toContain('result = push_group_listings(');
  expect(propagation).toContain('authority_warehouse_stock_id=requested_warehouse_stock_id');
  expect(propagation).not.toContain('submit_governed_marketplace_action');
});

test('shared group service resolves one Warehouse target quantity', async () => {
  expect(pushService).toContain('authority_warehouse_stock_id');
  expect(pushService).toContain('target_quantity = int(getattr(authority_stock, "sellable_quantity", 0) or 0)');
  expect(pushService).toContain('stock.available_quantity = int(target_quantity + reserved + allocated)');
  expect(pushService).toContain('"one_shared_group_quantity": True');
});

test('shared group service preserves current group and skips FBA before write', async () => {
  expect(pushService).toContain('MarketplaceListing.master_product_group_id == group_id');
  expect(pushService).toContain('if _is_fba_listing(listing):');
  expect(pushService).toContain('"push_status": "read_only"');
});

test('group push response identifies affected records for targeted UI handoff', async () => {
  expect(pushService).toContain('"affected_group_ids": [group_id]');
  expect(pushService).toContain('"affected_listing_ids"');
  expect(pushService).toContain('"affected_warehouse_stock_ids": warehouse_ids');
  expect(pushService).toContain('"target_quantity": target_quantity');
});
