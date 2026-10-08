"""kicad_text transforms on small synthetic KiCad 10 boards."""

import collections
import math
from pathlib import Path

import pytest

from altium_converter import kicad_text as kt

T = "\t"

V10 = "\n".join([
    "(kicad_pcb",
    f'{T}(version 20260206)',
    f'{T}(generator "pcbnew")',
    f'{T}(generator_version "10.0")',
    f"{T}(layers",
    f'{T}{T}(0 "F.Cu" signal "TOP")',
    f'{T}{T}(4 "In1.Cu" signal "02_GND")',
    f'{T}{T}(2 "B.Cu" signal "BOTTOM")',
    f'{T}{T}(5 "F.SilkS" user "Silk Top")',
    f'{T}{T}(25 "Edge.Cuts" user)',
    f"{T})",
    f"{T}(setup",
    f"{T}{T}(capping no)",
    f"{T}{T}(covering",
    f"{T}{T}{T}(front no)",
    f"{T}{T})",
    f"{T})",
    f"{T}(footprint \"\"",
    f'{T}{T}(uuid "fp-1")',
    f'{T}{T}(property "Reference" "U7"',
    f"{T}{T})",
    f'{T}{T}(property "Reference" "${{REFERENCE}}"',
    f"{T}{T})",
    f"{T}{T}(duplicate_pad_numbers_are_jumpers no)",
    f'{T}{T}(pad "1" smd rect',
    f'{T}{T}{T}(net "SIG_A")',
    f"{T}{T})",
    f"{T})",
    f"{T}(gr_line",
    f"{T}{T}(start 0 0)",
    f"{T}{T}(end 1 0)",
    f"{T}{T}(stroke",
    f"{T}{T}{T}(width 0.1)",
    f"{T}{T}{T}(type default)",
    f"{T}{T})",
    f'{T}{T}(layer "F.SilkS")',
    f"{T})",
    f"{T}(segment",
    f"{T}{T}(start 0 0)",
    f"{T}{T}(end 1 0)",
    f"{T}{T}(width 0.2)",
    f'{T}{T}(layer "F.Cu")',
    f'{T}{T}(net "SIG_A")',
    f'{T}{T}(uuid "seg-1")',
    f"{T})",
    f"{T}(arc",                                   # collinear: must become a segment
    f"{T}{T}(start 0 0)",
    f"{T}{T}(mid 0.00005 0)",
    f"{T}{T}(end 0.0001 0)",
    f"{T}{T}(width 0.1)",
    f'{T}{T}(layer "F.Cu")',
    f'{T}{T}(net "SIG_A")',
    f'{T}{T}(uuid "arc-1")',
    f"{T})",
    f"{T}(arc",                                   # a real arc: must stay
    f"{T}{T}(start 0 0)",
    f"{T}{T}(mid 1 1)",
    f"{T}{T}(end 2 0)",
    f"{T}{T}(width 0.1)",
    f'{T}{T}(layer "F.Cu")',
    f'{T}{T}(net "SIG_A")',
    f'{T}{T}(uuid "arc-2")',
    f"{T})",
    f"{T}(via",
    f"{T}{T}(at 1 0)",
    f'{T}{T}(net "")',
    f'{T}{T}(uuid "via-1")',
    f"{T})",
    f"{T}(gr_poly",
    f"{T}{T}(pts",
    f"{T}{T}{T}(xy 0 0) (xy 1 0) (xy 1 1)",
    f"{T}{T})",
    f'{T}{T}(layer "In1.Cu")',
    f'{T}{T}(net "GND")',
    f'{T}{T}(uuid "poly-1")',
    f"{T})",
    f"{T}(zone",
    f'{T}{T}(layer "F.Cu")',
    f'{T}{T}(uuid "zone-empty")',
    f"{T})",
    f"{T}(zone",
    f'{T}{T}(net "GND")',
    f'{T}{T}(layer "B.Cu")',
    f'{T}{T}(uuid "zone-gnd")',
    f"{T}{T}(min_thickness 0.25)",
    f"{T}{T}(filled_polygon",
    f"{T}{T}{T}(pts (xy 0 0))",
    f"{T}{T})",
    f"{T})",
    ")",
    "",
])

PLAN = {"priority": {"poly-1": 0, "zone-gnd": 1, "zone-empty": 2}, "nets": {"zone-empty": "GND"},
        "empty": {"zone-empty"}, "ambiguous": []}


@pytest.fixture
def board(tmp_path) -> Path:
    p = tmp_path / "b.kicad_pcb"
    p.write_text(V10, encoding="utf-8", newline="\n")
    return p


def test_raw_refdes_takes_the_first_reference(board):
    assert kt.raw_refdes(board) == {"fp-1": "U7"}


def test_via_nets_are_pinned_from_the_raw_record(board, tmp_path):
    assert kt.raw_via_nets(board) == {"via-1": '(net "")'}
    changed = kt.pin_via_nets(str(board), {"via-1": '(net "SIG_A")'})
    assert changed == 1
    assert '(net "SIG_A")' in board.read_text().split("(via")[1].split("(gr_poly")[0]


def test_plan_zones_writes_priority_and_inferred_net(board):
    kt.plan_zones(str(board), PLAN)
    text = board.read_text()
    blocks = {b.split('(uuid "')[1].split('"')[0]: b for b in text.split("\t(zone\n")[1:]}
    assert '\t\t(net "GND")\n' in blocks["zone-empty"] and "\t\t(priority 2)\n" in blocks["zone-empty"]
    assert "\t\t(priority 1)\n" in blocks["zone-gnd"]


def _downgrade(board, tmp_path, keep_fills=True):
    out = tmp_path / "v9.kicad_pcb"
    nets, counts = kt.downgrade_to_kicad9(str(board), str(out), PLAN, keep_fills=keep_fills)
    return out.read_text(), nets, counts


def test_downgrade_header_and_net_table(board, tmp_path):
    text, nets, _ = _downgrade(board, tmp_path)
    assert "\t(version 20241229)" in text and '(generator_version "9.0")' in text
    assert nets == 3                                         # "", GND, SIG_A
    assert '\t(net 0 "")' in text and '\t(net 1 "GND")' in text and '\t(net 2 "SIG_A")' in text
    assert text.index('\t(net 0 "")') > text.index("\t(setup")
    assert '(net 2 "SIG_A")' in text.split('(pad "1"')[1]   # pads keep the name
    assert "\t\t(net 2)\n" in text.split("\t(segment")[1]     # tracks are numbered only


def test_downgrade_renumbers_layers_kicad6(board, tmp_path):
    text, _, _ = _downgrade(board, tmp_path)
    table = text.split("\t(layers\n")[1].split("\t)\n")[0]
    assert table.splitlines() == ['\t\t(0 "F.Cu" signal "TOP")', '\t\t(1 "In1.Cu" signal "02_GND")',
                                  '\t\t(31 "B.Cu" signal "BOTTOM")', '\t\t(37 "F.SilkS" user "Silk Top")',
                                  '\t\t(44 "Edge.Cuts" user)']


def test_downgrade_drops_v10_only_lines(board, tmp_path):
    text, _, _ = _downgrade(board, tmp_path)
    for token in ("duplicate_pad_numbers_are_jumpers", "(covering", "(capping"):
        assert token not in text


def test_downgrade_writes_kicad6_width_beside_stroke(board, tmp_path):
    text, _, _ = _downgrade(board, tmp_path)
    gr = text.split("\t(gr_line")[1].split("\t)\n")[0]
    assert "\t\t(width 0.1)\n\t\t(stroke" in gr


def test_downgrade_straightens_only_collinear_arcs(board, tmp_path):
    text, _, counts = _downgrade(board, tmp_path)
    assert counts["arcs straightened"] == 1
    assert '(uuid "arc-1")' in text.split("\t(segment")[2]   # became the second segment
    assert text.count("\t(arc\n") == 1                         # arc-2 survives


def test_downgrade_zones_netted_polygons_and_drops_empty_zones(board, tmp_path):
    text, _, counts = _downgrade(board, tmp_path)
    assert counts["copper polygons zoned"] == 1 and counts["empty netless zones dropped"] == 1
    assert "gr_poly" not in text and "zone-empty" not in text
    poly_zone = text.split('(uuid "poly-1")')[0].rsplit("\t(zone", 1)[1]
    assert "(net 1)" in poly_zone and '(net_name "GND")' in poly_zone


def test_downgrade_lowers_zone_min_thickness_so_altium_keeps_plane_webs(board, tmp_path):
    # Altium reads min_thickness as "remove necks narrower than": 0.25 mm cut the plane under BGAs
    text, _, counts = _downgrade(board, tmp_path)
    assert counts["zone neck widths lowered"] == 1
    assert "(min_thickness 0.25)" not in text
    assert text.count("(min_thickness 0.0254)") == 2      # zone-gnd, and the zone made from gr_poly poly-1


def test_downgrade_nofill_strips_filled_polygons(board, tmp_path):
    text, _, _ = _downgrade(board, tmp_path, keep_fills=False)
    assert "filled_polygon" not in text
    assert "filled_polygon" in _downgrade(board, tmp_path, keep_fills=True)[0]


def test_arc_radius():
    assert kt.arc_radius_mm((0, 0), (1, 1), (2, 0)) == pytest.approx(1.0)
    assert kt.arc_radius_mm((0, 0), (1, 0), (2, 0)) == float("inf")


# ---- pour order -------------------------------------------------------------------------------

def _zone(uuid, net=None, keepout=False):
    lines = [f"{T}(zone"]
    if net is not None:
        lines.append(f'{T}{T}(net "{net}")')
    lines += [f'{T}{T}(layer "F.Cu")', f'{T}{T}(uuid "{uuid}")']
    if keepout:
        lines += [f"{T}{T}(keepout", f"{T}{T}{T}(tracks not_allowed)", f"{T}{T})"]
    lines += [f"{T}{T}(polygon", f"{T}{T}{T}(pts (xy 0 0) (xy 1 0) (xy 1 1))", f"{T}{T})", f"{T})"]
    return lines


def _order_board(tmp_path, zones):
    head = [l for l in V10.split("\n")][: V10.split("\n").index(f"{T}(gr_line")]
    p = tmp_path / "z.kicad_pcb"
    p.write_text("\n".join(head + [l for z in zones for l in z] + [")", ""]), encoding="utf-8", newline="\n")
    return p


def _uuids(text):
    return [b.split('(uuid "')[1].split('"')[0] for b in text.split("\t(zone\n")[1:]]


# largest area has the lowest number (the plan's convention): plane 0, medium 1, island 2, netless 3
ORDER_PLAN = {"priority": {"plane": 0, "medium": 1, "island": 2, "floating": 3},
              "nets": {}, "empty": set(), "ambiguous": []}


def _priorities(text):
    out = {}
    for b in text.split("\t(zone\n")[1:]:
        u = b.split('(uuid "')[1].split('"')[0]
        out[u] = int(b.split("(priority ")[1].split(")")[0]) if "(priority " in b.split("\t)\n")[0] else None
    return out


def test_altium_copies_carry_the_pour_rank_as_zone_priority(tmp_path):
    # Altium pours in ascending (priority N): the smallest island must get 0, the netless zone the last rank
    zones = [_zone("floating"), _zone("plane", "GND"), _zone("fence", keepout=True),
             _zone("island", "V2P5"), _zone("medium", "V3P3")]
    src = _order_board(tmp_path, zones)
    out = tmp_path / "o.kicad_pcb"
    _, counts = kt.downgrade_to_kicad9(str(src), str(out), ORDER_PLAN)
    text = out.read_text()
    assert _uuids(text) == ["fence", "island", "medium", "plane", "floating"]
    assert _priorities(text) == {"fence": None, "island": 0, "medium": 1, "plane": 2, "floating": 3}
    assert counts["zones written in pour order"] == 5 and counts["copper zones given their pour rank as priority"] == 4
    assert text.rstrip("\n").endswith(")") and text.count("\t(zone\n") == 5


def test_altium_priority_replaces_an_existing_priority():
    z = _zone("plane", "GND")
    z.insert(z.index(f'{T}{T}(uuid "plane")') + 1, f"{T}{T}(priority 0)")
    out = kt.altium_priority(z, 7)
    assert [l for l in out if "(priority" in l] == [f"{T}{T}(priority 7)"]


def test_pour_order_is_smallest_first_netless_last():
    zones = [_zone("floating"), _zone("plane", "GND"), _zone("island", "V2P5"), _zone("medium", "V3P3")]
    assert [_zone_uuid(z) for z in kt.pour_order(zones, ORDER_PLAN)] == ["island", "medium", "plane", "floating"]


def test_pour_order_ties_keep_file_order_and_unplanned_zones_go_last_among_netted():
    a, b, c = _zone("a", "X"), _zone("b", "X"), _zone("c", "X")      # c is not in the plan
    ordered = kt.pour_order([c, a, b], {"priority": {"a": 5, "b": 5}})
    assert [_zone_uuid(z) for z in ordered] == ["a", "b", "c"]


def _zone_uuid(block):
    return next(l.strip().split('"')[1] for l in block if l.strip().startswith("(uuid "))


# ---- outline arcs and pad connection -------------------------------------------------------------

NO_PLAN = {"priority": {"g": 3}, "nets": {}, "empty": set(), "ambiguous": []}


def _pts(lines):
    return [tuple(float(v) for v in l.strip()[4:-1].split()) for l in lines if l.strip().startswith("(xy ")]


def _area(pts):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def test_zone_outline_arcs_become_points_and_thermal_pads_become_solid():
    # 2 x 2 square whose right side is a half disc of radius 1 (KiCad 10 writes arcs over five lines)
    block = [f"{T}(zone", f'{T}{T}(net "GND")', f'{T}{T}(layer "F.Cu")', f'{T}{T}(uuid "z")',
             f"{T}{T}(connect_pads", f"{T}{T}{T}(clearance 0.5)", f"{T}{T})",
             f"{T}{T}(polygon", f"{T}{T}{T}(pts", f"{T}{T}{T}{T}(xy 0 0)", f"{T}{T}{T}{T}(xy 2 0)",
             f"{T}{T}{T}{T}(arc", f"{T}{T}{T}{T}{T}(start 2 0)", f"{T}{T}{T}{T}{T}(mid 3 1)", f"{T}{T}{T}{T}{T}(end 2 2)",
             f"{T}{T}{T}{T})", f"{T}{T}{T}{T}(xy 0 2)", f"{T}{T}{T})", f"{T}{T})", f"{T})"]
    counts = collections.Counter()
    out = kt.altium_block(block, counts, NO_PLAN)
    assert not any(l.strip().startswith(("(arc", "(start", "(mid", "(end")) for l in out)
    pts = _pts(out)
    assert _area(pts) == pytest.approx(4 + math.pi / 2, abs=0.01)       # the half disc is kept, not cut off
    assert pts.count((2.0, 0.0)) == 1                                     # shared end not repeated
    assert max(x for x, _ in pts) == pytest.approx(3.0, abs=0.003)       # the arc's far side survives
    assert f"{T}{T}(connect_pads yes" in out and counts["zone pad connections made solid"] == 1
    assert counts["outline arcs written as points"] == 1


def test_zoned_gr_poly_full_circle_becomes_a_polygon():
    # Altium drops an arc-only outline entirely ("Zone with 0 vertices"); 372 VCU118 GND dots each held a via
    block = [f"{T}(gr_poly", f"{T}{T}(pts", f"{T}{T}{T}(arc", f"{T}{T}{T}{T}(start 1 0)", f"{T}{T}{T}{T}(mid -1 0)",
             f"{T}{T}{T}{T}(end 1 0)", f"{T}{T}{T})", f"{T}{T})", f'{T}{T}(layer "B.Cu")', f'{T}{T}(net "GND")',
             f'{T}{T}(uuid "g")', f"{T})"]
    counts = collections.Counter()
    out = kt.altium_block(block, counts, NO_PLAN)
    assert out[0] == f"{T}(zone" and counts["copper polygons zoned"] == 1
    # chords cut at most ARC_SAGITTA_MM into the disc, so at most sagitta x perimeter of area is lost
    assert len(_pts(out)) > 16 and _area(_pts(out)) == pytest.approx(math.pi, abs=kt.ARC_SAGITTA_MM * 2 * math.pi)


def test_keepout_zone_is_left_alone():
    block = [f"{T}(zone", f'{T}{T}(layer "F.Cu")', f'{T}{T}(uuid "k")', f"{T}{T}(connect_pads", f"{T}{T})",
             f"{T}{T}(keepout", f"{T}{T}{T}(tracks not_allowed)", f"{T}{T})",
             f"{T}{T}(polygon", f"{T}{T}{T}(pts", f"{T}{T}{T}{T}(arc", f"{T}{T}{T}{T}{T}(start 1 0)",
             f"{T}{T}{T}{T}{T}(mid -1 0)", f"{T}{T}{T}{T}{T}(end 1 0)", f"{T}{T}{T}{T})", f"{T}{T}{T})", f"{T}{T})", f"{T})"]
    assert kt.altium_block(list(block), collections.Counter(), NO_PLAN) == block
