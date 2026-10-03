"""kicad_text transforms on small synthetic KiCad 10 boards."""

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


def test_downgrade_nofill_strips_filled_polygons(board, tmp_path):
    text, _, _ = _downgrade(board, tmp_path, keep_fills=False)
    assert "filled_polygon" not in text
    assert "filled_polygon" in _downgrade(board, tmp_path, keep_fills=True)[0]


def test_arc_radius():
    assert kt.arc_radius_mm((0, 0), (1, 1), (2, 0)) == pytest.approx(1.0)
    assert kt.arc_radius_mm((0, 0), (1, 0), (2, 0)) == float("inf")
