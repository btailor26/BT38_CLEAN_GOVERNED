from pathlib import Path

from services.governed_fbm_ready_landing_alignment import _align_ready_landing_html


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = ROOT / "services" / "governed_fbm_ready_landing_alignment.py"


def test_final_fbm_composition_keeps_pending_first_and_restores_rows():
    html = """
    <html><body>
      <table><tbody><tr class="fbm-order-row" data-order-id="1"></tr></tbody></table>
      <script>
      var labels={pending:'Pending',ready_dispatch:'Ready to dispatch'};
      var sessionDefaults={tab:'ready_dispatch',search:'',dirty:false};
      var active=(saved.tab&&labels[saved.tab]?saved.tab:'ready_dispatch');
      addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');
      addWorkflowButton(tabBar,'pending','Pending');
      </script>
    </body></html>
    """

    aligned = _align_ready_landing_html(html)

    assert "var sessionDefaults={tab:'pending',search:'',dirty:false};" in aligned
    assert "(saved.tab&&labels[saved.tab]?saved.tab:'pending')" in aligned
    assert aligned.index("addWorkflowButton(tabBar,'pending','Pending');") < aligned.index(
        "addWorkflowButton(tabBar,'ready_dispatch','Ready to dispatch');"
    )
    assert 'id="bt38FbmRowVisibilityAlignment"' in aligned
    assert "row.style.removeProperty('display');" in aligned


def test_pending_visibility_uses_existing_fbm_eligibility_without_new_reader():
    source = ALIGNMENT.read_text(encoding="utf-8")

    assert "def _restore_pending_fbm_visibility()" in source
    assert "page._workspace_fbm_eligible = aligned_visible_eligible" in source
    assert "page._is_fbm_eligible(row)" in source
    assert 'profile_channel in {"AFN", "FBA", "MCF"}' in source
    assert 'profile_channel in {"MFN", "FBM"}' in source
    assert 'return fulfillment in {"MFN", "FBM"}' in source

    visibility_block = source.split("def _fbm_row_visibility_script()", 1)[1].split(
        "def _align_ready_landing_html", 1
    )[0]
    assert "row.style.removeProperty('display');" in visibility_block
    assert "fetch(" not in visibility_block
    assert "window.location.reload" not in visibility_block
    assert "setInterval" not in visibility_block
    assert "new EventSource" not in visibility_block
