"""Dump a KiCad board's copper geometry to the neutral board model (gzipped JSON, mm).

Runs under KiCad's bundled Python (it needs ``pcbnew``); ``altium-converter dump``
finds that interpreter and runs this for you. The model is what the constraint
analysis reads, so the analysis never needs pcbnew -- and another source (an
Altium board via altium_monkey, say) can feed it by writing the same schema.

    <kicad>/bin/python.exe dump_board.py <board.kicad_pcb> <out.board.json.gz>

Schema (``schema`` = "altium_converter.board/1"), all lengths in mm:

    layers   [{id, name, copper, order}]           copper layers in stack order
    nets     [name, ...]                            index = net id; 0 is "no net"
    tracks   [[layer, net, width, x1, y1, x2, y2]]  arcs flattened to chords
    arcs     [[layer, net, width, x1, y1, xm, ym, x2, y2]]   arcs as drawn
    vias     [{net, x, y, drill, layers: {layer: pad_diameter or 0 if no pad}}]
    pads     [{ref, num, net, x, y, drill, layers: {layer: [[x, y], ...]}}]
    zones    [{net, layer, priority, outline, fills: [[exterior, [holes...]]]}]
    outline  [[x, y], ...] board edge (largest outline)

Rule areas (keepouts) are skipped. Zone ``fills`` are whatever the file carries:
for an Allegro import, Allegro's own poured copper.
"""
import datetime
import gzip
import json
import math
import sys

import pcbnew

MM = 1e-6       # pcbnew internal units are nm
ARC_CHORD_MM = 0.05


def pt(v):
    return [round(v.x * MM, 5), round(v.y * MM, 5)]


def ring(chain):
    return [pt(chain.CPoint(i)) for i in range(chain.PointCount())]


def polys(sps):
    """SHAPE_POLY_SET -> [[exterior, [holes]]]."""
    sps = pcbnew.SHAPE_POLY_SET(sps)
    sps.ClearArcs()       # boolean ops and point access on arcs assert; the dialog blocks
    out = []
    for i in range(sps.OutlineCount()):
        out.append([ring(sps.Outline(i)), [ring(sps.Hole(i, j)) for j in range(sps.HoleCount(i))]])
    return out


def arc_chords(a):
    """Flatten an arc to chords no longer than ARC_CHORD_MM of sagitta-free length."""
    s, m, e = a.GetStart(), a.GetMid(), a.GetEnd()
    c = a.GetCenter()
    r = math.hypot(s.x - c.x, s.y - c.y)
    a0 = math.atan2(s.y - c.y, s.x - c.x)
    a1 = math.atan2(e.y - c.y, e.x - c.x)
    am = math.atan2(m.y - c.y, m.x - c.x)
    sweep = (a1 - a0) % (2 * math.pi)
    if not 0 <= (am - a0) % (2 * math.pi) <= sweep:   # mid not on the ccw sweep -> cw
        sweep -= 2 * math.pi
    n = max(2, int(abs(sweep) * r * MM / ARC_CHORD_MM) + 1)
    pts = [[round((c.x + r * math.cos(a0 + sweep * k / n)) * MM, 5),
            round((c.y + r * math.sin(a0 + sweep * k / n)) * MM, 5)] for k in range(n + 1)]
    return list(zip(pts, pts[1:]))


def main(src, dst):
    b = pcbnew.LoadBoard(src)
    copper = list(b.GetEnabledLayers().CuStack())
    name = {l: b.GetLayerName(l) for l in copper}       # user names, e.g. "02_GND"
    layers = [{"id": pcbnew.LayerName(l), "name": name[l], "copper": True, "order": i}
              for i, l in enumerate(copper)]

    nets = [""] * (b.GetNetCount() + 1)
    for n in b.GetNetsByNetcode().values():
        if 0 <= n.GetNetCode() < len(nets):
            nets[n.GetNetCode()] = n.GetNetname()

    tracks, arcs, vias = [], [], []
    for t in b.GetTracks():
        cls = t.GetClass()
        if cls == "PCB_VIA":
            span = {}
            for l in copper:
                if t.IsOnLayer(l):
                    span[name[l]] = round(t.GetWidth(l) * MM, 5) if t.FlashLayer(l) else 0
            vias.append({"net": t.GetNetCode(), "x": pt(t.GetPosition())[0], "y": pt(t.GetPosition())[1],
                         "drill": round(t.GetDrillValue() * MM, 5), "layers": span})
        elif cls == "PCB_ARC":
            w, l, n = round(t.GetWidth() * MM, 5), name.get(t.GetLayer()), t.GetNetCode()
            arcs.append([l, n, w] + pt(t.GetStart()) + pt(t.GetMid()) + pt(t.GetEnd()))
            for p, q in arc_chords(t):
                tracks.append([l, n, w] + p + q)
        else:
            tracks.append([name.get(t.GetLayer()), t.GetNetCode(), round(t.GetWidth() * MM, 5)]
                          + pt(t.GetStart()) + pt(t.GetEnd()))

    pads = []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            shapes = {}
            for l in copper:
                if p.IsOnLayer(l) and p.FlashLayer(l):
                    poly = p.GetEffectivePolygon(l, pcbnew.ERROR_INSIDE)
                    if poly.OutlineCount():
                        shapes[name[l]] = ring(poly.Outline(0))
            drill = p.GetDrillSize()
            pads.append({"ref": ref, "num": p.GetNumber(), "net": p.GetNetCode(),
                         "x": pt(p.GetPosition())[0], "y": pt(p.GetPosition())[1],
                         "drill": round(min(drill.x, drill.y) * MM, 5) if drill.x else 0,
                         "layers": shapes})

    zones = []
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for l in z.GetLayerSet().Seq():
            if l not in name:
                continue
            fills = polys(z.GetFilledPolysList(l)) if z.IsFilled() else []
            zones.append({"net": z.GetNetCode(), "layer": name[l], "priority": z.GetAssignedPriority(),
                          "outline": polys(z.Outline()), "fills": fills})

    edge = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(edge, True)   # KiCad 10: (outlines, infer-if-necessary)
    outline = max(polys(edge), key=lambda p: len(p[0]))[0] if edge.OutlineCount() else []

    model = {"schema": "altium_converter.board/1", "source": src,
             "generated": datetime.datetime.now().isoformat(timespec="seconds"),
             "kicad": pcbnew.Version(), "layers": layers, "nets": nets, "tracks": tracks,
             "arcs": arcs, "vias": vias, "pads": pads, "zones": zones, "outline": outline}
    with gzip.open(dst, "wt", encoding="utf-8") as f:
        json.dump(model, f, separators=(",", ":"))
    print(f"layers {len(layers)}, nets {len(nets) - 1}, tracks {len(tracks)} ({len(arcs)} arcs), "
          f"vias {len(vias)}, pads {len(pads)}, zones {len(zones)} "
          f"({sum(1 for z in zones if z['fills'])} filled) -> {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
