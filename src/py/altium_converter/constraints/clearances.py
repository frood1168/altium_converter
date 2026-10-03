"""Observed object-to-object clearances, per layer, between different nets.

For every netted copper object the nearest different-net object of each kind
is found and its edge-to-edge distance kept. The per-object minima, split by
object-kind pair and by BGA field vs open board, are what the rule estimate
reads. Kinds follow Altium's clearance matrix (Track, Via, SMD Pad, TH Pad);
"Via hole" is a via with no pad on that layer (unused-pad removal), measured
from its drill.
"""

from __future__ import annotations

import numpy as np
import shapely

from ..model import Board
from .stats import summary

SEARCH_MM = 0.5         # only clearances up to this are of interest for rules


def nearest_other_net(objs) -> dict[tuple[str, str, bool], np.ndarray]:
    """{(kind, other_kind, in_bga): per-object minimum clearance} for one layer."""
    n = len(objs.geoms)
    if not n:
        return {}
    tree = shapely.STRtree(objs.geoms)
    rmax = float(objs.radius.max())
    parts: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    chunk = 20000
    for s in range(0, n, chunk):
        idx = np.arange(s, min(n, s + chunk))
        i, j = tree.query(objs.geoms[idx], predicate="dwithin", distance=SEARCH_MM + objs.radius[idx] + rmax)
        i = idx[i]
        keep = objs.net[i] != objs.net[j]
        i, j = i[keep], j[keep]
        if not len(i):
            continue
        d = shapely.distance(objs.geoms[i], objs.geoms[j]) - objs.radius[i] - objs.radius[j]
        ok = d <= SEARCH_MM
        i, j, d = i[ok], j[ok], np.maximum(d[ok], 0.0)
        kinds_j = objs.kind[j]
        for kj in np.unique(kinds_j):
            sel = kinds_j == kj
            parts.setdefault(kj, []).append((i[sel], d[sel]))
    out: dict[tuple[str, str, bool], np.ndarray] = {}
    for kj, chunks in parts.items():
        ii = np.concatenate([c[0] for c in chunks])
        dd = np.concatenate([c[1] for c in chunks])
        order = np.lexsort((dd, ii))                    # per object, nearest first
        ii, dd = ii[order], dd[order]
        first = np.r_[True, ii[1:] != ii[:-1]]
        ii, dd = ii[first], dd[first]
        for ki in np.unique(objs.kind[ii]):
            for bga in (False, True):
                sel = (objs.kind[ii] == ki) & (objs.in_bga[ii] == bga)
                if sel.any():
                    out[(ki, kj, bga)] = dd[sel]
    return out


def analyse(board: Board, progress=print) -> dict:
    per_layer: dict[str, dict] = {}
    pooled: dict[tuple[str, str, bool], list[np.ndarray]] = {}
    for layer in board.layers:
        objs = board.objects(layer)
        res = nearest_other_net(objs)
        progress(f"  clearance {layer}: {len(objs.geoms)} objects")
        per_layer[layer] = {_key(k): summary(v) for k, v in sorted(res.items())}
        for k, v in res.items():
            pooled.setdefault(k, []).append(v)
    board_wide = {_key(k): summary(np.concatenate(v)) for k, v in sorted(pooled.items())}
    return {"search_mm": SEARCH_MM, "board": board_wide, "layers": per_layer}


def _key(k) -> str:
    a, b, bga = k
    return f"{a} -> {b} ({'BGA' if bga else 'open'})"
