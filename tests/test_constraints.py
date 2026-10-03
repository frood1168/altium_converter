"""Constraint statistics and measurements on a tiny synthetic board model."""

import numpy as np
import pytest
import shapely

from altium_converter import constraints
from altium_converter.constraints import diffpairs, planes, stats
from altium_converter.model import Board


def model():
    nets = ["", "D_P", "D_N", "SIG", "GND"]
    tracks = [
        # a 0.1 mm pair at 0.15 mm gap (centre-lines 0.25 apart), 10 mm long, on L1
        ["L1", 1, 0.1, 0, 0, 10, 0],
        ["L1", 2, 0.1, 0, 0.25, 10, 0.25],
        # a single-ended track 0.2 mm wide, 0.3 mm edge-to-edge from the pair's N trace
        ["L1", 3, 0.2, 0, 0.65, 10, 0.65],
    ]
    return {"schema": "altium_converter.board/1", "source": "synthetic",
            "layers": [{"id": "F.Cu", "name": "L1", "copper": True, "order": 0},
                       {"id": "B.Cu", "name": "L2", "copper": True, "order": 1}],
            "nets": nets, "tracks": tracks, "arcs": [], "pads": [], "zones": [], "outline": [],
            # via pad r=0.2 at y=1.1: 1.1 - 0.65 - 0.1 - 0.2 = 0.15 mm from the SIG track's edge
            "vias": [{"net": 4, "x": 5, "y": 1.1, "drill": 0.2, "layers": {"L1": 0.4, "L2": 0.4}}]}


def test_find_pairs():
    nets = ["", "A_P", "A_N", "B_P", "CLKP", "CLKN", "X+", "X-", "VCC_P"]
    got = diffpairs.find_pairs(nets, set(nets))
    assert ("A_{P,N}", "A_P", "A_N") in got
    assert ("CLK{P,N}", "CLKP", "CLKN") in got
    assert ("X{P,N}", "X+", "X-") in got
    assert not any(p[1] == "B_P" for p in got)        # no partner
    assert diffpairs.family("PCIE_TX12_C_{P,N}") == "PCIE_TX#_C_{P,N}"


def test_rule_estimate_finds_the_pile_up_not_the_outlier():
    values = np.r_[[0.02], np.full(200, 0.1016), np.linspace(0.12, 1.0, 1800)]
    assert stats.rule_estimate(values) == pytest.approx(0.1, abs=0.003)


def test_diffpair_width_and_gap():
    res = diffpairs.analyse(Board.from_model(model()), progress=lambda *_: None)
    assert len(res["pairs"]) == 1
    l1 = res["pairs"][0]["layers"]["L1"]
    assert l1["width_mm"] == pytest.approx(0.1)
    assert l1["gap_mm"] == pytest.approx(0.15, abs=0.003)
    assert l1["coupled_mm"] == pytest.approx(10, abs=0.2)
    assert res["profiles"][0]["layer"] == "L1"


@pytest.mark.parametrize("name, fill, want", [
    # a 0.2 mm-radius via at the origin in four kinds of plane
    ("direct", lambda: shapely.box(-2, -2, 2, 2).difference(shapely.Point(0, 0).buffer(0.2)), "direct"),
    ("isolated", lambda: shapely.box(-2, -2, 2, 2).difference(shapely.Point(0, 0).buffer(0.35)), "isolated"),
    ("relief", lambda: shapely.box(-2, -2, 2, 2).difference(shapely.Point(0, 0).buffer(0.35)).union(
        shapely.union_all([shapely.box(-0.4, -0.05, 0.4, 0.05), shapely.box(-0.05, -0.4, 0.05, 0.4)]))
        .difference(shapely.Point(0, 0).buffer(0.2)), "relief"),
    ("partial", lambda: shapely.box(0, -2, 2, 2).difference(shapely.Point(0, 0).buffer(0.2)), "partial"),
])
def test_plane_connection_style(name, fill, want):
    style, spokes, gap = planes._style(fill(), 0.0, 0.0, 0.2)
    assert style == want
    if want == "relief":
        assert spokes == 4 and gap == pytest.approx(0.15, abs=0.015)
    if want == "isolated":
        assert gap == pytest.approx(0.15, abs=0.015)


def test_clearance_track_to_track():
    res = constraints.run(model(), sections=("diffpairs", "clearances"), progress=lambda *_: None)
    layer = res["clearances"]["layers"]["L1"]
    tt = layer["Track -> Track (open)"]
    assert tt["min"] == pytest.approx(0.15, abs=1e-6)          # the pair gap
    assert layer["Track -> Via (open)"]["min"] == pytest.approx(0.15, abs=1e-6)
    assert layer["Via -> Track (open)"]["n"] == 1
