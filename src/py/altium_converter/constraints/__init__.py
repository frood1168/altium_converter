"""Observed design constraints, measured from a board model.

``run(model)`` returns one JSON-able dict; ``report.markdown`` renders it.
Every value is *observed* -- what the routed copper actually does -- not a
rule read from the source tool, which the importers generally do not carry.
"""

from __future__ import annotations

import datetime

from ..model import Board
from . import clearances, diffpairs, planes, vias, widths

SECTIONS = ("diffpairs", "widths", "vias", "clearances", "planes")


def run(model: dict, sections=SECTIONS, progress=print) -> dict:
    board = Board.from_model(model)
    progress(f"{len(board.layers)} copper layers, {len(board.bga_regions)} BGA fields "
             f"({', '.join(r for r, _ in board.bga_regions)})")
    out = {"schema": "altium_converter.constraints/1", "source": model.get("source"),
           "generated": datetime.datetime.now().isoformat(timespec="seconds"),
           "layers": board.layers, "bga_fields": [r for r, _ in board.bga_regions],
           # the board's namespaces, so rules emitted from this result can be linted against them
           "nets": [n for n in board.nets if n],
           "components": sorted({p["ref"] for p in model["pads"] if p.get("ref")})}
    dp = diffpairs.analyse(board, progress) if "diffpairs" in sections else {"pairs": []}
    out["diffpairs"] = dp
    pair_nets = {n for p in dp["pairs"] for n in (p["p"], p["n"])}
    if "widths" in sections:
        out["widths"] = widths.analyse(board, pair_nets, progress)
    if "vias" in sections:
        out["vias"] = vias.analyse(board, progress)
    if "clearances" in sections:
        out["clearances"] = clearances.analyse(board, progress)
    if "planes" in sections:
        out["planes"] = planes.analyse(board, progress)
    return out
