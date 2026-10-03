"""The neutral board model (``altium_converter.board/1``) and its shapely views.

Written by ``kicad_side/dump_board.py``; read here. All lengths are mm. The
analysis sees the board only through this module, so any source that writes
the schema (KiCad today, an Altium board via altium_monkey later) can be
measured the same way.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely

SCHEMA = "altium_converter.board/1"

# Object kinds, named the way Altium's clearance matrix names them.
TRACK, VIA, VIA_HOLE, SMD_PAD, TH_PAD, COPPER = "Track", "Via", "Via hole", "SMD Pad", "TH Pad", "Copper"

BGA_MIN_PADS = 100      # a footprint with at least this many pads is treated as a BGA field


def load(path: str | Path) -> dict:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        model = json.load(f)
    if model.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {model.get('schema')!r}, expected {SCHEMA!r}")
    return model


def poly(rings) -> shapely.Polygon:
    exterior, holes = rings
    return shapely.Polygon(exterior, holes)


@dataclass
class LayerObjects:
    """Every netted copper object on one layer, as parallel arrays."""

    layer: str
    geoms: np.ndarray                    # shapely geometries (centre-lines, points, polygons)
    radius: np.ndarray                   # inflate: half track width, via pad radius, 0 for polygons
    net: np.ndarray
    kind: np.ndarray                     # one of the kind constants above
    in_bga: np.ndarray                   # bool
    width: np.ndarray                    # track width (0 for non-tracks)
    length: np.ndarray                   # track length (0 for non-tracks)


@dataclass
class Board:
    model: dict
    layers: list[str]                                   # copper layer names, stack order
    nets: list[str]
    bga_regions: list[tuple[str, shapely.Geometry]] = field(default_factory=list)
    _bga_tree: shapely.STRtree | None = None

    @classmethod
    def from_model(cls, model: dict) -> "Board":
        b = cls(model=model, layers=[l["name"] for l in model["layers"]], nets=model["nets"])
        by_ref: dict[str, list] = {}
        for p in model["pads"]:
            by_ref.setdefault(p["ref"], []).append((p["x"], p["y"]))
        for ref, pts in by_ref.items():
            if len(pts) >= BGA_MIN_PADS:
                b.bga_regions.append((ref, shapely.MultiPoint(pts).convex_hull.buffer(0.5)))
        if b.bga_regions:
            b._bga_tree = shapely.STRtree([g for _, g in b.bga_regions])
        return b

    def in_bga(self, geoms) -> np.ndarray:
        geoms = np.asarray(geoms, dtype=object)
        out = np.zeros(len(geoms), dtype=bool)
        if self._bga_tree is None or not len(geoms):
            return out
        hit = self._bga_tree.query(shapely.centroid(geoms), predicate="intersects")
        out[np.unique(hit[0])] = True
        return out

    def net_name(self, n: int) -> str:
        return self.nets[n] if 0 <= n < len(self.nets) else f"#{n}"

    def objects(self, layer: str) -> LayerObjects:
        m = self.model
        g, r, net, kind, width, length = [], [], [], [], [], []
        for lay, n, w, x1, y1, x2, y2 in m["tracks"]:
            if lay == layer and n > 0:
                g.append(shapely.LineString([(x1, y1), (x2, y2)])); r.append(w / 2); net.append(n)
                kind.append(TRACK); width.append(w); length.append(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
        for v in m["vias"]:
            if v["net"] > 0 and layer in v["layers"]:
                d = v["layers"][layer]
                g.append(shapely.Point(v["x"], v["y"])); net.append(v["net"]); width.append(0); length.append(0)
                r.append(d / 2 if d else v["drill"] / 2); kind.append(VIA if d else VIA_HOLE)
        for p in m["pads"]:
            if p["net"] > 0 and layer in p["layers"]:
                g.append(shapely.Polygon(p["layers"][layer])); r.append(0.0); net.append(p["net"])
                kind.append(TH_PAD if p["drill"] else SMD_PAD); width.append(0); length.append(0)
        geoms = np.array(g, dtype=object)
        return LayerObjects(layer, geoms, np.array(r), np.array(net), np.array(kind, dtype=object),
                            self.in_bga(geoms), np.array(width), np.array(length))

    def outline(self) -> shapely.Polygon | None:
        o = self.model.get("outline")
        return shapely.Polygon(o) if o else None
