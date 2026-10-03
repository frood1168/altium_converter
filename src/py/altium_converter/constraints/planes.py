"""Plane (poured copper) clearances and connection styles, measured from the fills.

Only meaningful when the board carries real fills -- for an Allegro import,
Allegro's own poured copper, so these are the clearances its shapes were
voided with: the antipads. For every filled zone on a layer:

- other-net vias and pads inside its outline: fill-edge to object distance,
  both from the pad edge on that layer and from the drill edge (Altium's
  Power Plane Clearance is hole-referenced; Clearance to Copper is
  copper-to-copper), by object kind and BGA field vs open board;
- other-net tracks inside its outline: fill-edge to track-edge distance;
- same-net vias and pads: how the fill connects -- direct (solid), relief
  (spokes, counted), or not at all;
- fill to board edge.

A plane polygon can have 10^5+ vertices and thousands of holes, so nothing
here intersects against it whole: distances come from a spatial index over
its boundary segments, connection style from point-in-polygon probes on a
ring round each object.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import shapely

from ..model import TH_PAD, TRACK, VIA, VIA_HOLE, Board, poly
from .stats import summary

RING_MM = 0.03          # probe ring sits this far outside a same-net object's edge
PROBES = 72
MAX_MM = 1.5


def _boundary_segments(geom) -> np.ndarray:
    """Every edge of every ring of a (Multi)Polygon, as LineStrings."""
    segs = []
    for p in getattr(geom, "geoms", [geom]):
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            segs.append(np.stack([c[:-1], c[1:]], axis=1))
    return shapely.linestrings(np.concatenate(segs)) if segs else np.array([], dtype=object)


_ANGLES = np.linspace(0, 2 * np.pi, PROBES, endpoint=False)
_GAP_STEPS = np.arange(RING_MM, 0.8, 0.0127)     # scan outward in half-mil steps


def _ring(fill, x, y, rr):
    inside = shapely.contains_xy(fill, x + rr * np.cos(_ANGLES), y + rr * np.sin(_ANGLES))
    return inside.mean(), int(np.count_nonzero(inside & ~np.roll(inside, 1))) if not inside.all() else 1


def _style(fill, x, y, r):
    """(style, spokes, gap) for a same-net object of radius r centred at (x, y).

    At the object's edge (first ring):
      copper all round                     -> direct
      several narrow arcs (spokes)         -> relief; gap = first radius where the plane is solid
      one or two broad arcs                -> partial (plane edge, or cut back by routing)
      nothing                              -> scan outward: plane further out -> isolated
                                              (an antipad, no connection; gap = its clearance)
                                              no plane within 0.8 mm -> none
    """
    frac, runs = _ring(fill, x, y, r + _GAP_STEPS[0])
    if frac > 0.9:
        return "direct", 0, 0.0
    if frac > 0:
        if runs >= 3 and frac < 0.6:
            for gap in _GAP_STEPS[1:]:
                if _ring(fill, x, y, r + gap)[0] > 0.9:
                    return "relief", runs, round(float(gap), 4)
            return "relief", runs, None
        return "partial", runs, None
    for gap in _GAP_STEPS[1:]:
        if _ring(fill, x, y, r + gap)[0] > 0:
            return "isolated", 0, round(float(gap), 4)
    return "none", 0, None


def analyse(board: Board, progress=print) -> dict:
    m = board.model
    zones = [z for z in m["zones"] if z["net"] > 0 and z["fills"]]
    if not zones:
        return {"note": "no filled zones in this board: plane clearances need the source fills"}
    outline = board.outline()
    edge_segs = _boundary_segments(outline) if outline is not None else None
    hole_r = {}
    for v in m["vias"]:
        hole_r[(round(v["x"], 4), round(v["y"], 4))] = v["drill"] / 2
    for p in m["pads"]:
        if p["drill"]:
            hole_r[(round(p["x"], 4), round(p["y"], 4))] = p["drill"] / 2

    other = defaultdict(list)
    tracks = defaultdict(list)
    conn = defaultdict(lambda: defaultdict(int))
    spokes = defaultdict(list)
    gaps = defaultdict(list)
    edge = defaultdict(list)
    for layer in board.layers:
        lz = [z for z in zones if z["layer"] == layer]
        if not lz:
            continue
        objs = board.objects(layer)
        tree = shapely.STRtree(objs.geoms)
        cent = shapely.centroid(objs.geoms)
        cx, cy = shapely.get_x(cent), shapely.get_y(cent)
        # Hole radius for vias and TH pads only: an SMD pad can share its centre
        # with a via-in-pad, whose drill is not the pad's.
        drill = np.array([hole_r.get((round(cx[k], 4), round(cy[k], 4)), 0.0)
                          if objs.kind[k] in (VIA, VIA_HOLE, TH_PAD) else 0.0 for k in range(len(cent))])
        for z in lz:
            fill = shapely.union_all([poly(f) for f in z["fills"]])
            # The zone's own outline, not the fill's exteriors: KiCad stores fills
            # *fractured* (holes joined to the edge by zero-width slits), so a fill
            # exterior runs round every antipad and excludes the vias inside them.
            hull = shapely.union_all([poly(o) for o in z["outline"]])
            shapely.prepare(fill)
            segs = _boundary_segments(fill)
            seg_tree = shapely.STRtree(segs)
            if edge_segs is not None and len(edge_segs):
                _, d = seg_tree.query_nearest(edge_segs, return_distance=True, all_matches=False)
                edge[layer].append(float(d.min()))
            idx = tree.query(hull, predicate="intersects")
            if not len(idx):
                continue
            same = objs.net[idx] == z["net"]
            for k in idx[same]:
                if objs.kind[k] == TRACK:
                    continue
                if objs.kind[k] in (VIA, VIA_HOLE):
                    r = objs.radius[k]
                else:   # pads: probe just outside their bounding circle
                    b = objs.geoms[k].bounds
                    r = 0.5 * max(b[2] - b[0], b[3] - b[1])
                style, n, gap = _style(fill, cx[k], cy[k], r)
                conn[(layer, objs.kind[k])][style] += 1
                if style == "relief":
                    spokes[(layer, objs.kind[k])].append(n)
                if gap:
                    gaps[(layer, objs.kind[k], style)].append(gap)
            ko = idx[~same]
            if not len(ko):
                continue
            ko = ko[~shapely.contains_xy(fill, cx[ko], cy[ko])]   # centred on the fill: a short, not a clearance
            if not len(ko):
                continue
            (qi, _), dist = seg_tree.query_nearest(objs.geoms[ko], return_distance=True, all_matches=False)
            ko = ko[qi]
            edge_e = dist - objs.radius[ko]
            # hole-referenced, from the centre (exact for round vias and pads)
            (qc, _), dc = seg_tree.query_nearest(shapely.points(cx[ko], cy[ko]), return_distance=True,
                                                 all_matches=False)
            hole_e = np.full(len(ko), np.nan)
            hole_e[qc] = dc - drill[ko[qc]]
            for k, e, h in zip(ko, edge_e, hole_e):
                if not 0 <= e <= MAX_MM:
                    continue
                if objs.kind[k] == TRACK:
                    tracks[(layer, bool(objs.in_bga[k]))].append(e)
                    continue
                key = (layer, objs.kind[k], bool(objs.in_bga[k]))
                other[key + ("pad",)].append(e)
                if drill[k]:
                    other[key + ("drill",)].append(h)
        progress(f"  planes {layer}: {len(lz)} filled zones")

    def table(acc):
        return {f"{k[0]} | {k[1]} ({'BGA' if k[2] else 'open'}) from {k[3]}": summary(v)
                for k, v in sorted(acc.items())}

    return {
        "antipad": table(other),
        "to_tracks": {f"{l} ({'BGA' if b else 'open'})": summary(v) for (l, b), v in sorted(tracks.items())},
        "connections": {f"{l} | {k}": dict(v) for (l, k), v in sorted(conn.items())},
        "relief_spokes": {f"{l} | {k}": {int(s): int(n) for s, n in zip(*np.unique(v, return_counts=True))}
                          for (l, k), v in sorted(spokes.items())},
        "connection_gap": {f"{l} | {k} | {s}": summary(v) for (l, k, s), v in sorted(gaps.items())},
        "to_board_edge": {l: summary(v) for l, v in sorted(edge.items())},
    }
