from pathlib import Path


def test_deploy_workflow_checks_db_before_and_after_rollout():
    workflow = Path('.github/workflows/deploy-fly.yml').read_text(encoding='utf-8')
    pre = 'Verify candidate image DB contract against production Neon'
    deploy = 'Deploy exact GitHub commit with Fly remote builder'
    post = 'Verify deployed image DB contract against production Neon'

    assert pre in workflow
    assert post in workflow
    assert workflow.index(pre) < workflow.index(deploy) < workflow.index(post)
    assert 'scripts/verify_production_db_contract.py' in workflow


def test_db_contract_is_targeted_not_full_schema_equality():
    source = Path('scripts/verify_production_db_contract.py').read_text(encoding='utf-8')

    assert 'REQUIRED_COLUMNS' in source
    assert 'Extra tables/columns do not fail the release' in source
    assert 'DB_CONTRACT_WARNING' in source
    assert 'marketplace_orders' in source
    assert 'fbm_shipments' in source
    assert 'warehouse_stock' in source
