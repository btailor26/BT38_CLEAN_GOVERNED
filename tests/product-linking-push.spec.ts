import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const template = readFileSync('templates/product_linking.html', 'utf8');
const propagation = readFileSync('governed_group_propagation_routes.py', 'utf8');

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

test('governed group shortcut resolves one selected Warehouse target quantity', async () => {
  expect(propagation).toContain('target_quantity = None');
  expect(propagation).toContain('getattr(requested_stock, "sellable_quantity", 0) or 0');
  expect(propagation).toContain('if target_quantity is not None');
  expect(propagation).toContain('int(target_quantity)');
});

test('group shortcut preserves current Product Linking membership and FBA read-only guard', async () => {
  expect(propagation).toContain('MarketplaceListing.master_product_group_id');
  expect(propagation).toContain('== group_id');
  expect(propagation).toContain('if classification["is_fba"]:');
  expect(propagation).toContain('"read_only"');
});

test('group push response identifies affected records for targeted UI handoff', async () => {
  expect(propagation).toContain('"affected_group_ids": [int(group_id)]');
  expect(propagation).toContain('"affected_listing_ids": affected_listing_ids');
  expect(propagation).toContain('"affected_warehouse_stock_ids": affected_warehouse_stock_ids');
  expect(propagation).toContain('"target_quantity": target_quantity');
});
