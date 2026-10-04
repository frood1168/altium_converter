"""constraints.json -> master rules TOML + net class TOML, on a small synthetic result."""

import sys
import tomllib
from pathlib import Path

import pytest

from altium_converter import emit

MIL = 0.0254
OVERBAR = "ST_R" + chr(92) + "S" + chr(92) + "T"   # an Altium overbar name: literal backslashes


def stat(rule_mm, n=100):
    return {"n": n, "min": rule_mm, "rule_mm": rule_mm}


def constraints():
    def layer(w, g, cm):
        return {"width_mm": w, "gap_mm": g, "length_mm": cm, "coupled_mm": cm}
    return {
        "schema": "altium_converter.constraints/1", "source": r"C:\x\B.kicad_pcb",
        "layers": ["TOP", "L3", "L5"], "bga_fields": ["U1"],
        "nets": ["A_P", "A_N", "B_P", "B_N", "GND", OVERBAR], "components": ["U1", "R1"],
        "diffpairs": {"pairs": [
            # A: 0.1 mm / 0.15 mm on L3, a different geometry on L5, an uncoupled stub on TOP
            {"pair": "A_{P,N}", "p": "A_P", "n": "A_N", "layers": {
                "L3": layer(0.1, 0.15, 50), "L5": layer(0.12, 0.2, 40), "TOP": {"length_mm": 3, "coupled_mm": 0.0}}},
            # B: same geometry as A on L3, and 1 mm of coupling on L5 (below the floor)
            {"pair": "B_{P,N}", "p": "B_P", "n": "B_N", "layers": {
                "L3": layer(0.1, 0.15, 30), "L5": layer(0.5, 0.5, 1.0)}}]},
        "widths": {"TOP": {"single_ended": [
            {"width_mm": 0.15, "length_mm": 900, "share": 0.9, "nets": 3, "nets_with_planes": 0, "examples": []},
            {"width_mm": 0.4, "length_mm": 80, "share": 0.08, "nets": 1, "nets_with_planes": 1, "examples": []},
            {"width_mm": 0.9, "length_mm": 5, "share": 0.005, "nets": 1, "nets_with_planes": 0, "examples": []}]}},
        "vias": {"styles": [
            {"span": "through", "drill_mm": 0.25, "outer_pad_mm": 0.5, "count": 500},
            {"span": "through", "drill_mm": 0.3, "outer_pad_mm": 0.6, "count": 30},
            {"span": "through", "drill_mm": 0.1, "outer_pad_mm": 0.2, "count": 3},
            {"span": "TOP-L3", "drill_mm": 0.1, "outer_pad_mm": 0.2, "count": 90}]},
        "clearances": {"search_mm": 0.5, "board": {
            "Track -> Track (open)": stat(0.1), "Track -> Via (open)": stat(0.12),
            "Track -> Track (BGA)": stat(0.09), "SMD Pad -> SMD Pad (open)": stat(0.08, 3),
            "Via hole -> Track (open)": stat(0.05)}},
        "planes": {"antipad": {"L3 | Via (open) from drill": stat(0.3), "L3 | Via (open) from pad": stat(0.1),
                               "L3 | Via (BGA) from drill": stat(0.2)},
                   "connections": {"L3 | Via": {"direct": 5}}},
    }


@pytest.fixture
def em():
    return emit.build(constraints())


def rule(em, name):
    return next(r for r in em.rules if r.name == name)


def test_one_class_per_observed_layer_geometry(em):
    # A and B share L3's geometry; A alone has L5's; B's 1 mm L5 run and A's uncoupled TOP are dropped
    assert em.classes == {
        "NC_DP_L3_W3p9_G5p9": ["A_N", "A_P", "B_N", "B_P"],
        "NC_DP_L5_W4p7_G7p9": ["A_N", "A_P"],
    }


def test_pair_rules_are_scoped_to_their_layer(em):
    r = rule(em, "Width_DP_L3_W3p9_G5p9")
    assert r.scope1 == "InNetClass('NC_DP_L3_W3p9_G5p9') And OnLayer('L3')"
    assert rule(em, "DiffPairsRouting_DP_L5_W4p7_G7p9").scope1.endswith("OnLayer('L5')")


def test_pair_values_carry_a_band_around_the_observation(em):
    v = rule(em, "Width_DP_L3_W3p9_G5p9").values
    assert v["preferred_width"] == emit.mil_str(0.1)
    assert v["minimum_width"] == emit.mil_str(0.1 * 0.98) and v["maximum_width"] == emit.mil_str(0.1 * 1.02)
    d = rule(em, "DiffPairsRouting_DP_L3_W3p9_G5p9").values
    assert d["preferred_gap"] == emit.mil_str(0.15)
    assert d["impedance_profile_driven"] == "false"


def test_pair_rules_outrank_layer_and_catch_all_width_rules(em):
    width = [r for r in em.rules if r.kind == "Width"]
    assert [r.priority for r in width] == list(range(1, len(width) + 1))
    names = [r.name for r in width]
    assert names.index("Width_DP_L3_W3p9_G5p9") < names.index("Width_TOP") < names.index("Width")


def test_single_ended_width_ignores_thin_evidence(em):
    v = rule(em, "Width_TOP").values
    assert v["preferred_width"] == emit.mil_str(0.15)
    assert v["maximum_width"] == emit.mil_str(0.4)          # the 0.5 % track is not a rule


def test_clearance_bga_outranks_generic_and_ignores_holes_and_thin_samples(em):
    bga, gen = rule(em, "Clearance_BGA"), rule(em, "Clearance")
    assert bga.priority < gen.priority and bga.scope1 == "InComponent('U1')"
    assert bga.values["gap"] == emit.mil_str(0.09)
    # the 0.05 hole row and the 3-sample 0.08 pad row would both undercut 0.1
    assert gen.values["gap"] == emit.mil_str(0.1)


def test_vias_use_through_styles_with_enough_samples(em):
    v = rule(em, "RoutingVias").values
    assert v["preferred_hole_width"] == emit.mil_str(0.25)
    assert v["minimum_hole_width"] == emit.mil_str(0.25)      # the 3-via style is noise
    assert v["maximum_hole_width"] == emit.mil_str(0.3)
    assert any("blind/buried" in n for n in em.notes)


def test_plane_clearance_is_hole_referenced_open_board(em):
    assert rule(em, "PlaneClearance").values["clearance"] == emit.mil_str(0.3)
    assert any("PolygonConnect" in n for n in em.notes)


def test_missing_sections_are_reported_not_invented():
    em = emit.build({"layers": ["TOP"], "bga_fields": []})
    assert em.rules == [] and em.classes == {}
    assert len(em.notes) >= 4


def test_master_toml_round_trips(em):
    d = tomllib.loads(emit.master_toml(em, "B.kicad_pcb", "2026-10-03"))
    assert d["meta"]["rule_count"] == len(em.rules) == len(d["rule"])
    assert d["board"]["namespaces"] == "enumerated" and OVERBAR in d["board"]["nets"]
    assert {c["name"] for c in d["board"]["class"]} == set(em.classes)
    bool_rule = next(r for r in d["rule"] if r["kind"] == "DiffPairsRouting")
    assert bool_rule["values"]["impedance_profile_driven"] is False


def test_netclass_toml_round_trips_literal_backslashes():
    em = emit.build(constraints())
    em.classes["NC_X"] = [OVERBAR, "it's"]
    d = tomllib.loads(emit.netclass_toml(em, "B.kicad_pcb"))
    assert d["class"]["NC_X"]["members"] == [OVERBAR, "it's"]
    assert d["board"]["nets"][-1] == OVERBAR and d["schema"] == 1


def test_no_board_table_without_namespaces():
    c = constraints()
    del c["nets"], c["components"]
    em = emit.build(c)
    assert "board" not in tomllib.loads(emit.master_toml(em, "B", "d"))
    assert "board" not in tomllib.loads(emit.netclass_toml(em, "B"))


def test_scopes_parse_with_altium_drcs_own_parser(em):
    sib = Path(__file__).resolve().parents[2] / "altium_drc" / "src" / "py"
    if not sib.is_dir():
        pytest.skip("altium_drc not checked out beside this repo")
    sys.path.insert(0, str(sib))
    try:
        from altium_drc.scopeq import parse_scope
    finally:
        sys.path.remove(str(sib))
    for r in em.rules:
        for s in (r.scope1, r.scope2):
            assert not parse_scope(s).errors, (r.name, s)
