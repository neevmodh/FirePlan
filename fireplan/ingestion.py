from __future__ import annotations

import io
import math
import re

import ezdxf
import networkx as nx
from shapely import set_precision
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, polygonize, unary_union

from .models import Network

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
PIPE_ALIAS = re.compile(r"^(?:WATER_)?PIPES(?:_(?:MAIN|BRANCH))?$", re.I)
HAZARD = re.compile(rf"^ZONE_(\d+)\s*:\s*([A-Za-z0-9_ -]+?)\s*\(\s*RISK_WEIGHT\s*=\s*({NUMBER})\s*\)\s*$", re.I)
PUMP = re.compile(rf"^MAIN_PUMP_SOURCE(?:\s*\(\s*P_in\s*=\s*({NUMBER})\s*bar\s*\))?\s*$", re.I)


def straight_points(entity, *, strict=False):
    if entity.dxftype() == "LINE":
        planar = abs(entity.dxf.start.z) <= 1e-6 and abs(entity.dxf.end.z) <= 1e-6
        if planar:
            return [(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)]
    elif entity.dxftype() == "LWPOLYLINE":
        planar = abs(entity.dxf.elevation) <= 1e-6 and not any(abs(p[4]) > 1e-9 for p in entity.get_points())
        if planar:
            points = list(entity.get_points("xy"))
            if entity.closed and points and points[0] != points[-1]:
                points.append(points[0])
            return points
    else:
        if strict:
            raise ValueError(f"Unsupported {entity.dxftype()} on pipe layer; use straight LINE/LWPOLYLINE")
        return []
    if strict:
        raise ValueError("Only planar z=0 straight pipes are supported; convert curved or elevated geometry")
    return []


def ingest_dxf(content: bytes, *, metres_per_unit: float, source_x: float | None = None,
               source_y: float | None = None, pipe_layer: str = "PIPES", diameter_mm: float = 100) -> Network:
    """Read explicit pipe conventions, candidate symbols and risk data.

    Structural walls only provide drawing context and labeled room boundaries.
    They never become water pipes. Source and candidate points split pipe edges.
    """
    values = [metres_per_unit, diameter_mm, *[n for n in (source_x, source_y) if n is not None]]
    if not all(math.isfinite(n) for n in values) or metres_per_unit <= 0:
        raise ValueError("Supply coordinates and unit scale must be finite; scale must be positive")
    if (source_x is None) != (source_y is None):
        raise ValueError("Provide both supply coordinates, or leave both unset for pump detection")
    if not 10 <= diameter_mm <= 2000:
        raise ValueError("Diameter must be between 10 and 2000 mm")
    try:
        doc = ezdxf.read(io.StringIO(content.decode("utf-8-sig"), newline=None))
    except (UnicodeError, ezdxf.DXFError) as error:
        raise ValueError("Upload an ASCII DXF file; convert binary DXF or DWG before importing") from error
    entities = list(doc.modelspace())
    layers = {entity.dxf.layer for entity in entities}
    pipe_layers = {layer for layer in layers if (PIPE_ALIAS.fullmatch(layer) if pipe_layer.casefold() == "pipes"
                                                else layer.casefold() == pipe_layer.casefold())}
    scale = metres_per_unit
    def xy(point):
        return (float(point[0]) * scale, float(point[1]) * scale)
    lines, zones, drawing_lines, wall_lines = [], [], [], []
    pipe_markers, pump_labels, hazard_labels, candidates, candidate_labels = [], [], [], [], []
    candidate_layer_present = False
    for entity in entities:
        layer, kind = entity.dxf.layer, entity.dxftype()
        text = (entity.dxf.text if kind == "TEXT" else entity.plain_text() if kind == "MTEXT" else "").strip()
        if text:
            pump = PUMP.fullmatch(text)
            if pump:
                pump_labels.append((entity, float(pump.group(1)) if pump.group(1) else None))
            hazard = HAZARD.fullmatch(text) if layer.casefold() == "hazard_labels" else None
            if hazard:
                hazard_labels.append((hazard, xy(entity.dxf.insert)))
        if layer.casefold() == "candidate_nodes":
            candidate_layer_present = True
            if kind == "CIRCLE":
                if abs(entity.dxf.center.z) > 1e-6:
                    raise ValueError("Candidate nodes must be planar z=0 circles")
                candidates.append((xy(entity.dxf.center), float(entity.dxf.radius) * scale))
            elif text and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", text):
                candidate_labels.append((text, xy(entity.dxf.insert)))
        if layer in pipe_layers:
            if kind == "CIRCLE":
                if abs(entity.dxf.center.z) > 1e-6:
                    raise ValueError("Pump markers must be planar z=0")
                pipe_markers.append(entity)
                continue
            if kind in {"TEXT", "MTEXT"}:
                continue
            points = straight_points(entity, strict=True)
            for a, b in zip(points, points[1:]):
                line = LineString([xy(a), xy(b)])
                if line.length > .0001:
                    lines.append(set_precision(line, .0001))
            if len(lines) > 1000:
                raise ValueError("DXF exceeds the prototype import limit of 1000 pipe segments")
            continue
        risk = re.fullmatch(r"RISK_(\d+(?:\.\d+)?)", layer, re.I)
        if risk:
            points = straight_points(entity)
            if kind != "LWPOLYLINE" or not points or points[0] != points[-1]:
                raise ValueError("RISK_n layers must contain closed straight planar LWPOLYLINE boundaries")
            zones.append({"id": f"Z{len(zones)+1}", "label": f"Imported zone {len(zones)+1}",
                          "risk": float(risk.group(1)), "polygon": [xy(p) for p in points[:-1]]})
            continue
        for a, b in zip((points := straight_points(entity)), points[1:]):
            pair = (xy(a), xy(b))
            drawing_lines.append(pair)
            if layer.casefold() in {"structural_walls", "walls"} and pair[0] != pair[1]:
                wall_lines.append(set_precision(LineString(pair), .0001))
        if len(drawing_lines) > 2000:
            raise ValueError("CAD context exceeds 2000 straight segments; remove non-plan details before importing")
    if not lines:
        raise ValueError("No pipe segments found. Use PIPES, WATER_PIPES_MAIN or WATER_PIPES_BRANCH layers. "
                         + "Drawing layers: " + ", ".join(sorted(layers)))
    risk_method = "explicit_polygons"
    if not zones:
        if not hazard_labels or not wall_lines:
            raise ValueError("Add closed RISK_0–RISK_10 zone polygons, or HAZARD_LABELS with ZONE_n: NAME (RISK_WEIGHT=value) inside enclosed STRUCTURAL_WALLS rooms")
        rooms = list(polygonize(unary_union(wall_lines)))
        assigned = set()
        for label, position in hazard_labels:
            containing = [i for i, room in enumerate(rooms) if room.covers(Point(position))]
            if len(containing) != 1:
                raise ValueError(f"ZONE_{label.group(1)} must sit inside exactly one enclosed structural room")
            index = containing[0]
            if index in assigned or rooms[index].interiors:
                raise ValueError("Risk room labels must be unique and boundaries must have no holes; use explicit RISK_n polygons")
            assigned.add(index)
            zones.append({"id": "Z" + label.group(1), "label": label.group(2).replace("_", " ").strip(),
                          "risk": float(label.group(3)), "polygon": list(rooms[index].exterior.coords)[:-1]})
        if len(assigned) != len(rooms):
            raise ValueError("Every enclosed structural room needs a risk label, or explicit RISK_n zone polygons")
        risk_method = "labeled_rooms"
    pump_point, pressure = None, None
    if pump_labels:
        if len(pump_labels) != 1 or len(pipe_markers) != 1:
            raise ValueError("Pump detection needs one MAIN_PUMP_SOURCE label and one pipe-layer circle marker")
        label, pressure = pump_labels[0]
        marker = pipe_markers[0]
        position, pump_point = xy(label.dxf.insert), xy(marker.dxf.center)
        text_height = float(label.dxf.height if label.dxftype() == "TEXT" else label.dxf.char_height) * scale
        if math.dist(position, pump_point) > max(.01, 4 * text_height + 2 * marker.dxf.radius * scale):
            raise ValueError("MAIN_PUMP_SOURCE label must be next to its pump circle")
    automatic = source_x is None and pump_point is not None
    supply = pump_point if automatic else xy((source_x or 0, source_y or 0))
    if pump_point is None or math.dist(supply, pump_point) > .01:
        pressure = None
    merged = unary_union(lines)
    def on_pipe(position, name):
        point = Point(position)
        if merged.distance(point) > .01:
            raise ValueError(f"{name} must match a pipe endpoint, segment or intersection within 1 cm")
        nearest = nearest_points(merged, point)[0]
        return tuple(set_precision(nearest, .0001).coords[0])
    supply = on_pipe(supply, "Source")
    marked = {}
    used_labels = set()
    if candidate_layer_present and not candidates:
        raise ValueError("CANDIDATE_NODES requires circle markers for placement locations")
    for position, radius in candidates:
        point = on_pipe(position, "Candidate node")
        if point in marked:
            raise ValueError("Candidate circle locations must be unique")
        nearby = sorted(candidate_labels, key=lambda label: math.dist(position, label[1]))
        identifier = nearby[0][0] if nearby and math.dist(position, nearby[0][1]) <= max(.01, 5 * radius) else f"C{len(marked)+1:03d}"
        if identifier in used_labels or identifier == "SUPPLY":
            raise ValueError("Candidate labels must be unique and cannot use SUPPLY")
        used_labels.add(identifier)
        marked[point] = identifier
    extra = set(marked) | {supply}
    segments = [merged] if isinstance(merged, LineString) else list(merged.geoms)
    graph = nx.Graph()
    for segment in segments:
        coordinates = list(segment.coords)
        for a, b in zip(coordinates, coordinates[1:]):
            line = LineString([a, b])
            cuts = sorted({tuple(a), tuple(b)} | {p for p in extra if line.distance(Point(p)) <= .00015},
                          key=lambda p: line.project(Point(p)))
            for start, end in zip(cuts, cuts[1:]):
                if start != end:
                    graph.add_edge(start, end, length=math.dist(start, end))
    if not graph or not nx.is_connected(graph):
        raise ValueError("Pipe layer contains disconnected components; connect every junction to the water source")
    if not extra.issubset(graph.nodes):
        raise ValueError("Source and candidate markers could not be connected to the pipe graph")
    if candidate_layer_present:
        ids = {point: "SUPPLY" if point == supply else marked.get(point, f"J{i+1:03d}")
               for i, point in enumerate(sorted(graph.nodes))}
        if len(set(ids.values())) != len(ids):
            raise ValueError("Candidate IDs conflict with generated pipe junction IDs")
    else:
        ids = {point: f"N{i+1:03d}" for i, point in enumerate(sorted(graph.nodes))}
    return Network.model_validate({"name": "Imported CAD network", "description": "Explicit CAD pipes, risk regions and placement locations.",
        "source_id": ids[supply],
        "nodes": [{"id": ids[p], "x": p[0], "y": p[1],
                   "candidate": p != supply and (p in marked if candidate_layer_present else True),
                   "label": "Water source" if p == supply else ids[p]} for p in ids],
        "pipes": [{"id": f"P{i+1:03d}", "start": ids[a], "end": ids[b], "length_m": attr["length"],
                   "diameter_mm": diameter_mm} for i, (a, b, attr) in enumerate(graph.edges(data=True))],
        "zones": zones, "drawing_lines": drawing_lines,
        "import_details": {"pipe_layers": sorted(pipe_layers), "source_coordinates": (supply[0]/scale, supply[1]/scale),
                           "source_autodetected": automatic, "suggested_source_pressure_bar": pressure, "risk_method": risk_method}})
