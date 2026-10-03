"""Checks that a translation landed where it should.

- ``layer_counts``: tracks/arcs per Altium layer in a PcbDoc against per-layer
  counts in the KiCad file it was imported from. A wholesale relabelling (the
  KiCad 9 layer-number scramble) shows as identical counts on the wrong names.
- ``drc_compare``: unconnected items per net between two ``kicad-cli pcb drc``
  JSON reports -- typically the source's own fills vs a re-pour, which is what
  Altium does to an imported board.
"""

from __future__ import annotations

import collections
import json
import re
import struct

# Altium layer ids (as written in Tracks6/Arcs6 records).
ALTIUM_LAYER = {1: "TopLayer", 32: "BottomLayer", 33: "TopOverlay", 34: "BottomOverlay", 35: "TopPaste",
                36: "BottomPaste", 37: "TopSolder", 38: "BottomSolder", 56: "KeepOut", 74: "MultiLayer"}
ALTIUM_LAYER.update({i: f"MidLayer{i - 1}" for i in range(2, 32)})
ALTIUM_LAYER.update({i: f"Mechanical{i - 56}" for i in range(57, 73)})

# Where each KiCad layer should land in Altium.
EXPECTED = {"F.Cu": "TopLayer", "B.Cu": "BottomLayer", "F.SilkS": "TopOverlay", "B.SilkS": "BottomOverlay",
            "F.Paste": "TopPaste", "B.Paste": "BottomPaste", "F.Mask": "TopSolder", "B.Mask": "BottomSolder",
            **{f"In{i}.Cu": f"MidLayer{i}" for i in range(1, 31)}}


def altium_counts(pcbdoc: str) -> dict[str, collections.Counter]:
    """{'Tracks': Counter(layer), 'Arcs': ...} from a PcbDoc's binary streams.

    Record framing (as altium_monkey reads it): <u8 type><u32 length><payload>,
    payload byte 0 = layer id.
    """
    import olefile

    ole = olefile.OleFileIO(pcbdoc)
    out = {}
    for stream, kind in (("Tracks6/Data", "Tracks"), ("Arcs6/Data", "Arcs")):
        data = ole.openstream(stream).read() if ole.exists(stream) else b""
        c, i = collections.Counter(), 0
        while i + 6 <= len(data):
            _, length = struct.unpack_from("<BI", data, i)
            c[ALTIUM_LAYER.get(data[i + 5], f"layer{data[i + 5]}")] += 1
            i += 5 + length
        out[kind] = c
    return out


def kicad_counts(kicad_pcb: str) -> dict[str, collections.Counter]:
    """Per-layer counts of segments+gr_line ('Tracks') and arcs+gr_arc ('Arcs'), by KiCad layer name."""
    c = {"Tracks": collections.Counter(), "Arcs": collections.Counter()}
    cur = None
    with open(kicad_pcb, encoding="utf-8") as f:
        for line in f:
            if line in ("\t(segment\n", "\t(arc\n", "\t(gr_line\n", "\t(gr_arc\n"):
                cur = "Arcs" if "arc" in line else "Tracks"
            elif cur and line.startswith("\t\t(layer "):
                c[cur][line.strip()[8:-2]] += 1
                cur = None
    return c


def layer_counts(pcbdoc: str, kicad_pcb: str) -> list[dict]:
    """One row per KiCad layer: expected Altium layer, count there, and where the count actually is."""
    a, k = altium_counts(pcbdoc), kicad_counts(kicad_pcb)
    rows = []
    for kind in ("Tracks", "Arcs"):
        for layer, n in k[kind].most_common():
            want = EXPECTED.get(layer, "Mechanical?")
            got = a[kind].get(want, 0)
            # where else does exactly this count appear?
            elsewhere = [al for al, an in a[kind].items() if an == n and al != want]
            rows.append({"kind": kind, "kicad": layer, "count": n, "expected": want, "altium_there": got,
                         "ok": got == n, "same_count_on": elsewhere})
    return rows


def _per_net(path):
    d = json.load(open(path, encoding="utf-8"))
    c = collections.Counter()
    for u in d.get("unconnected_items", []):
        m = re.search(r"\[([^\]]+)\]", u["items"][0]["description"])
        c[m.group(1) if m else "?"] += 1
    return c


def drc_compare(reference: str, candidate: str) -> dict:
    a, b = _per_net(reference), _per_net(candidate)
    worse = sorted(((b[n] - a[n], n) for n in b if b[n] > a[n]), reverse=True)
    better = sorted(((a[n] - b[n], n) for n in a if a[n] > b[n]), reverse=True)
    return {"reference": {"unconnected": sum(a.values()), "nets": len(a)},
            "candidate": {"unconnected": sum(b.values()), "nets": len(b)},
            "worse": [{"net": n, "reference": a[n], "candidate": b[n]} for _, n in worse],
            "better": [{"net": n, "reference": a[n], "candidate": b[n]} for _, n in better]}
