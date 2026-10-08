"""Text-level transforms of KiCad board files (no pcbnew).

Everything here edits the s-expression text line by line, never through
pcbnew: KiCad re-derives some values (via nets) when it saves, unrepeatably,
so a load-edit-save moves things it was not asked to. Text rewriting leaves
every untouched item byte-identical. Importable by both the uv package and
the scripts that run under KiCad's own Python.
"""
import collections
import math
import os
import re


def raw_refdes(path):
    """Footprint uuid -> refdes, read from the imported file's text.

    KiCad 10.0.3's Allegro importer writes two Reference properties per
    footprint: the real refdes, then "${REFERENCE}" from the assembly layer.
    The loader keeps the last, so every footprint loads as "${REFERENCE}".
    The first one is the truth; recover it before the board is loaded.
    """
    refs, uuid, ref = {}, None, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("\t(footprint "):
                uuid, ref = None, None
            elif line.startswith("\t\t(uuid ") and uuid is None:
                uuid = line.split('"')[1]
            elif line.startswith('\t\t(property "Reference" ') and ref is None:
                ref = line.split('"')[3]
                if uuid and ref and not ref.startswith("${"):
                    refs[uuid] = ref
    return refs



def raw_via_nets(path):
    """Via uuid -> its (net "NAME") line, read from the imported file's text.

    The importer gives every via a net. Loading and re-saving through pcbnew
    re-derives the net of a via that touches only copper shapes, and not
    repeatably: five saves of this board left 9, 11, 23, 28 and 31 vias netless.
    The importer's text is the record; pin_via_nets writes it back.
    """
    nets, uuid, net, in_via = {}, None, None, False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line == "\t(via\n":
                in_via, uuid, net = True, None, None
            elif in_via:
                s = line.strip()
                if s.startswith("(net "):
                    net = s
                elif s.startswith("(uuid "):
                    uuid = s.split('"')[1]
                elif line == "\t)\n":
                    nets[uuid] = net
                    in_via = False
    return nets


def pin_via_nets(path, nets):
    """Rewrite each via's net line in a saved KiCad 10 file from the importer's record."""
    tmp = path + ".tmp"
    changed, block = 0, None
    with open(path, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8", newline="\n") as out:
        for line in f:
            if block is None and line == "\t(via\n":
                block = [line]
            elif block is not None:
                block.append(line)
                if line == "\t)\n":
                    uuid = next(l.strip().split('"')[1] for l in block if l.strip().startswith("(uuid "))
                    want = nets.get(uuid)
                    for i, l in enumerate(block):
                        if want and l.strip().startswith("(net ") and l.strip() != want:
                            block[i] = l[:len(l) - len(l.lstrip("\t"))] + want + "\n"
                            changed += 1
                    out.writelines(block)
                    block = None
            else:
                out.write(line)
    os.replace(tmp, path)
    return changed


def plan_zones(path, plan):
    """Write zone priorities and inferred nets into a saved KiCad 10 file."""
    tmp = path + ".tmp"
    block = None
    with open(path, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8", newline="\n") as out:
        for line in f:
            if block is None and line == "\t(zone\n":
                block = [line]
            elif block is not None:
                block.append(line)
                if line == "\t)\n":
                    uuid = next(l.strip().split('"')[1] for l in block if l.startswith("\t\t(uuid "))
                    block = [l for l in block if not l.startswith("\t\t(priority ")]
                    at = next(i for i, l in enumerate(block) if l.startswith("\t\t(uuid "))
                    if uuid in plan["priority"]:
                        block.insert(at + 1, f"\t\t(priority {plan['priority'][uuid]})\n")
                    if uuid in plan["nets"] and not any(l.startswith("\t\t(net ") for l in block):
                        block.insert(1, f'\t\t(net "{plan["nets"][uuid]}")\n')
                    out.writelines(block)
                    block = None
            else:
                out.write(line)
    os.replace(tmp, path)


MAX_ARC_RADIUS_MM = 1000   # Altium's Int32 limit is ~5455 mm (1 mil = 10000 units)
COPPER = re.compile(r'^\(layer "(F\.Cu|B\.Cu|In\d+\.Cu)"\)$')


def arc_radius_mm(a, b, c):
    """Radius (or centre offset, if larger) of the circle through a, b, c; inf when collinear."""
    (ax, ay), (bx, by), (cx, cy) = a, b, c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if d == 0:
        return float("inf")
    ux = ((ax*ax + ay*ay) * (by - cy) + (bx*bx + by*by) * (cy - ay) + (cx*cx + cy*cy) * (ay - by)) / d
    uy = ((ax*ax + ay*ay) * (cx - bx) + (bx*bx + by*by) * (ax - cx) + (cx*cx + cy*cy) * (bx - ax)) / d
    return max(abs(ux), abs(uy), ((ax - ux) ** 2 + (ay - uy) ** 2) ** 0.5)


# Altium's importer turns a zone's (min_thickness W) into its polygon "Remove necks when copper width less than W"
# (0.25 mm -> REMOVENECKS=TRUE, NECKWIDTHTHRESHOLD=9.8425mil). Neck removal works like rolling a disc of that width
# through the poured copper: under a BGA, with the real ~4 mil plane-to-via clearance, the webs between vias are
# ~0.11 mm, so 0.25 mm cut the plane away (VCU118: SYS_1V8 under the U60-U62 DDR4 row, 2026-10-07). Allegro has
# already decided what copper exists; 1 mil keeps every real web and is effectively "off".
ALTIUM_MIN_THICKNESS_MM = 0.0254

# Altium's importer reads a zone outline's (xy) points and drops its (arc ...) segments: an arc-only outline imports
# as "0 vertices" (not at all), a mixed one loses its rounded parts. On the VCU118, 412 of the 726 netted copper
# shapes lost every via they hold -- the PCIe edge GND tabs (pills with a via in each round end: 5.00 -> 3.88 mm2)
# and 372 GND dots -- and Altium then removed the copper as dead (2026-10-07). So outline arcs are written as points.
ARC_SAGITTA_MM = 0.0025      # largest gap between an arc and its chords (0.1 mil)
_N = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
_XY = re.compile(rf"\(xy\s+{_N}\s+{_N}\)")
_ARC = re.compile(rf"\(arc\s+\(start\s+{_N}\s+{_N}\)\s+\(mid\s+{_N}\s+{_N}\)\s+\(end\s+{_N}\s+{_N}\)\s*\)")


def arc_points(start, mid, end, sagitta=ARC_SAGITTA_MM):
    """Points along the arc start -> mid -> end (start and end exact); start == end is a full circle through mid."""
    (sx, sy), (mx, my), (ex, ey) = start, mid, end
    if math.isclose(sx, ex, abs_tol=1e-9) and math.isclose(sy, ey, abs_tol=1e-9):
        cx, cy = (sx + mx) / 2, (sy + my) / 2
        span = 2 * math.pi
    else:
        d = 2 * (sx * (my - ey) + mx * (ey - sy) + ex * (sy - my))
        if abs(d) < 1e-12:
            return [start, end]
        cx = ((sx*sx + sy*sy) * (my - ey) + (mx*mx + my*my) * (ey - sy) + (ex*ex + ey*ey) * (sy - my)) / d
        cy = ((sx*sx + sy*sy) * (ex - mx) + (mx*mx + my*my) * (sx - ex) + (ex*ex + ey*ey) * (mx - sx)) / d
        a0, am, a1 = (math.atan2(y - cy, x - cx) for x, y in (start, mid, end))
        tau = 2 * math.pi
        span = (a1 - a0) % tau if (am - a0) % tau < (a1 - a0) % tau else -((a0 - a1) % tau)
    r = math.hypot(sx - cx, sy - cy)
    a0 = math.atan2(sy - cy, sx - cx)
    step = 2 * math.acos(1 - sagitta / r) if r > sagitta else math.pi / 2
    n = max(2, min(256, math.ceil(abs(span) / step)))
    pts = [(cx + r * math.cos(a0 + span * i / n), cy + r * math.sin(a0 + span * i / n)) for i in range(n + 1)]
    pts[0], pts[-1] = start, end
    return pts


def _num(v):
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def linearize_outline_arcs(lines):
    """Replace each (arc (start)(mid)(end)) in an outline's pts with (xy) points; return (lines, arcs replaced).

    Arcs may span several lines (KiCad 10 writes start/mid/end on their own lines). A point equal to the one just
    written (consecutive arcs share ends) is not repeated."""
    out, arcs, last, k = [], 0, None, 0
    while k < len(lines):
        line = lines[k]
        if line.strip().startswith("(arc"):
            indent = line[:len(line) - len(line.lstrip())]
            buf, depth = [line], line.count("(") - line.count(")")
            while depth > 0:
                k += 1
                buf.append(lines[k])
                depth += lines[k].count("(") - lines[k].count(")")
            m = _ARC.search(" ".join(b.strip() for b in buf))
            if not m:
                raise RuntimeError(f"unreadable outline arc: {' '.join(b.strip() for b in buf)}")
            v = [float(g) for g in m.groups()]
            for x, y in arc_points((v[0], v[1]), (v[2], v[3]), (v[4], v[5])):
                if last is not None and abs(x - last[0]) < 1e-6 and abs(y - last[1]) < 1e-6:
                    continue
                out.append(f"{indent}(xy {_num(x)} {_num(y)})")
                last = (x, y)
            arcs += 1
        else:
            found = _XY.findall(line)
            if found:
                last = (float(found[-1][0]), float(found[-1][1]))
            out.append(line)
        k += 1
    return out, arcs


def altium_block(block, counts, plan):
    """Rewrite one top-level item for Altium's KiCad importer; return its lines.

    Done on the text, not through pcbnew: KiCad re-derives via nets from the
    copper they touch when it saves, so any edit-and-save of the board moves
    via nets. Text rewriting leaves every other item exactly as written.

    - Near-straight track arc -> segment. Altium derives centre and radius from
      start/mid/end and overflows Int32 on a collinear arc, abandoning the whole
      import ("Value was either too large or too small for an Int32", empty
      PcbDoc). Allegro leaves sub-micron ones behind.
    - Netted copper gr_poly -> zone on the same net and outline, with its
      zone_plan priority. Altium's reader predates nets on graphic shapes and
      would import no-net copper.
    - Netless zone Allegro poured nothing into (zone_plan "empty") -> dropped.
    - Zone min_thickness -> ALTIUM_MIN_THICKNESS_MM, so Altium's re-pour does not neck away plane webs.
    - Copper zone / zoned gr_poly outline arcs -> points (Altium drops outline arcs; see ARC_SAGITTA_MM).
    - Copper zone pad connection: KiCad's default (thermal relief) -> solid. kicad-cli writes Allegro's dynamic
      shapes with KiCad's thermal defaults (0.5 mm gap and spokes), which Altium imports as Relief connects that
      leave pads in a bare ring between fine-pitch pins; Allegro's fill covers those pads (J6 pins 9/10: 99 %/84 %).
    """
    head = block[0].strip()
    fields = [l.strip() for l in block]
    uuid_line = next((f for f in fields if f.startswith("(uuid ")), None)
    uuid = uuid_line.split('"')[1] if uuid_line else None
    if head == "(zone" and uuid in plan["empty"]:
        counts["empty netless zones dropped"] += 1
        return []
    if head == "(zone":
        lowered = [f"\t\t(min_thickness {ALTIUM_MIN_THICKNESS_MM:g})" if l.startswith("\t\t(min_thickness ") else l
                   for l in block]
        if lowered != block:
            counts["zone neck widths lowered"] += 1
        if not any(f.startswith("(keepout") for f in fields):
            if "\t\t(connect_pads" in lowered:
                lowered[lowered.index("\t\t(connect_pads")] = "\t\t(connect_pads yes"
                counts["zone pad connections made solid"] += 1
            lowered, arcs = linearize_outline_arcs(lowered)
            if arcs:
                counts["outline arcs written as points"] += arcs
                counts["zones with outline arcs"] += 1
        return lowered
    if head == "(arc":
        pts = {}
        for f in fields:
            for key in ("start", "mid", "end"):
                if f.startswith(f"({key} "):
                    x, y = f[len(key) + 2:-1].split()
                    pts[key] = (float(x), float(y))
        if arc_radius_mm(pts["start"], pts["mid"], pts["end"]) > MAX_ARC_RADIUS_MM:
            counts["arcs straightened"] += 1
            return ["\t(segment"] + [l for l in block[1:] if not l.strip().startswith("(mid ")]
    elif head == "(gr_poly":
        layer = next((f for f in fields if f.startswith("(layer ")), "")
        net = next((f for f in fields if f.startswith("(net ")), '(net "")')
        if COPPER.match(layer) and net != '(net "")':
            i = fields.index("(pts")
            j = next(k for k in range(i + 1, len(block)) if block[k] == "\t\t)")
            counts["copper polygons zoned"] += 1
            pts, arcs = linearize_outline_arcs(block[i:j + 1])
            if arcs:
                counts["outline arcs written as points"] += arcs
                counts["zones with outline arcs"] += 1
            return (["\t(zone", f"\t\t{net}", f"\t\t{layer}", f"\t\t{uuid_line}",
                     f"\t\t(priority {plan['priority'][uuid]})", "\t\t(hatch edge 0.5)",
                     "\t\t(connect_pads yes", "\t\t\t(clearance 0)", "\t\t)", f"\t\t(min_thickness {ALTIUM_MIN_THICKNESS_MM:g})",
                     "\t\t(fill", "\t\t\t(thermal_gap 0.5)", "\t\t\t(thermal_bridge_width 0.5)", "\t\t)",
                     "\t\t(polygon"]
                    + ["\t" + l for l in pts]
                    + ["\t\t)", "\t)"])
    return block


# KiCad 6/7/8 layer numbers. KiCad 9 renumbered (copper even, technical odd);
# Altium places items by the number in the layer table, read the old way, so a
# KiCad 9 table scrambles the stack (In19.Cu = 40 landed on BottomOverlay).
V6_LAYER_IDS = {"F.Cu": 0, **{f"In{i}.Cu": i for i in range(1, 31)}, "B.Cu": 31,
                "B.Adhes": 32, "F.Adhes": 33, "B.Paste": 34, "F.Paste": 35,
                "B.SilkS": 36, "F.SilkS": 37, "B.Mask": 38, "F.Mask": 39,
                "Dwgs.User": 40, "Cmts.User": 41, "Eco1.User": 42, "Eco2.User": 43,
                "Edge.Cuts": 44, "Margin": 45, "B.CrtYd": 46, "F.CrtYd": 47,
                "B.Fab": 48, "F.Fab": 49, **{f"User.{i}": 49 + i for i in range(1, 10)}}
LAYER_ROW = re.compile(r'^\t\t\((\d+) "([^"]+)"(.*)\)$')


def renumber_layers(rows):
    """Rewrite the board's layer-table rows with KiCad 6 numbers, in number order."""
    out = []
    for row in rows:
        m = LAYER_ROW.match(row)
        if not m or m.group(2) not in V6_LAYER_IDS:
            raise RuntimeError(f"no KiCad 6 number for layer row {row.strip()}")
        out.append((V6_LAYER_IDS[m.group(2)], f'\t\t({V6_LAYER_IDS[m.group(2)]} "{m.group(2)}"{m.group(3)})'))
    return [row for _, row in sorted(out)]


NET_REF = re.compile(r'^(\t+)\(net "((?:[^"\\]|\\.)*)"\)$')


def _is_keepout(block):
    return any(l.strip().startswith("(keepout") for l in block)


def pour_order(zones, plan):
    """Zone blocks in the order Altium should POUR them: smallest outline first, netless last.

    Allegro lets a split island cut out of the plane around it; in Altium the polygon that pours first
    wins, so a full-layer plane poured first starves every smaller shape inside it (53 polygons lost
    their copper this way). ``plan["priority"]`` ranks zones by area with the largest at 0 (KiCad's
    meaning: the higher number wins), so smallest-first is descending priority. ``altium_priority``
    writes the resulting rank into the Altium copies.

    Keepout rule areas pour nothing and sort to the front. The sort is stable, so ties keep file order.
    """
    def uuid(block):
        return next((l.strip().split('"')[1] for l in block if l.strip().startswith("(uuid ")), None)

    def netless(block):
        for l in block:
            m = NET_REF.match(l)
            if m and l.startswith("\t\t(net "):
                return m.group(2) == ""
        return True

    def key(block):
        if _is_keepout(block):
            return (0, False, 0)
        return (1, netless(block), -plan["priority"].get(uuid(block), -1))

    return sorted(zones, key=key)


def altium_priority(block, rank):
    """A copper zone block with ``(priority rank)``: its place in ``pour_order``, so the first to pour gets 0.

    Altium's KiCad importer copies ``(priority N)`` straight into the pour order (``POURINDEX``, ascending),
    and the lowest pour index pours first and wins -- the reverse of KiCad, where the higher priority wins.
    Measured 2026-10-07 on two VCU118 imports written in opposite file orders: POURINDEX ranked against the
    zone's priority with Spearman +1.0 both times, against its file position +1.0 and -1.0. With the plan's
    KiCad priorities (largest zone 0) every full-layer plane poured first and starved the islands in it;
    file order does nothing.
    """
    out = [l for l in block if not l.startswith("\t\t(priority ")]
    at = next(i for i, l in enumerate(out) if l.startswith("\t\t(uuid "))
    return out[:at + 1] + [f"\t\t(priority {rank})"] + out[at + 1:]
# Lines/blocks introduced after KiCad 9 (format 20241229) that it would reject.
V10_ONLY = ("(duplicate_pad_numbers_are_jumpers ", "(covering", "(plugging", "(capping", "(filling")


def downgrade_to_kicad9(src, dst, plan, keep_fills=True):
    """Rewrite a KiCad 10 board as format 20241229 (KiCad 9).

    Altium 26 reads KiCad up to 9; given a v10 file it imports an empty board.
    The material difference is nets: v10 (20251028) stopped writing netcodes, so
    every reference is (net "NAME") and there is no net table. KiCad 9 needs the
    table and numbered references, whose form depends on the owner: pads
    (net N "NAME"), zones (net N) (net_name "NAME"), everything else (net N).

    keep_fills=False drops the zones' filled_polygon blocks (the poured copper),
    leaving outlines to be re-poured. KiCad 10 hangs, blocked, loading a
    20241229 file that carries fills, so only the unfilled copy can be checked
    by loading it back.
    """
    dropped = V10_ONLY if keep_fills else V10_ONLY + ("(filled_polygon",)
    names = set()
    with open(src, encoding="utf-8") as f:
        for line in f:
            m = NET_REF.match(line.rstrip("\n"))
            if m:
                names.add(m.group(2))
    codes = {"": 0}
    for n in sorted(names - {""}):
        codes[n] = len(codes)

    counts = collections.Counter()

    def source_lines(f):
        """The file's lines: layer table renumbered, top-level arc / gr_poly blocks run through altium_block."""
        block, layer_rows, zones = None, None, []
        for line in f:
            body = line.rstrip("\n")
            if layer_rows is not None:
                if body == "\t)":
                    yield from renumber_layers(layer_rows)
                    yield body
                    layer_rows = None
                else:
                    layer_rows.append(body)
            elif body == "\t(layers":
                layer_rows = []
                yield body
            elif block is None and body in ("\t(arc", "\t(gr_poly", "\t(zone"):
                block = [body]
            elif block is not None:
                block.append(body)
                if body == "\t)":
                    done = altium_block(block, counts, plan)
                    if done and done[0].strip() == "(zone":
                        zones.append(done)      # held back: written in pour order, ranked, at the end
                    else:
                        yield from done
                    block = None
            elif body == ")" and zones:
                rank = 0
                for z in pour_order(zones, plan):
                    if not _is_keepout(z):
                        z = altium_priority(z, rank)
                        rank += 1
                    yield from z
                counts["zones written in pour order"] = len(zones)
                counts["copper zones given their pour rank as priority"] = rank
                zones.clear()
                yield body
            else:
                yield body

    heads = []          # owner head at each depth
    skip_depth = None   # inside a dropped multi-line block
    in_setup = False
    stroke = None       # buffered (stroke ...) block: [depth, lines]
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8", newline="\n") as out:
        for body in source_lines(f):
            depth = len(body) - len(body.lstrip("\t"))
            # Line widths live in (stroke (width W) ...) since KiCad 7; Altium's
            # reader skips stroke and falls back to 0.254 mm. KiCad still reads
            # the older sibling (width W), so write both.
            if stroke is not None:
                stroke[1].append(body)
                if depth == stroke[0] and body.strip() == ")":
                    width = next((l.strip() for l in stroke[1] if l.strip().startswith("(width ")), None)
                    if width:
                        out.write("\t" * stroke[0] + width + "\n")
                    out.write("\n".join(stroke[1]) + "\n")
                    stroke = None
                continue
            if body.strip() == "(stroke":
                stroke = [depth, [body]]
                continue
            if skip_depth is not None:
                if depth == skip_depth and body.strip() == ")":
                    skip_depth = None
                continue
            stripped = body.strip()
            if stripped.startswith(dropped):
                if not stripped.endswith(")") or stripped.count("(") > stripped.count(")"):
                    skip_depth = depth
                continue
            if depth == 1 and stripped.startswith("(version "):
                body = "\t(version 20241229)"
            elif depth == 1 and stripped.startswith("(generator_version "):
                body = '\t(generator_version "9.0")'
            m = NET_REF.match(body)
            if m:
                indent, name = m.groups()
                owner = heads[depth - 1] if depth - 1 < len(heads) else ""
                code = codes[name]
                if owner == "pad":
                    body = f'{indent}(net {code} "{name}")'
                elif owner == "zone":
                    body = f'{indent}(net {code})\n{indent}(net_name "{name}")'
                else:
                    body = f"{indent}(net {code})"
            if stripped.startswith("("):
                del heads[depth:]
                heads.append(stripped[1:].split()[0].rstrip(")"))
            out.write(body + "\n")
            if depth == 1 and stripped.startswith("(setup"):
                in_setup = True
            elif in_setup and depth == 1 and stripped == ")":
                in_setup = False
                for n, code in codes.items():
                    out.write(f'\t(net {code} "{n}")\n')
    return len(codes), counts


