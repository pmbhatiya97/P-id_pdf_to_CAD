"""Local stdio MCP helper for a licensed Windows AutoCAD installation.

All COM calls run on one dedicated STA thread. No command/LISP execution tool
is exposed. Save, reopen and plot paths are confined to --output-root.
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
import sys

from .cad import ATTRIBUTE_TAGS, CadError, CadUnavailable, _points, _prepare_windows_runtime


class AutoCadComAdapter:
    def __init__(self, output_root):
        self.output_root = Path(output_root).resolve()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autocad-sta")
        self.app = None
        self.doc = None
        self.client = None
        self.pythoncom = None

    def output_path(self, value, suffixes=(".dwg", ".dxf")):
        path = Path(value).resolve()
        if not path.is_relative_to(self.output_root):
            raise CadError("Path is outside the configured CAD output root")
        if path.suffix.lower() not in suffixes:
            raise CadError("Unsupported CAD output extension")
        return path

    def call(self, operation, **arguments):
        # locals() in tool wrappers also includes the captured adapter object.
        arguments.pop("adapter", None)
        return self.worker.submit(self._execute, operation, arguments).result()

    def _connect(self):
        if self.app is not None:
            return
        if os.name != "nt":
            raise CadUnavailable("AutoCAD COM requires a licensed Windows installation")
        # pip --target does not execute pywin32's .pth files automatically.
        for root in [Path(__file__).resolve().parent.parent / ".runtime"]:
            for child in ["win32", "win32/lib", "pywin32_system32"]:
                path = root / child
                if path.is_dir() and str(path) not in sys.path:
                    sys.path.insert(0, str(path))
            dll = root / "pywin32_system32"
            if dll.is_dir() and hasattr(os, "add_dll_directory"):
                self._dll_cookie = os.add_dll_directory(str(dll))
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise CadUnavailable("AutoCAD COM requires the optional pywin32 package") from exc
        self.pythoncom, self.client = pythoncom, win32com.client
        pythoncom.CoInitialize()
        try:
            self.app = win32com.client.GetActiveObject("AutoCAD.Application")
        except Exception:
            try:
                self.app = win32com.client.Dispatch("AutoCAD.Application")
            except Exception as exc:
                raise CadUnavailable("AutoCAD.Application is unavailable. Install and activate a licensed local AutoCAD version.") from exc

    def _array(self, values, kind=None):
        return self.client.VARIANT(self.pythoncom.VT_ARRAY | (kind or self.pythoncom.VT_R8), values)

    def _point(self, point):
        xy = _points([point])[0]
        return self._array([*xy, 0.0])

    def _name(self, name):
        if not name or len(name) > 255 or any(c in name for c in '<>/\\":;?*|=') or any(ord(c) < 32 for c in name):
            raise CadError("Invalid CAD table name")
        return name

    def _source(self, entity, source_id):
        if source_id:
            try:
                self.doc.RegisteredApplications.Item("LINEAFORGE")
            except Exception:
                self.doc.RegisteredApplications.Add("LINEAFORGE")
            entity.SetXData(self._array([1001, 1000], self.pythoncom.VT_I2),
                            self._array(["LINEAFORGE", str(source_id)[:255]], self.pythoncom.VT_VARIANT))
        return str(entity.Handle)

    def _polyline(self, space, points, closed=False):
        pts = _points(points)
        if len(pts) < 2:
            raise CadError("A polyline requires at least two points")
        entity = space.AddLightWeightPolyline(self._array([v for xy in pts for v in xy]))
        entity.Closed = bool(closed)
        return entity

    def _execute(self, name, a):
        self._connect()
        if name == "new_drawing":
            self.doc = self.app.Documents.Add()
            return {"document": str(self.doc.Name), "backend": "autocad_com"}
        if self.doc is None:
            raise CadError("Call new_drawing before drawing tools")
        space = self.doc.ModelSpace
        if name == "set_units":
            if a["units"] != "mm":
                raise CadError("This helper supports millimetres only")
            self.doc.SetVariable("INSUNITS", 4)
            self.doc.SetVariable("MEASUREMENT", 1)
            return {"units": "mm"}
        if name == "create_layer":
            layer_name = self._name(a["name"])
            try:
                layer = self.doc.Layers.Item(layer_name)
            except Exception:
                layer = self.doc.Layers.Add(layer_name)
            layer.Color, layer.Lineweight = int(a["color"]), int(a["lineweight"])
            linetype = {"LF-DASHED": "DASHED", "LF-DASHDOTDOT": "PHANTOM"}.get(a["linetype"], a["linetype"])
            if linetype != "CONTINUOUS":
                try:
                    self.doc.Linetypes.Item(linetype)
                except Exception:
                    self.doc.Linetypes.Load(linetype, "acad.lin")
            layer.Linetype = linetype
            return {"layer": layer_name, "linetype": linetype}
        if name == "define_block":
            block_name = self._name(a["name"])
            try:
                block = self.doc.Blocks.Item(block_name)
                return {"block": block_name, "existing": True}
            except Exception:
                block = self.doc.Blocks.Add(self._point([0, 0]), block_name)
            for shape in a["geometry"]:
                kind = shape.get("type", "polyline")
                if kind == "polyline":
                    self._polyline(block, shape["points"], shape.get("closed", False))
                elif kind == "circle":
                    block.AddCircle(self._point(shape["center"]), float(shape["radius"]))
                elif kind == "arc":
                    block.AddArc(self._point(shape["center"]), float(shape["radius"]), math.radians(shape["start_angle"]), math.radians(shape["end_angle"]))
                else:
                    raise CadError("Unsupported literal block primitive")
            for i, tag in enumerate(a["attribute_tags"]):
                block.AddAttribute(2.0, 0 if tag == "TAG" else 1, tag, self._point([0, -2.5 * (i + 1)]), self._name(tag), "")
            return {"block": block_name, "attribute_tags": a["attribute_tags"]}
        if name == "insert_block":
            ref = space.InsertBlock(self._point(a["position"]), self._name(a["name"]), float(a["scale"]), float(a["scale"]), 1.0, math.radians(a["rotation"]))
            ref.Layer = self._name(a["layer"])
            if ref.HasAttributes:
                for attr in ref.GetAttributes():
                    if str(attr.TagString) in a["attributes"]:
                        attr.TextString = str(a["attributes"][str(attr.TagString)])
            return {"handle": self._source(ref, a["source_id"])}
        if name in ("draw_line", "draw_polyline"):
            entity = self._polyline(space, a["points"], a.get("closed", False))
            entity.Layer = self._name(a["layer"])
            return {"handle": self._source(entity, a["source_id"])}
        if name in ("draw_circle", "draw_arc"):
            radius = float(a["radius"])
            if not math.isfinite(radius) or radius <= 0:
                raise CadError("radius must be positive and finite")
            if name == "draw_circle":
                entity = space.AddCircle(self._point(a["center"]), radius)
            else:
                entity = space.AddArc(self._point(a["center"]), radius, math.radians(a["start_angle"]), math.radians(a["end_angle"]))
            entity.Layer = self._name(a["layer"])
            return {"handle": self._source(entity, a["source_id"])}
        if name in ("add_text", "add_mtext"):
            if name == "add_text":
                entity = space.AddText(a["text"], self._point(a["position"]), float(a["height"]))
            else:
                entity = space.AddMText(self._point(a["position"]), float(a["width"]), a["text"])
                entity.Height = float(a["height"])
            entity.Layer, entity.Rotation = self._name(a["layer"]), math.radians(a["rotation"])
            return {"handle": self._source(entity, a["source_id"])}
        if name == "set_attribute":
            entity = self.doc.HandleToObject(a["handle"])
            for attr in entity.GetAttributes():
                if str(attr.TagString) == a["tag"]:
                    attr.TextString = a["value"]
                    return {"updated": True}
            raise CadError("Attribute tag does not exist on that block")
        if name == "create_layout":
            layout_name = self._name(a["name"])
            layout = self.doc.Layouts.Add(layout_name)
            layout.PaperUnits = 1
            self.doc.ActiveLayout = layout
            return {"layout": layout_name}
        if name == "create_viewport":
            viewport = self.doc.PaperSpace.AddPViewport(self._point(a["center"]), float(a["width"]), float(a["height"]))
            viewport.Display(True)
            return {"handle": str(viewport.Handle)}
        if name == "save_drawing":
            path = self.output_path(a["output_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            self.doc.SaveAs(str(path), 61 if path.suffix.lower() == ".dxf" else 60)
            return {"output_path": str(path), "saved": path.is_file()}
        if name == "reopen_and_enumerate":
            path = self.output_path(a["output_path"])
            if not path.is_file():
                raise CadError("Saved CAD output does not exist")
            self.doc.Close(False)
            self.doc = self.app.Documents.Open(str(path))
            entities = list(self.doc.ModelSpace)
            return {"reopened": True, "entity_count": len(entities), "entities_per_layer": dict(Counter(str(e.Layer) for e in entities)), "entity_types": dict(Counter(str(e.ObjectName) for e in entities))}
        if name == "plot_pdf":
            path = self.output_path(a["output_path"], (".pdf",))
            path.parent.mkdir(parents=True, exist_ok=True)
            self.doc.SetVariable("BACKGROUNDPLOT", 0)
            layout = self.doc.ActiveLayout
            layout.ConfigName = "DWG To PDF.pc3"
            layout.RefreshPlotDeviceInfo()
            if a["canonical_media_name"]:
                layout.CanonicalMediaName = a["canonical_media_name"]
            ok = self.doc.Plot.PlotToFile(str(path), "DWG To PDF.pc3")
            return {"output_path": str(path), "plotted": bool(ok and path.is_file())}
        raise CadError("Unsupported CAD operation")


def create_server(output_root):
    _prepare_windows_runtime()
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("LineaForge AutoCAD", instructions="Local 2D CAD only. Create a new drawing first. Output paths are confined to the configured output root. No arbitrary CAD command execution.")
    adapter = AutoCadComAdapter(output_root)

    @server.tool()
    def new_drawing() -> dict:
        """Create a new AutoCAD document; leaves existing user documents intact."""
        return adapter.call("new_drawing")

    @server.tool()
    def set_units(units: str = "mm") -> dict:
        """Set millimetre drawing units."""
        return adapter.call("set_units", units=units)

    @server.tool()
    def create_layer(name: str, color: int = 7, linetype: str = "CONTINUOUS", lineweight: int = 25) -> dict:
        """Create/update a layer. LF-DASHED and LF-DASHDOTDOT map to installed AutoCAD linetypes."""
        return adapter.call("create_layer", **locals())

    @server.tool()
    def define_block(name: str, geometry: list[dict], attribute_tags: list[str] = list(ATTRIBUTE_TAGS)) -> dict:
        """Define a block from literal local polyline/circle/arc geometry and named attributes."""
        return adapter.call("define_block", **locals())

    @server.tool()
    def insert_block(name: str, position: list[float], attributes: dict[str, str], layer: str = "0", scale: float = 1, rotation: float = 0, source_id: str = "") -> dict:
        """Insert a named block; rotation is degrees counterclockwise in drawing coordinates."""
        return adapter.call("insert_block", **locals())

    @server.tool()
    def draw_polyline(points: list[list[float]], layer: str = "0", closed: bool = False, source_id: str = "") -> dict:
        """Draw connected literal 2D points in millimetres."""
        return adapter.call("draw_polyline", **locals())

    @server.tool()
    def draw_line(start: list[float], end: list[float], layer: str = "0", source_id: str = "") -> dict:
        """Draw a two-point line without arbitrary CAD commands."""
        return adapter.call("draw_line", points=[start, end], layer=layer, source_id=source_id)

    @server.tool()
    def draw_circle(center: list[float], radius: float, layer: str = "0", source_id: str = "") -> dict:
        """Draw a circle with positive radius."""
        return adapter.call("draw_circle", **locals())

    @server.tool()
    def draw_arc(center: list[float], radius: float, start_angle: float, end_angle: float, layer: str = "0", source_id: str = "") -> dict:
        """Draw an arc; angles are counterclockwise degrees."""
        return adapter.call("draw_arc", **locals())

    @server.tool()
    def add_text(text: str, position: list[float], height: float, layer: str = "0", rotation: float = 0, source_id: str = "") -> dict:
        """Add literal source text; text is never interpreted as a CAD command."""
        return adapter.call("add_text", **locals())

    @server.tool()
    def add_mtext(text: str, position: list[float], height: float, width: float, layer: str = "0", rotation: float = 0, source_id: str = "") -> dict:
        """Add multiline text."""
        return adapter.call("add_mtext", **locals())

    @server.tool()
    def set_attribute(handle: str, tag: str, value: str) -> dict:
        """Update an existing block attribute by handle and exact tag."""
        return adapter.call("set_attribute", **locals())

    @server.tool()
    def create_layout(name: str) -> dict:
        """Create a named paper-space layout."""
        return adapter.call("create_layout", **locals())

    @server.tool()
    def create_viewport(center: list[float], width: float, height: float) -> dict:
        """Create a paper-space viewport in the active layout."""
        return adapter.call("create_viewport", **locals())

    @server.tool()
    def save_drawing(output_path: str) -> dict:
        """Save a DWG/DXF inside the configured output root (AutoCAD 2013 format)."""
        return adapter.call("save_drawing", **locals())

    @server.tool()
    def reopen_and_enumerate(output_path: str) -> dict:
        """Reopen saved output and count model-space entities by layer/type."""
        return adapter.call("reopen_and_enumerate", **locals())

    @server.tool()
    def plot_pdf(output_path: str, canonical_media_name: str = "") -> dict:
        """Plot the active layout through the installed DWG To PDF.pc3 driver."""
        return adapter.call("plot_pdf", **locals())

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--write-schema", type=Path)
    args = parser.parse_args()
    server = create_server(args.output_root)
    if args.write_schema:
        tools = asyncio.run(server.list_tools())
        args.write_schema.parent.mkdir(parents=True, exist_ok=True)
        args.write_schema.write_text(json.dumps([t.model_dump(mode="json") for t in tools], indent=2), encoding="utf-8")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
