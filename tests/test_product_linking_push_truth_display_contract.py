from pathlib import Path


GLOBAL_STATE = Path("static/js/bt38-global-state.js").read_text(encoding="utf-8")
PUSH = Path("services/governed_push_execution.py").read_text(encoding="utf-8")


def test_successful_group_push_is_not_relabelled_by_targeted_ui_refresh_failure():
    assert "marketplace push succeeded; targeted UI refresh could not be reconciled" in GLOBAL_STATE
    assert "contract.success || contract.ok" in GLOBAL_STATE
    assert "Number(contract.failed || 0) === 0" in GLOBAL_STATE
    assert "Number(contract.pushed || contract.ok_count || 0) > 0" in GLOBAL_STATE
    assert "throw error" in GLOBAL_STATE


def test_marketplace_push_result_remains_the_authority_for_success_counts():
    assert '"pushed": success_count' in PUSH
    assert '"failed": failed_count' in PUSH
    assert '"results": results' in PUSH
    assert 'response["error"] = failed_reasons[0]' in PUSH
