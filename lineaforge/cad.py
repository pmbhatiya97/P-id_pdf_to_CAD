"""Traceable 2D CAD backends; native CAD is optional and never simulated."""
from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Protocol, runtime_checkable

from .models import PidGraph

ATTRIBUTE_TAGS = ("TAG", "SERVICE", "SIZE", "SPEC", "SOURCE_ID", "CONFIDENCE")
_DLL_DIRECTORIES = []


def _prepare_windows_runtime():
    """Support the development pip --target layout as well as normal installs."""
    if os.name != "nt":
        return
    root = Path(__file__).resolve().parent.parent / ".runtime"
    for child in ("win32", "win32/lib", "pywin32_system32"):
        path = root / child
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    dll = root / "pywin32_system32"
    if dll.is_dir() and hasattr(os, "add_dll_directory") and not _DLL_DIRECTORIES:
        _DLL_DIRECTORIES.append(os.add_dll_directory(str(dll)))
LAYER_DEFAULTS = {
    "EQUIPMENT": (7, "CONTINUOUS"), "PIPING-PROCESS": (7, "CONTINUOUS"),
    "PIPING-UTILITY": (4, "CONTINUOUS"), "PIPING-DRAIN-VENT": (3, "CONTINUOUS"),
    "INSTR-FIELD": (6, "CONTINUOUS"), "INSTR-PANEL": (6, "CONTINUOUS"),
    "SIGNAL-ELECTRIC": (2, "LF-DASHED"), "SIGNAL-PNEUMATIC": (2, "LF-DASHDOTDOT"),
    "SIGNAL-DATA": (2, "LF-DASHDOTDOT"), "VALVES": (7, "CONTINUOUS"),
    "TEXT-TAGS": (7, "CONTINUOUS"), "TEXT-LINE-NUMBERS": (7, "CONTINUOUS"),
    "NOTES": (7, "CONTINUOUS"), "TITLEBLOCK": (8, "CONTINUOUS"),
    "REVISION-CLOUDS": (1, "CONTINUOUS"), "MARKUPS": (1, "CONTINUOUS"),
    "REVIEW-FLAGS": (1, "LF-DASHED"),
}


class CadError(RuntimeError):
    pass


class CadUnavailable(CadError):
    pass


@runtime_checkable
class CadBackend(Protocol):
    def create_layer(self, name: str, color: int = 7, linetype: str = "CONTINUOUS", lineweight: int = 25) -> Any: ...
    def define_block(self, name: str, geometry: list[dict], attribute_tags: tuple[str, ...] = ATTRIBUTE_TAGS) -> Any: ...
    def insert_block(self, name: str, position: list[float], attributes: dict[str, str], *, layer: str = "0", scale: float = 1, rotation: float = 0, source_id: str = "") -> Any: ...
    def draw_polyline(self, points: list[list[float]], *, layer: str = "0", closed: bool = False, source_id: str = "") -> Any: ...
    def add_text(self, text: str, position: list[float], height: float, *, layer: str = "0", rotation: float = 0, source_id: str = "") -> Any: ...
    def save(self, output_path: str | Path) -> dict: ...


def _points(points):
    result = [[float(p[0]), float(p[1])] for p in points]
    if any(not math.isfinite(v) for p in result for v in p):
        raise CadError("CAD coordinates must be finite")
    return result


class EzdxfBackend:
    """Editable DXF with literal extracted geometry and provenance XDATA."""

    def __init__(self):
        import ezdxf
        self.doc = ezdxf.new("R2013")
        self.doc.units = 4  # millimetres
        self.space = self.doc.modelspace()
        self.doc.appids.new("LINEAFORGE")
        self.doc.linetypes.new("LF-DASHED", dxfattribs={"pattern": [.6, .4, -.2]})
        self.doc.linetypes.new("LF-DASHDOTDOT", dxfattribs={"pattern": [1.1, .5, -.2, 0, -.2, 0, -.2]})

    def _source(self, entity, source_id):
        if source_id:
            entity.set_xdata("LINEAFORGE", [(1000, str(source_id)[:255])])
        return entity.dxf.handle

    def create_layer(self, name, color=7, linetype="CONTINUOUS", lineweight=25):
        if linetype not in self.doc.linetypes:
            raise CadError(f"Unknown linetype: {linetype}")
        if name not in self.doc.layers:
            self.doc.layers.new(name, dxfattribs={"color": int(color), "linetype": linetype, "lineweight": int(lineweight)})
        return name

    def define_block(self, name, geometry, attribute_tags=ATTRIBUTE_TAGS):
        if name in self.doc.blocks:
            return name
        block = self.doc.blocks.new(name)
        for shape in geometry:
            kind = shape.get("type", "polyline")
            if kind == "polyline":
                pts = _points(shape["points"])
                if len(pts) >= 2:
                    block.add_lwpolyline(pts, close=bool(shape.get("closed", False)))
            elif kind == "circle":
                block.add_circle(_points([shape["center"]])[0], float(shape["radius"]))
            elif kind == "arc":
                block.add_arc(_points([shape["center"]])[0], float(shape["radius"]), float(shape["start_angle"]), float(shape["end_angle"]))
            else:
                raise CadError(f"Unsupported literal block primitive: {kind}")
        for index, tag in enumerate(attribute_tags):
            block.add_attdef(tag, (0, -2.5 * (index + 1)), height=2,
                             dxfattribs={"flags": 0 if tag == "TAG" else 1})
        return name

    def insert_block(self, name, position, attributes, *, layer="0", scale=1, rotation=0, source_id=""):
        ref = self.space.add_blockref(name, _points([position])[0], dxfattribs={"layer": layer, "xscale": scale, "yscale": scale, "rotation": rotation})
        ref.add_auto_attribs({str(k): str(v) for k, v in attributes.items()})
        return self._source(ref, source_id)

    def draw_polyline(self, points, *, layer="0", closed=False, source_id=""):
        pts = _points(points)
        if len(pts) < 2:
            raise CadError("A polyline requires at least two points")
        return self._source(self.space.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer}), source_id)

    def draw_circle(self, center, radius, *, layer="0", source_id=""):
        return self._source(self.space.add_circle(center, radius, dxfattribs={"layer": layer}), source_id)

    def draw_arc(self, center, radius, start_angle, end_angle, *, layer="0", source_id=""):
        return self._source(self.space.add_arc(center, radius, start_angle, end_angle, dxfattribs={"layer": layer}), source_id)

    def add_text(self, text, position, height, *, layer="0", rotation=0, source_id=""):
        return self._source(self.space.add_text(str(text), dxfattribs={"insert": _points([position])[0], "height": max(float(height), .1), "layer": layer, "rotation": rotation}), source_id)

    def save(self, output_path):
        import ezdxf
        path = Path(output_path).resolve()
        if path.suffix.lower() != ".dxf":
            raise CadError("EzdxfBackend writes DXF. Native DWG requires a configured ODA/LibreDWG converter or licensed AutoCAD MCP.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.saveas(path)
        reopened = ezdxf.readfile(path)
        audit = reopened.audit()
        entities = list(reopened.modelspace())
        return {"output_path": str(path), "format": "DXF", "backend": "ezdxf", "reopened": True,
                "entity_count": len(entities), "entities_per_layer": dict(Counter(e.dxf.layer for e in entities)),
                "entity_types": dict(Counter(e.dxftype() for e in entities)),
                "source_ids": sorted({str(t.value) for e in entities if e.has_xdata("LINEAFORGE") for t in e.get_xdata("LINEAFORGE") if t.code == 1000}),
                "audit_errors": len(audit.errors), "audit_fixes": len(audit.fixes)}


class AutoCadMcpBackend:
    """Queues a build and executes real tool calls in one MCP stdio session."""

    def __init__(self, command=None, args=None, *, output_root=None, timeout_seconds=180):
        self.output_root = Path(output_root or Path.cwd()).resolve()
        if command is None and getattr(sys, "frozen", False):
            helper = Path(sys.executable).resolve().with_name("LineaForgeCadServer.exe")
            if not helper.is_file():
                raise CadUnavailable("Frozen builds require the sibling LineaForgeCadServer.exe helper. Install that helper or explicitly configure a source Python executable and CAD server arguments.")
            self.command = str(helper)
            default_args = ["--output-root", str(self.output_root)]
        else:
            self.command = command or sys.executable
            default_args = ["-m", "lineaforge.cad_server", "--output-root", str(self.output_root)]
        self.args = list(args) if args is not None else default_args
        self.timeout_seconds = timeout_seconds
        self.commands = [("new_drawing", {}), ("set_units", {"units": "mm"})]

    def _queue(self, name, arguments):
        self.commands.append((name, arguments))
        return f"queued:{len(self.commands)}"

    def create_layer(self, name, color=7, linetype="CONTINUOUS", lineweight=25):
        return self._queue("create_layer", dict(name=name, color=color, linetype=linetype, lineweight=lineweight))

    def define_block(self, name, geometry, attribute_tags=ATTRIBUTE_TAGS):
        return self._queue("define_block", dict(name=name, geometry=geometry, attribute_tags=list(attribute_tags)))

    def insert_block(self, name, position, attributes, *, layer="0", scale=1, rotation=0, source_id=""):
        return self._queue("insert_block", dict(name=name, position=position, attributes=attributes, layer=layer, scale=scale, rotation=rotation, source_id=source_id))

    def draw_polyline(self, points, *, layer="0", closed=False, source_id=""):
        return self._queue("draw_polyline", dict(points=_points(points), layer=layer, closed=closed, source_id=source_id))

    def add_text(self, text, position, height, *, layer="0", rotation=0, source_id=""):
        return self._queue("add_text", dict(text=text, position=position, height=height, layer=layer, rotation=rotation, source_id=source_id))

    async def _execute(self, path):
        _prepare_windows_runtime()
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise CadUnavailable("The optional mcp package is required for AutoCAD MCP") from exc
        project = Path(__file__).resolve().parent.parent
        python_paths = [str(project)]
        if (project / ".runtime").is_dir():
            python_paths.append(str(project / ".runtime"))
        params = StdioServerParameters(command=self.command, args=self.args,
                                       env={"PYTHONPATH": os.pathsep.join(python_paths)})
        async with asyncio.timeout(self.timeout_seconds):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    last = {}
                    for tool, arguments in [*self.commands, ("save_drawing", {"output_path": str(path)}), ("reopen_and_enumerate", {"output_path": str(path)})]:
                        result = await session.call_tool(tool, arguments=arguments)
                        blocks = [x.text for x in result.content if getattr(x, "type", "") == "text"]
                        if result.isError:
                            raise CadUnavailable(f"CAD tool {tool} failed: {' '.join(blocks)[:1000]}")
                        if blocks:
                            try:
                                last = json.loads(blocks[-1])
                            except json.JSONDecodeError:
                                last = {"message": blocks[-1]}
                    return {**last, "output_path": str(path), "backend": "autocad_mcp", "format": path.suffix[1:].upper(), "tool_calls": len(self.commands) + 2}

    def save(self, output_path):
        path = Path(output_path).resolve()
        if not path.is_relative_to(self.output_root):
            raise CadError("CAD output must stay inside the configured output root")
        if path.suffix.lower() not in (".dwg", ".dxf"):
            raise CadError("Native CAD output must be .dwg or .dxf")
        def execute():
            try:
                return asyncio.run(self._execute(path))
            except BaseExceptionGroup as exc:
                def leaves(error):
                    if isinstance(error, BaseExceptionGroup):
                        return [e for child in error.exceptions for e in leaves(child)]
                    return [error]
                errors = leaves(exc)
                known = next((e for e in errors if isinstance(e, CadError)), None)
                if known:
                    raise known from exc
                raise CadUnavailable("MCP connection failed: " + "; ".join(str(e) for e in errors)[:1000]) from exc
            except TimeoutError as exc:
                raise CadUnavailable("CAD MCP operation exceeded its configured timeout") from exc
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return execute()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(execute).result()


class SolidWorksMcpBackend(AutoCadMcpBackend):
    """Optional external SolidWorks server using the documented tool contract."""

    def __init__(self, command=None, args=None, **kwargs):
        if not command or args is None:
            raise CadUnavailable("SolidWorks generation is unavailable: configure a licensed local SolidWorks MCP server implementing the CadBackend tools. The bundled helper supports AutoCAD only.")
        super().__init__(command, args, **kwargs)


def build_graph_cad(graph: PidGraph | dict, backend: CadBackend, output_path, mode="fidelity") -> dict:
    """Map source pixels to millimetres without inventing missing process shapes."""
    graph = graph if isinstance(graph, PidGraph) else PidGraph.model_validate(graph)
    if mode not in ("fidelity", "clean"):
        raise CadError("mode must be fidelity or clean")
    sx, sy = graph.width_mm / graph.width_px, graph.height_mm / graph.height_px

    def cad(point):
        x, y = point[0] * sx, graph.height_mm - point[1] * sy
        return [round(x), round(y)] if mode == "clean" else [x, y]

    for name, (color, linetype) in LAYER_DEFAULTS.items():
        backend.create_layer(name, color, linetype)
    review = []
    counts = Counter()
    expected_layers = Counter()
    built_ids = []
    for element in graph.elements:
        if element.status == "REJECTED":
            counts["rejected"] += 1
            continue
        attrs = element.attributes
        layer = {"equipment": "EQUIPMENT", "valve": "VALVES", "instrument": "INSTR-FIELD", "pipe": "PIPING-PROCESS", "signal": "SIGNAL-ELECTRIC", "text": "TEXT-TAGS", "annotation": "NOTES", "connector": "EQUIPMENT", "junction": "PIPING-PROCESS"}[element.kind]
        requested_layer = attrs.get("layer")
        if requested_layer in LAYER_DEFAULTS:
            layer = requested_layer
        if element.kind == "pipe":
            line_class = attrs.get("line_class", "").lower()
            if line_class in ("utility", "drain", "vent"):
                layer = "PIPING-UTILITY" if line_class == "utility" else "PIPING-DRAIN-VENT"
        if element.kind == "signal" and attrs.get("signal_type") in ("pneumatic", "data"):
            layer = "SIGNAL-" + attrs["signal_type"].upper()
        x0, y0, x1, y1 = element.bbox
        origin = cad([x0, y1])
        points = [cad(p) for p in element.points]
        unresolved = element.status in ("UNREADABLE", "LOW_CONFIDENCE")
        if element.kind in ("pipe", "signal") and len(points) >= 2:
            backend.draw_polyline(points, layer=layer, source_id=element.id)
            counts["polylines"] += 1
            expected_layers[layer] += 1
        elif element.kind in ("text", "annotation") and (element.text or element.tag) and element.status != "UNREADABLE":
            backend.add_text(element.text or element.tag, origin, max((y1 - y0) * sy * .75, .5), layer=layer, rotation=-element.rotation, source_id=element.id)
            counts["texts"] += 1
            expected_layers[layer] += 1
        elif element.kind in ("equipment", "valve", "instrument", "connector", "junction"):
            literal = len(points) >= 2
            if literal:
                geometry = [{"type": "polyline", "points": [[p[0] - origin[0], p[1] - origin[1]] for p in points], "closed": attrs.get("closed", "false").lower() == "true"}]
            else:
                # A review rectangle is deliberately not a guessed engineering symbol.
                width, height = max((x1 - x0) * sx, .5), max((y1 - y0) * sy, .5)
                geometry = [{"type": "polyline", "points": [[0, 0], [width, 0], [width, height], [0, height]], "closed": True}]
                unresolved = True
                layer = "REVIEW-FLAGS"
                review.append({"element_id": element.id, "reason": "No literal symbol geometry supplied; review rectangle is a fallback, not a verified catalog symbol."})
            digest = hashlib.sha256(element.id.encode("utf-8")).hexdigest()[:12]
            name = "LF_" + re.sub(r"[^A-Za-z0-9_-]", "_", element.subclass)[:35] + "_" + re.sub(r"[^A-Za-z0-9_-]", "_", element.id)[-45:] + "_" + digest
            backend.define_block(name, geometry)
            metadata = {"TAG": "" if element.status == "UNREADABLE" else (element.tag or ""), "SERVICE": attrs.get("service", ""), "SIZE": attrs.get("size", ""), "SPEC": attrs.get("spec", ""), "SOURCE_ID": element.id, "CONFIDENCE": f"{element.confidence:.6f}"}
            # Literal points are already in page coordinates; do not rotate twice.
            backend.insert_block(name, origin, metadata, layer=layer, rotation=0, source_id=element.id)
            counts["blocks"] += 1
            expected_layers[layer] += 1
        else:
            unresolved = True
            review.append({"element_id": element.id, "reason": "Element lacks usable source geometry or readable text."})
        if unresolved:
            a, b = cad([x0, y0]), cad([x1, y1])
            backend.draw_polyline([a, [b[0], a[1]], b, [a[0], b[1]]], layer="REVIEW-FLAGS", closed=True, source_id=element.id)
            backend.add_text(f"REVIEW {element.id}: {element.status}", origin, 1.5, layer="REVIEW-FLAGS", source_id=element.id)
            expected_layers["REVIEW-FLAGS"] += 2
            if not any(r["element_id"] == element.id for r in review):
                review.append({"element_id": element.id, "reason": f"Source status is {element.status}; requires human review."})
        built_ids.append(element.id)
    result = backend.save(output_path)
    actual_layers = result.get("entities_per_layer", {})
    counts_match = dict(expected_layers) == actual_layers
    if not counts_match:
        review.append({"element_id": None, "reason": "Saved CAD per-layer entity counts differ from emitted graph geometry; inspect the drawing."})
    result.update({"mode": mode, "units": "mm", "pixel_scale_mm": [sx, sy], "graph_element_count": len(graph.elements), "built_element_ids": built_ids, "build_counts": dict(counts), "expected_entities_per_layer": dict(expected_layers), "layer_counts_match": counts_match, "review": review, "edges_without_invented_geometry": len(graph.edges), "fidelity_notice": "Literal source points preserved" if mode == "fidelity" else "Coordinates snapped to 1 mm grid; source positions changed"})
    return result
