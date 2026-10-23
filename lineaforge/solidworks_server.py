"""MCP 2D drawing bridge to a licensed Windows SOLIDWORKS installation.

SOLIDWORKS drawings use sheet sketch geometry. P&ID symbol definitions are
expanded into editable sketch segments; this is not SOLIDWORKS Electrical or
an assembly/3D piping model. All COM objects remain on one dedicated STA thread.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re

from .cad import ATTRIBUTE_TAGS, CadError, CadUnavailable, _points, _prepare_windows_runtime


class SolidWorksComAdapter:
    def __init__(self, output_root, template=None):
        self.output_root = Path(output_root).resolve()
        self.template = str(template or os.environ.get("LINEAFORGE_SW_DRAWING_TEMPLATE", ""))
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solidworks-sta")
        self.app = self.doc = self.client = self.pythoncom = None
        self.blocks = {}
        self.provenance = []

    def output_path(self, value, suffixes=(".dxf", ".dwg")):
        path = Path(value).resolve()
        if not path.is_relative_to(self.output_root) or path == self.output_root:
            raise CadError("Path is outside the configured CAD output root")
        if path.suffix.lower() not in suffixes:
            raise CadError("Unsupported SOLIDWORKS output extension")
        return path

    def call(self, operation, **arguments):
        arguments.pop("adapter", None)
        return self.worker.submit(self._execute, operation, arguments).result()

    def _connect(self):
        if self.app is not None:
            return
        if os.name != "nt":
            raise CadUnavailable("SOLIDWORKS COM requires a licensed Windows installation")
        _prepare_windows_runtime()
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise CadUnavailable("SOLIDWORKS COM requires pywin32 in the bridge Python environment") from exc
        self.pythoncom, self.client = pythoncom, win32com.client
        pythoncom.CoInitialize()
        try:
            # Use dynamic dispatch: no generated type-library files need to be
            # written to the application installation or user profile.
            self.app = win32com.client.Dispatch("SldWorks.Application")
        except Exception as exc:
            raise CadUnavailable("SldWorks.Application is unavailable. Install and activate SOLIDWORKS desktop on the CAD host.") from exc

    def _long_ref(self):
        return self.client.VARIANT(self.pythoncom.VT_BYREF | self.pythoncom.VT_I4, 0)

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not value or len(value) > 255 or re.search(r'[<>/\\":;?*|=\x00-\x1f]', value):
            raise CadError("Invalid SOLIDWORKS layer/symbol name")
        return value

    def _layer(self, name):
        manager = self.doc.GetLayerManager()
        if name == "0":
            # The default layer in SOLIDWORKS is '-None-'. Explicitly drawing
            # on the current P&ID layer would otherwise misclassify entities.
            manager.SetCurrentLayer("")
        else:
            name = self._name(name)
            if manager.GetLayer(name) is None:
                raise CadError("Create the drawing layer before adding geometry")
            if not manager.SetCurrentLayer(name) and manager.GetCurrentLayer() != name:
                raise CadError("SOLIDWORKS could not activate the requested layer")

    @staticmethod
    def _positive(value, name):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise CadError(f"{name} must be positive and finite")
        return value

    def _ensure_sketch(self):
        manager = self.doc.SketchManager
        if manager.ActiveSketch is None:
            manager.InsertSketch(True)
        if manager.ActiveSketch is None:
            raise CadError("SOLIDWORKS could not activate a 2D drawing-sheet sketch")
        # Avoid snap/inferencing adjustments to extracted source coordinates.
        manager.AddToDB = True

    def _polyline(self, points, closed=False):
        pts = _points(points)
        if len(pts) < 2:
            raise CadError("A polyline requires at least two points")
        self._ensure_sketch()
        pairs = list(zip(pts, pts[1:]))
        if closed and pts[0] != pts[-1]:
            pairs.append((pts[-1], pts[0]))
        handles = []
        for start, end in pairs:
            segment = self.doc.SketchManager.CreateLine(start[0] / 1000, start[1] / 1000, 0,
                                                      end[0] / 1000, end[1] / 1000, 0)
            if segment is None:
                raise CadError("SOLIDWORKS failed to create a sheet sketch line")
            handles.append(str(segment.GetName()))
        return handles

    def _circle(self, center, radius):
        x, y = _points([center])[0]
        radius = self._positive(radius, "Radius")
        self._ensure_sketch()
        segment = self.doc.SketchManager.CreateCircleByRadius(x / 1000, y / 1000, 0, radius / 1000)
        if segment is None:
            raise CadError("SOLIDWORKS failed to create a circle")
        return str(segment.GetName())

    def _arc(self, center, radius, start_angle, end_angle):
        x, y = _points([center])[0]
        radius = self._positive(radius, "Radius")
        start, end = math.radians(float(start_angle)), math.radians(float(end_angle))
        if not all(math.isfinite(v) for v in (start, end)):
            raise CadError("Arc angles must be finite")
        self._ensure_sketch()
        segment = self.doc.SketchManager.CreateArc(x / 1000, y / 1000, 0,
            (x + radius * math.cos(start)) / 1000, (y + radius * math.sin(start)) / 1000, 0,
            (x + radius * math.cos(end)) / 1000, (y + radius * math.sin(end)) / 1000, 0, 1)
        if segment is None:
            raise CadError("SOLIDWORKS failed to create an arc")
        return str(segment.GetName())

    def _text(self, text, position, height, rotation=0):
        x, y = _points([position])[0]
        height = self._positive(height, "Text height")
        angle = math.radians(float(rotation))
        if not math.isfinite(angle):
            raise CadError("Text angle must be finite")
        note = self.doc.CreateText2(str(text), x / 1000, y / 1000, 0, height / 1000, angle)
        if note is None:
            raise CadError("SOLIDWORKS failed to create drawing text")
        return "note"

    def _remember(self, operation, a, handles):
        self.provenance.append({"operation": operation, "source_id": a.get("source_id", ""),
            "layer": a.get("layer", "0"), "handles": handles,
            "attributes": a.get("attributes", {})})
        return {"handles": handles, "source_id": a.get("source_id", "")}

    def _save_as(self, path):
        errors, warnings = self._long_ref(), self._long_ref()
        manager = self.doc.SketchManager
        if manager.ActiveSketch is not None:
            manager.AddToDB = False
            manager.InsertSketch(True)
        self.doc.ClearSelection2(True)
        # SaveAs remains supported by desktop SOLIDWORKS, with the same native
        # translator as SaveAs3. Keeping this older interface supports older CAD
        # installations without discarding the returned native error flags.
        nothing = self.client.VARIANT(self.pythoncom.VT_DISPATCH, None)
        ok = self.doc.Extension.SaveAs(str(path), 0, 1, nothing, errors, warnings)
        if not ok or int(errors.value) or not path.is_file() or not path.stat().st_size:
            raise CadError(f"SOLIDWORKS save failed (error={errors.value}, warning={warnings.value})")
        return int(warnings.value)

    def _enumerate(self):
        types, layers = Counter(), Counter()
        sheets = list(self.doc.GetSheetNames() or [])
        if not sheets:
            raise CadError("Reimported SOLIDWORKS output has no drawing sheets")
        for sheet in sheets:
            if not self.doc.ActivateSheet(sheet):
                raise CadError("Could not activate reimported drawing sheet")
            view = self.doc.GetFirstView()
            while view is not None:
                sketch = view.GetSketch()
                if sketch is not None:
                    for segment in sketch.GetSketchSegments() or []:
                        types[f"SketchSegment:{segment.GetType()}"] += 1
                        layers[str(segment.Layer)] += 1
                for note in view.GetNotes() or []:
                    types["Note"] += 1
                    layers[str(note.GetAnnotation().Layer)] += 1
                view = view.GetNextView()
        return {"entity_count": sum(types.values()), "entity_types": dict(types),
                "entities_per_layer": dict(layers), "sheet_count": len(sheets)}

    def _execute(self, name, a):
        self._connect()
        if name == "new_drawing":
            template = Path(self.template) if self.template else None
            if template is None or template.suffix.lower() != ".drwdot" or not template.is_file():
                raise CadUnavailable("Set LINEAFORGE_SW_DRAWING_TEMPLATE or --template to an existing SOLIDWORKS .drwdot drawing template on the CAD host.")
            self.doc = self.app.NewDocument(str(template.resolve()), 0, 0.0, 0.0)
            if self.doc is None:
                raise CadUnavailable("SOLIDWORKS could not create a drawing from the configured template")
            if self.doc.GetType() != 3:
                raise CadError("Configured SOLIDWORKS template did not create a drawing document")
            self.doc.ActivateView("")
            self.doc.ClearSelection2(True)
            self.blocks, self.provenance = {}, []
            return {"document": str(self.doc.GetTitle()), "backend": "solidworks_com",
                    "block_mode": "expanded_sketch_geometry", "units": "mm"}
        if self.doc is None:
            raise CadError("Call new_drawing before drawing tools")
        if name == "set_units":
            if a["units"] != "mm":
                raise CadError("SOLIDWORKS bridge supports millimetres only")
            # All lengths entering this protocol are mm; SOLIDWORKS API geometry
            # is always metres, independent of the template's display units.
            return {"units": "mm", "api_units": "metres", "template_display_units": "unchanged"}
        if name == "create_layer":
            layer_name = self._name(a["name"])
            style = {"CONTINUOUS": 0, "LF-DASHED": 1, "LF-DASHDOTDOT": 2}.get(a["linetype"])
            if style is None:
                raise CadError("Unsupported SOLIDWORKS line style")
            colors = {1: 0x0000FF, 2: 0x00FFFF, 3: 0x00FF00, 4: 0xFFFF00,
                      5: 0xFF0000, 6: 0xFF00FF, 7: 0x000000, 8: 0x808080}
            manager = self.doc.GetLayerManager()
            existing = manager.GetLayer(layer_name)
            if existing is None and manager.AddLayer(layer_name, "LineaForge P&ID", colors.get(int(a["color"]), 0), style, 1) != 1:
                raise CadError("SOLIDWORKS could not create the drawing layer")
            return {"layer": layer_name, "linetype": a["linetype"], "lineweight_mapping": "SOLIDWORKS normal"}
        if name == "define_block":
            block_name = self._name(a["name"])
            for primitive in a["geometry"]:
                if primitive.get("type", "polyline") not in ("polyline", "circle", "arc"):
                    raise CadError("Unsupported symbol primitive")
            self.blocks[block_name] = a["geometry"]
            return {"definition": block_name, "block_mode": "expanded_sketch_geometry", "attribute_storage": "provenance sidecar"}
        if name == "insert_block":
            if a["name"] not in self.blocks:
                raise CadError("Define the symbol before inserting it")
            self._layer(a["layer"])
            scale = self._positive(a["scale"], "Symbol scale")
            angle = math.radians(float(a["rotation"]))
            if not math.isfinite(angle):
                raise CadError("Symbol rotation must be finite")
            origin = _points([a["position"]])[0]
            def transformed(point):
                x, y = _points([point])[0]
                return [origin[0] + scale * (x * math.cos(angle) - y * math.sin(angle)),
                        origin[1] + scale * (x * math.sin(angle) + y * math.cos(angle))]
            handles = []
            for shape in self.blocks[a["name"]]:
                kind = shape.get("type", "polyline")
                if kind == "polyline":
                    handles.extend(self._polyline([transformed(p) for p in shape["points"]], shape.get("closed", False)))
                elif kind == "circle":
                    handles.append(self._circle(transformed(shape["center"]), float(shape["radius"]) * scale))
                else:
                    handles.append(self._arc(transformed(shape["center"]), float(shape["radius"]) * scale,
                        float(shape["start_angle"]) + float(a["rotation"]), float(shape["end_angle"]) + float(a["rotation"])))
            tag = a["attributes"].get("TAG")
            if tag:
                handles.append(self._text(tag, [origin[0], origin[1] - 2.5 * scale], 2 * scale, a["rotation"]))
            return {**self._remember(name, a, handles), "block_mode": "expanded_sketch_geometry"}
        if name in ("draw_polyline", "draw_circle", "draw_arc", "add_text"):
            self._layer(a["layer"])
            if name == "draw_polyline":
                handles = self._polyline(a["points"], a["closed"])
            elif name == "draw_circle":
                handles = [self._circle(a["center"], a["radius"])]
            elif name == "draw_arc":
                handles = [self._arc(a["center"], a["radius"], a["start_angle"], a["end_angle"])]
            else:
                handles = [self._text(a["text"], a["position"], a["height"], a["rotation"])]
            return self._remember(name, a, handles)
        if name == "save_drawing":
            path = self.output_path(a["output_path"])
            if path.exists():
                raise CadError("SOLIDWORKS output already exists; use a new job output path")
            path.parent.mkdir(parents=True, exist_ok=True)
            native = path.with_suffix(".slddrw")
            if native.exists():
                raise CadError("SOLIDWORKS native output already exists")
            native_warning = self._save_as(native)
            warning = self._save_as(path)
            from .mcp_connections import _validate_file
            _validate_file(path, path.suffix)
            sidecar = path.with_suffix(".provenance.json")
            sidecar.write_text(json.dumps({"backend": "solidworks_com", "block_mode": "expanded_sketch_geometry",
                "native_document": str(native), "entities": self.provenance}, indent=2), encoding="utf-8")
            return {"saved": True, "output_path": str(path), "native_document": str(native),
                    "provenance_path": str(sidecar), "warnings": [native_warning, warning],
                    "block_mode": "expanded_sketch_geometry"}
        if name == "reopen_and_enumerate":
            path = self.output_path(a["output_path"])
            from .mcp_connections import _validate_file
            _validate_file(path, path.suffix)
            # Reimport the actual exported artifact, rather than counting the
            # still-open original or a queued list of drawing commands.
            self.app.CloseDoc(self.doc.GetTitle())
            self.doc = None
            import_data = self.app.GetImportFileData(str(path))
            if import_data is None:
                raise CadError("SOLIDWORKS has no DXF/DWG import translator available")
            import_data._oleobj_.Invoke(import_data._oleobj_.GetIDsOfNames("ImportMethod"),
                0, self.pythoncom.DISPATCH_PROPERTYPUT, 0, "", 1)
            errors = self._long_ref()
            self.doc = self.app.LoadFile4(str(path), "r", import_data, errors)
            if self.doc is None or int(errors.value):
                raise CadError(f"SOLIDWORKS could not reimport the exported CAD file (error={errors.value})")
            if self.doc.GetType() != 3:
                raise CadError("SOLIDWORKS reimport did not produce a drawing document")
            return {**self._enumerate(), "reopened": True, "verification": "native exported-file reimport",
                    "block_mode": "expanded_sketch_geometry"}
        raise CadError("Unsupported SOLIDWORKS operation")


def create_server(output_root, template=None):
    _prepare_windows_runtime()
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("LineaForge SOLIDWORKS", instructions="2D drawing sheet sketches only, requiring a licensed local SOLIDWORKS desktop installation and drawing template. Symbols expand into editable sketch geometry. No macros or arbitrary commands.")
    adapter = SolidWorksComAdapter(output_root, template)

    @server.tool()
    def new_drawing() -> dict:
        """Create a separate drawing using the configured .drwdot template."""
        return adapter.call("new_drawing")

    @server.tool()
    def set_units(units: str = "mm") -> dict:
        """Use protocol millimetres, converted to native SOLIDWORKS metres."""
        return adapter.call("set_units", units=units)

    @server.tool()
    def create_layer(name: str, color: int = 7, linetype: str = "CONTINUOUS", lineweight: int = 25) -> dict:
        """Create a drawing layer with a mapped native line style."""
        return adapter.call("create_layer", **locals())

    @server.tool()
    def define_block(name: str, geometry: list[dict], attribute_tags: list[str] = list(ATTRIBUTE_TAGS)) -> dict:
        """Store literal symbol geometry; instances expand into editable sheet sketch segments."""
        return adapter.call("define_block", **locals())

    @server.tool()
    def insert_block(name: str, position: list[float], attributes: dict[str, str], layer: str = "0", scale: float = 1, rotation: float = 0, source_id: str = "") -> dict:
        """Draw a transformed symbol and tag; retain all attributes in a provenance sidecar."""
        return adapter.call("insert_block", **locals())

    @server.tool()
    def draw_polyline(points: list[list[float]], layer: str = "0", closed: bool = False, source_id: str = "") -> dict:
        """Draw a series of connected literal sheet sketch lines."""
        return adapter.call("draw_polyline", **locals())

    @server.tool()
    def draw_circle(center: list[float], radius: float, layer: str = "0", source_id: str = "") -> dict:
        """Draw a native sheet sketch circle."""
        return adapter.call("draw_circle", **locals())

    @server.tool()
    def draw_arc(center: list[float], radius: float, start_angle: float, end_angle: float, layer: str = "0", source_id: str = "") -> dict:
        """Draw a native sheet sketch arc, using counterclockwise degree angles."""
        return adapter.call("draw_arc", **locals())

    @server.tool()
    def add_text(text: str, position: list[float], height: float, layer: str = "0", rotation: float = 0, source_id: str = "") -> dict:
        """Create a native drawing note from literal source text."""
        return adapter.call("add_text", **locals())

    @server.tool()
    def save_drawing(output_path: str) -> dict:
        """Save native SLDDrw plus a DWG/DXF export inside the bridge output root."""
        return adapter.call("save_drawing", **locals())

    @server.tool()
    def reopen_and_enumerate(output_path: str) -> dict:
        """Reimport the actual exported DWG/DXF and enumerate its sketch entities and notes."""
        return adapter.call("reopen_and_enumerate", **locals())

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--write-schema", type=Path)
    args = parser.parse_args()
    server = create_server(args.output_root, args.template)
    if args.write_schema:
        args.write_schema.parent.mkdir(parents=True, exist_ok=True)
        args.write_schema.write_text(json.dumps([t.model_dump(mode="json") for t in asyncio.run(server.list_tools())], indent=2), encoding="utf-8")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
