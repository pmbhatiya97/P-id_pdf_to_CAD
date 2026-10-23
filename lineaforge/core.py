"""Local, deterministic PDF/CAD conversion primitives.

PDF coordinates are converted from points to millimetres. Filled outlines stay
editable hatch boundaries; they are never described as recognized text.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
import math
import os
import re
import shutil
import subprocess
import tempfile
import time

import fitz
import ezdxf

PT_TO_MM = 25.4 / 72
Progress = Callable[[int, str], None]


class ConversionError(RuntimeError):
    """An input or conversion could not be completed."""


class ConversionCancelled(ConversionError):
    """The caller cancelled the operation; no partial output is published."""


class ConverterUnavailable(ConversionError):
    """DWG conversion requires a separately installed converter."""


@dataclass
class ConverterConfig:
    converter_path: str | None = None
    converter_kind: str = "oda"
    version: str = "R2018"
    timeout: float = 300


def _check_cancel(cancel: Any = None) -> None:
    if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise ConversionCancelled("Conversion cancelled. No partial output was saved.")


def _progress(callback: Progress | None, value: float, message: str, cancel: Any = None) -> None:
    _check_cancel(cancel)
    if callback:
        callback(max(0, min(100, int(value))), message)


def _source(path: str | Path, suffix: str | None = None) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConversionError(f"Input file does not exist: {p}")
    if suffix and p.suffix.lower() != suffix:
        raise ConversionError(f"Expected a {suffix.upper()} input: {p.name}")
    return p


def _destination(path: str | Path, source: str | Path | None = None) -> Path:
    p = Path(path).expanduser().resolve()
    if source is not None and p == Path(source).expanduser().resolve():
        raise ConversionError("Choose an output file different from the input file.")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _atomic_bytes(destination: Path, data: bytes, cancel: Any = None) -> None:
    _check_cancel(cancel)
    fd, name = tempfile.mkstemp(prefix=".lineaforge-", suffix=destination.suffix, dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        _check_cancel(cancel)
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _save_dxf(doc: Any, destination: Path, cancel: Any = None) -> None:
    fd, name = tempfile.mkstemp(prefix=".lineaforge-", suffix=".dxf", dir=destination.parent)
    os.close(fd)
    try:
        doc.saveas(name)
        _check_cancel(cancel)
        # Confirm this is a readable DXF before publishing it.
        with open(name, "rb") as stream:
            if b"SECTION" not in stream.read(512):
                raise ConversionError("CAD writer did not produce a valid DXF header.")
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _open_pdf(path: str | Path, password: str | None = None) -> fitz.Document:
    p = _source(path, ".pdf")
    try:
        doc = fitz.open(p)
    except Exception as exc:
        raise ConversionError(f"Cannot open PDF {p.name}: {exc}") from exc
    if doc.needs_pass and not doc.authenticate(password or ""):
        doc.close()
        raise ConversionError("This PDF is encrypted. Enter the correct PDF password.")
    if not doc.page_count:
        doc.close()
        raise ConversionError("The PDF contains no pages.")
    return doc


def parse_pages(pages: Any, count: int) -> list[int]:
    """Strings use human page numbers; integer sequences use zero-based indices."""
    if pages is None or (isinstance(pages, str) and pages.strip().lower() == "all"):
        return list(range(count))
    result: list[int] = []
    if isinstance(pages, str):
        try:
            for part in pages.split(","):
                match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
                if not match:
                    raise ValueError
                first = int(match[1])
                last = int(match[2] or first)
                if last < first:
                    raise ValueError
                result.extend(range(first - 1, last))
        except (ValueError, TypeError) as exc:
            raise ConversionError("Use page numbers such as 1,3-5 or 'all'.") from exc
    elif isinstance(pages, int):
        result = [pages]
    else:
        try:
            result = [int(p) for p in pages]
        except (TypeError, ValueError) as exc:
            raise ConversionError("Page selection must be a range or a list of page indices.") from exc
    if not result or any(p < 0 or p >= count for p in result):
        raise ConversionError(f"Page selection is outside this PDF's 1–{count} page range.")
    return result


def _page_info(page: fitz.Page, drawings: list | None = None) -> dict:
    paths = drawings if drawings is not None else page.get_drawings()
    text = page.get_text("text")
    chars = len(text.strip())
    images = len(page.get_image_info())
    if images and (paths or chars):
        kind = "mixed"
    elif images:
        kind = "raster"
    elif paths and not chars:
        kind = "outlined-vector"
    else:
        kind = "vector"
    rect = page.rect
    return {
        "page": page.number + 1, "page_index": page.number,
        "width_pt": rect.width, "height_pt": rect.height,
        "width": rect.width, "height": rect.height,
        "width_mm": rect.width * PT_TO_MM, "height_mm": rect.height * PT_TO_MM,
        "rotation": page.rotation, "kind": kind, "source_kind": kind,
        "paths": len(paths), "path_segments": sum(len(p.get("items", ())) for p in paths),
        "images": images, "text_characters": chars, "semantic_text": bool(chars),
    }


def inspect_pdf(path: str | Path, password: str | None = None) -> list[dict]:
    with _open_pdf(path, password) as doc:
        return [_page_info(page) for page in doc]


def _rgb(value: Any) -> tuple[int, int, int]:
    if value is None:
        return 0, 0, 0
    if isinstance(value, int):
        return ((value >> 16) & 255, (value >> 8) & 255, value & 255)
    values = tuple(value)
    if len(values) == 1:
        values = values * 3
    if len(values) == 4:
        c, m, y, k = values
        values = (1 - min(1, c + k), 1 - min(1, m + k), 1 - min(1, y + k))
    return tuple(max(0, min(255, round(float(v) * 255))) for v in values[:3])


def _truecolor(rgb: tuple[int, int, int]) -> int:
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def _safe_name(value: str) -> str:
    return re.sub(r'[<>/\\":;?*|=,`\x00-\x1f]', "_", value).strip()[:100] or "PDF"


class _PdfWriter:
    def __init__(self, doc: Any, factor: float, cancel: Any = None):
        self.doc, self.msp, self.factor, self.cancel = doc, doc.modelspace(), factor, cancel
        self.counts: Counter = Counter()
        self.warnings: set[str] = set()
        self._linetypes: dict[tuple, str] = {}
        self._layers: dict[tuple, str] = {}
        self._styles: dict[str, str] = {}

    def attrs(self, drawing: dict, page_number: int, fill: bool = False) -> dict:
        rgb = _rgb(drawing.get("fill" if fill else "color"))
        width = float(drawing.get("width") or 0) * self.factor
        # DXF uses a discrete set of lineweights, measured in hundredths of mm.
        weights = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
        lw = min(weights, key=lambda w: abs(w - width * 100))
        ocg = _safe_name(str(drawing.get("layer") or "")) if drawing.get("layer") else ""
        key = page_number, rgb, lw, ocg, fill
        if key not in self._layers:
            tag = "FILL" if fill else "PATH"
            name = f"P{page_number:03}_{tag}_{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}_W{lw:03}"
            if ocg:
                name += "_" + ocg[:50]
            self.doc.layers.new(name, dxfattribs={"color": 7, "true_color": _truecolor(rgb), "lineweight": lw})
            self._layers[key] = name
        result = {"layer": self._layers[key], "true_color": _truecolor(rgb), "lineweight": lw}
        opacity = drawing.get("fill_opacity" if fill else "stroke_opacity", 1)
        if opacity is not None and opacity < 1:
            from ezdxf.colors import float2transparency
            result["transparency"] = float2transparency(1 - float(opacity))
        dash = drawing.get("dashes", "[] 0")
        if not fill and dash and not dash.startswith("[]"):
            match = re.search(r"\[([^]]+)\]", dash)
            if match:
                numbers = [float(n) * self.factor for n in re.findall(r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?", match[1])]
                if numbers and sum(numbers) > 0:
                    if len(numbers) % 2:
                        numbers *= 2
                    dashkey = tuple(round(n, 6) for n in numbers)
                    if dashkey not in self._linetypes:
                        name = f"PDF_DASH_{len(self._linetypes) + 1}"
                        self.doc.linetypes.new(name, dxfattribs={"description": "PDF dash pattern", "pattern": [sum(numbers)] + [n if i % 2 == 0 else -n for i, n in enumerate(numbers)]})
                        self._linetypes[dashkey] = name
                    result["linetype"] = self._linetypes[dashkey]
        return result

    def point(self, value: Any, page: fitz.Page, offset: float) -> tuple[float, float]:
        p = fitz.Point(value) * page.rotation_matrix
        return offset + p.x * self.factor, (page.rect.height - p.y) * self.factor

    def segments(self, drawing: dict, page: fitz.Page, offset: float, on_chunk=None) -> list[list[tuple]]:
        """Convert disconnected PDF subpaths into independent CAD boundaries."""
        paths: list[list[tuple]] = []
        current: list[tuple] = []
        last = None
        for index, item in enumerate(drawing.get("items", ())):
            if index % 512 == 0:
                _check_cancel(self.cancel)
                if on_chunk:
                    on_chunk(.35 * index / max(1, len(drawing.get("items", ()))))
            op = item[0]
            parts = []
            if op == "l":
                parts = [("l", self.point(item[1], page, offset), self.point(item[2], page, offset))]
            elif op == "c":
                parts = [("c", *(self.point(p, page, offset) for p in item[1:5]))]
            elif op == "re":
                r = item[1]
                pts = [r.tl, r.tr, r.br, r.bl]
                if len(item) > 2 and item[2] < 0:
                    pts.reverse()
                pts = [self.point(p, page, offset) for p in pts]
                if current:
                    paths.append(current)
                    current = []
                paths.append([("l", pts[i], pts[(i + 1) % 4]) for i in range(4)])
                last = None
                continue
            elif op == "qu":
                q = item[1]
                pts = [self.point(p, page, offset) for p in (q.ul, q.ur, q.lr, q.ll)]
                if current:
                    paths.append(current)
                    current = []
                paths.append([("l", pts[i], pts[(i + 1) % 4]) for i in range(4)])
                last = None
                continue
            else:
                self.warnings.add(f"Unsupported PDF path operator {op!r} was skipped.")
            for segment in parts:
                start, end = segment[1], segment[-1]
                if last is not None and math.dist(start, last) > 1e-7:
                    if current:
                        paths.append(current)
                    current = []
                current.append(segment)
                last = end
        if current:
            paths.append(current)
        if drawing.get("closePath"):
            for path in paths:
                if path and math.dist(path[0][1], path[-1][-1]) > 1e-7:
                    path.append(("l", path[-1][-1], path[0][1]))
        return paths

    def drawing(self, drawing: dict, page: fitz.Page, offset: float, on_chunk=None) -> None:
        paths = self.segments(drawing, page, offset, on_chunk)
        if not paths:
            return
        kind = drawing.get("type", "s")
        if "f" in kind and drawing.get("fill") is not None:
            hatch = self.msp.add_hatch(color=7, dxfattribs=self.attrs(drawing, page.number + 1, True))
            hatch.dxf.hatch_style = 0  # nested boundary parity retains glyph counters/holes
            try:
                for path_number, path in enumerate(paths):
                    if path_number % 128 == 0:
                        _check_cancel(self.cancel)
                        if on_chunk:
                            on_chunk(.35 + (.3 if "s" in kind else .65) * path_number / max(1, len(paths)))
                    if not path:
                        continue
                    closed = list(path)
                    if math.dist(closed[0][1], closed[-1][-1]) > 1e-7:
                        closed.append(("l", closed[-1][-1], closed[0][1]))
                    if all(s[0] == "l" for s in closed):
                        hatch.paths.add_polyline_path([s[1] for s in closed], is_closed=True, flags=0)
                    else:
                        edgepath = hatch.paths.add_edge_path(flags=0)
                        for s in closed:
                            if s[0] == "l":
                                edgepath.add_line(s[1], s[2])
                            else:
                                edgepath.add_spline(control_points=s[1:], knot_values=[0, 0, 0, 0, 1, 1, 1, 1], degree=3)
                self.counts["hatches"] += 1
            except Exception as exc:
                self.msp.delete_entity(hatch)
                raise ConversionError(f"Cannot preserve a filled PDF path on page {page.number + 1}: {exc}") from exc
        if "s" in kind:
            attrs = self.attrs(drawing, page.number + 1)
            for path_number, path in enumerate(paths):
                if path_number % 128 == 0:
                    _check_cancel(self.cancel)
                    if on_chunk:
                        on_chunk((.65 if "f" in kind else .35) + (.35 if "f" in kind else .65) * path_number / max(1, len(paths)))
                if all(s[0] == "l" for s in path):
                    points = [path[0][1]] + [s[-1] for s in path]
                    if len(points) == 2:
                        self.msp.add_line(*points, dxfattribs=attrs)
                        self.counts["lines"] += 1
                    else:
                        closed = math.dist(points[0], points[-1]) < 1e-7
                        self.msp.add_lwpolyline(points[:-1] if closed else points, close=closed, dxfattribs=attrs)
                        self.counts["polylines"] += 1
                else:
                    for segment in path:
                        if segment[0] == "l":
                            self.msp.add_line(segment[1], segment[2], dxfattribs=attrs)
                            self.counts["lines"] += 1
                        else:
                            self.msp.add_open_spline(control_points=segment[1:], degree=3, knots=[0, 0, 0, 0, 1, 1, 1, 1], dxfattribs=attrs)
                            self.counts["splines"] += 1

    def text(self, page: fitz.Page, offset: float) -> None:
        for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES).get("blocks", []):
            for line in block.get("lines", []):
                direction = fitz.Point(line.get("dir", (1, 0)))
                direction = direction * fitz.Matrix(page.rotation)
                rotation = math.degrees(math.atan2(-direction.y, direction.x))
                for span in line.get("spans", []):
                    _check_cancel(self.cancel)
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    font = span.get("font", "Arial")
                    if font not in self._styles:
                        name = "PDF_FONT_" + str(len(self._styles) + 1)
                        self.doc.styles.new(name, dxfattribs={"font": font + ".ttf"})
                        self._styles[font] = name
                    attrs = self.attrs({"color": span.get("color", 0)}, page.number + 1)
                    attrs.update(insert=self.point(span.get("origin", span["bbox"][:2]), page, offset),
                                 height=float(span.get("size", 10)) * self.factor,
                                 rotation=rotation, style=self._styles[font])
                    self.msp.add_text(text, dxfattribs=attrs)
                    self.counts["texts"] += 1

    def raster(self, page: fitz.Page, offset: float, progress: Progress | None, base: float, span: float) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise ConversionError("Raster tracing requires the optional OpenCV and NumPy packages.") from exc
        _check_cancel(self.cancel)
        zoom = min(3, 6000 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        _, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        attrs = self.attrs({"color": (0, 0, 0), "width": .4}, page.number + 1)
        for i, contour in enumerate(contours):
            if i % 256 == 0:
                _progress(progress, base + span * i / max(1, len(contours)), f"Tracing raster contours on page {page.number + 1}", self.cancel)
            if len(contour) < 2 or cv2.arcLength(contour, True) < 3:
                continue
            simplified = cv2.approxPolyDP(contour, .5, True).reshape(-1, 2)
            points = [(offset + float(x) / pix.width * page.rect.width * self.factor,
                       (page.rect.height - float(y) / pix.height * page.rect.height) * self.factor)
                      for x, y in simplified]
            self.msp.add_lwpolyline(points, close=True, dxfattribs=attrs)
            self.counts["raster_contours"] += 1
        self.warnings.add("Raster tracing is a generic contour approximation; it does not recognize text, symbols, or connectivity.")


def pdf_to_dxf(source: str | Path, destination: str | Path, pages: Any = "all", scale: float = 1,
               password: str | None = None, progress: Progress | None = None, cancel: Any = None,
               raster: bool = False) -> dict:
    started = time.perf_counter()
    source = _source(source, ".pdf")
    destination = _destination(destination, source)
    if destination.suffix.lower() != ".dxf":
        raise ConversionError("PDF geometry output must have a .dxf extension.")
    try:
        factor = float(scale) * PT_TO_MM
    except (TypeError, ValueError) as exc:
        raise ConversionError("Scale must be a positive number.") from exc
    if not math.isfinite(factor) or factor <= 0:
        raise ConversionError("Scale must be a positive finite number.")
    _progress(progress, 0, "Opening PDF", cancel)
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$MEASUREMENT"] = 1
    writer = _PdfWriter(doc, factor, cancel)
    details = []
    offset = 0.0
    with _open_pdf(source, password) as pdf:
        selected = parse_pages(pages, len(pdf))
        for position, index in enumerate(selected):
            _check_cancel(cancel)
            page = pdf[index]
            drawings = page.get_drawings()
            info = _page_info(page, drawings)
            details.append(dict(info, origin_mm=[offset, 0]))
            start, share = position * 90 / len(selected), 90 / len(selected)
            if info["kind"] == "raster" and not raster:
                raise ConversionError(f"Page {index + 1} is raster-only. Enable raster contour tracing, or use a vector PDF.")
            if info["images"] and not raster:
                writer.warnings.add(f"Page {index + 1}: {info['images']} raster image(s) were excluded; enable raster tracing to approximate them.")
            if raster and info["images"]:
                # Trace the rendered page once, avoiding duplicate vector outlines.
                writer.raster(page, offset, progress, start, share)
            else:
                for i, drawing in enumerate(drawings):
                    if i % 128 == 0:
                        _progress(progress, start + share * i / max(1, len(drawings)), f"Preserving paths on page {index + 1} ({i:,}/{len(drawings):,})", cancel)
                    # A PDF may encode tens of thousands of segments in a single
                    # path; report/cancel within that path as well as between paths.
                    chunk_progress = None
                    if len(drawing.get("items", ())) >= 512:
                        def chunk_progress(fraction, item_index=i):
                            _progress(progress, start + share * (item_index + fraction) / max(1, len(drawings)),
                                      f"Preserving path segments on page {index + 1}", cancel)
                    writer.drawing(drawing, page, offset, chunk_progress)
                writer.text(page, offset)
            if info["kind"] == "outlined-vector":
                writer.warnings.add("Outlined lettering is preserved as editable geometry. The source has no semantic text; no text recognition is claimed.")
            # One named paper layout per source page; modelspace sheets are side by side.
            layout = doc.layouts.new(f"Page_{index + 1}_{position + 1}")
            width, height = page.rect.width * factor, page.rect.height * factor
            layout.page_setup(size=(width, height), margins=(0, 0, 0, 0), units="mm")
            layout.add_viewport(center=(width / 2, height / 2), size=(width, height),
                                view_center_point=(offset + width / 2, height / 2), view_height=height)
            offset += width + 20 * float(scale)
            del drawings
    _progress(progress, 94, "Writing editable DXF", cancel)
    if not len(doc.modelspace()):
        raise ConversionError("No supported PDF geometry or text was found on the selected pages.")
    doc.set_modelspace_vport(height=max(d["height_mm"] for d in details) * float(scale), center=(offset / 2, max(d["height_mm"] for d in details) * float(scale) / 2))
    _save_dxf(doc, destination, cancel)
    _progress(progress, 100, "DXF complete", cancel)
    return {"source": str(source), "output": str(destination), "format": "DXF", "pages": len(details),
            "page_details": details, "entities": len(doc.modelspace()), "counts": dict(writer.counts),
            "warnings": sorted(writer.warnings), "units": "mm", "scale": float(scale),
            "duration_s": round(time.perf_counter() - started, 3)}


def _scale_number(scale: Any) -> float:
    try:
        if isinstance(scale, str) and ":" in scale:
            n, d = scale.split(":", 1)
            value = float(n) / float(d)
        else:
            value = float(scale)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ConversionError("Use 'fit', a numeric factor, or a plot scale such as 1:100.") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConversionError("Plot scale must be a positive finite number.")
    return value


def _read_dxf(path: str | Path) -> Any:
    try:
        return ezdxf.readfile(_source(path, ".dxf"))
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Cannot read DXF {Path(path).name}: {exc}") from exc


def dxf_to_pdf(source: str | Path, destination: str | Path, paper: str = "A3", orientation: str = "landscape",
               scale: Any = "fit", area: str = "model", monochrome: bool = True, lineweights: bool = True,
               progress: Progress | None = None, cancel: Any = None) -> dict:
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy, LineweightPolicy
    started = time.perf_counter()
    source, destination = _source(source, ".dxf"), _destination(destination, source)
    if destination.suffix.lower() != ".pdf":
        raise ConversionError("Plot output must have a .pdf extension.")
    papers = {"A0": (841, 1189), "A1": (594, 841), "A2": (420, 594), "A3": (297, 420), "A4": (210, 297), "LETTER": (215.9, 279.4), "LEGAL": (215.9, 355.6)}
    if paper.upper() not in papers:
        raise ConversionError("Unsupported paper size. Choose A0–A4, Letter, or Legal.")
    if orientation.lower() not in {"portrait", "landscape"}:
        raise ConversionError("Orientation must be portrait or landscape.")
    width, height = papers[paper.upper()]
    if orientation.lower() == "landscape":
        width, height = height, width
    _progress(progress, 0, "Reading CAD drawing", cancel)
    doc = _read_dxf(source)
    warnings = ["CAD plotting supports common 2D entities; unsupported proxy/3D objects may be omitted."]
    try:
        unit_factor = ezdxf.units.conversion_factor(doc.units, ezdxf.units.MM)
    except (TypeError, ValueError, IndexError):
        unit_factor = 1
        warnings.append("The CAD drawing has no supported unit declaration; numeric plot scales assume millimetres.")
    key = area.lower().strip()
    if key in {"model", "modelspace", "extents"}:
        layouts = [doc.modelspace()]
    elif key in {"all", "layouts", "all layouts", "paperspace"}:
        layouts = [l for l in doc.layouts if l.name != "Model" and len(l)]
        if not layouts:
            raise ConversionError("The drawing has no nonempty paper layouts.")
    else:
        name = area.split(":", 1)[-1]
        try:
            layouts = [doc.layouts.get(name)]
        except KeyError as exc:
            raise ConversionError(f"Paper layout does not exist: {name}") from exc
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE,
                        color_policy=ColorPolicy.MONOCHROME if monochrome else ColorPolicy.COLOR,
                        lineweight_policy=LineweightPolicy.ABSOLUTE if lineweights else LineweightPolicy.RELATIVE,
                        lineweight_scaling=1 if lineweights else 0,
                        min_lineweight=.05)
    context = RenderContext(doc)
    result = fitz.open()
    entity_count = sum(len(l) for l in layouts)
    done = 0
    try:
        for drawing_layout in layouts:
            _check_cancel(cancel)
            if not len(drawing_layout):
                raise ConversionError(f"The {drawing_layout.name} layout is empty.")
            backend = pymupdf.PyMuPdfBackend()
            frontend = Frontend(context, backend, config=cfg)
            def visible(entity: Any) -> bool:
                nonlocal done
                if done % 128 == 0:
                    _progress(progress, 5 + 80 * done / max(1, entity_count), f"Plotting {drawing_layout.name}: {done:,} entities", cancel)
                done += 1
                return True
            frontend.draw_layout(drawing_layout, filter_func=visible)
            page = layout.Page(width, height, layout.Units.mm, margins=layout.Margins.all(6))
            settings = layout.Settings(fit_page=str(scale).lower() == "fit", scale=1 if str(scale).lower() == "fit" else _scale_number(scale) * unit_factor, crop_at_margins=True)
            rendered = backend.get_pdf_bytes(page, settings=settings)
            if not rendered:
                raise ConversionError(f"No plottable entities found in {drawing_layout.name}.")
            with fitz.open(stream=rendered, filetype="pdf") as part:
                # ezdxf maps both axes to floor(max(page-size-in-points)). Undo
                # that uniform rounding so explicit engineering plot scales are
                # accurate, then retain the exact ISO/ANSI media size.
                exact_width, exact_height = width / PT_TO_MM, height / PT_TO_MM
                correction = max(exact_width, exact_height) / max(part[0].rect.width, part[0].rect.height)
                if abs(correction - 1) > 1e-9:
                    page_obj = part[0]
                    delta_y = exact_height - page_obj.rect.height * correction
                    wrapped = (f"q\n{correction:.12f} 0 0 {correction:.12f} 0 {delta_y:.12f} cm\n".encode()
                               + page_obj.read_contents() + b"\nQ\n")
                    stream_xref = part.get_new_xref()
                    part.update_object(stream_xref, "<<>>")
                    part.update_stream(stream_xref, wrapped)
                    page_obj.set_contents(stream_xref)
                part[0].set_mediabox(fitz.Rect(0, 0, exact_width, exact_height))
                result.insert_pdf(part)
        _progress(progress, 95, "Saving plotted PDF", cancel)
        _atomic_bytes(destination, result.tobytes(garbage=3, deflate=True), cancel)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"CAD plotting failed: {exc}") from exc
    finally:
        result.close()
    _progress(progress, 100, "PDF complete", cancel)
    return {"source": str(source), "output": str(destination), "format": "PDF", "pages": len(layouts),
            "entities": entity_count, "paper": paper.upper(), "orientation": orientation.lower(),
            "scale": scale, "unit_factor_mm": unit_factor, "warnings": warnings,
            "duration_s": round(time.perf_counter() - started, 3)}


def _converter_executable(config: ConverterConfig, output_suffix: str) -> str:
    if config.converter_kind.lower() not in {"oda", "libredwg"}:
        raise ConverterUnavailable("Converter kind must be 'oda' or 'libredwg'.")
    name = "ODAFileConverter" if config.converter_kind.lower() == "oda" else ("dxf2dwg" if output_suffix == ".dwg" else "dwg2dxf")
    candidate = Path(config.converter_path).expanduser() if config.converter_path else None
    if candidate and candidate.is_dir():
        candidate = candidate / (name + (".exe" if os.name == "nt" else ""))
    if candidate and config.converter_kind.lower() == "libredwg" and candidate.stem.lower() in {"dwg2dxf", "dxf2dwg", "dwgread", "dwgwrite"}:
        candidate = candidate.with_name(name + candidate.suffix)
    found = str(candidate.resolve()) if candidate and candidate.is_file() else (shutil.which(str(candidate)) if candidate else shutil.which(name))
    if not found:
        raise ConverterUnavailable("DWG needs a separately installed ODA File Converter or LibreDWG. Configure its executable in Settings. Editable DXF export works without it.")
    return found


def _external_cad(source: Path, destination: Path, config: ConverterConfig, progress: Progress | None, cancel: Any) -> dict:
    exe = _converter_executable(config, destination.suffix.lower())
    kind = config.converter_kind.lower()
    version = config.version.upper().replace("ACAD", "R")
    if kind == "libredwg" and destination.suffix.lower() == ".dwg" and version not in {"R12", "R14", "R2000"}:
        raise ConverterUnavailable("LibreDWG DWG writing is experimental and this adapter supports R12/R14/R2000 only. Choose R2000 or use ODA for R2018 output.")
    _progress(progress, 5, "Preparing isolated DWG conversion", cancel)
    with tempfile.TemporaryDirectory(prefix="lineaforge-cad-") as tmp:
        root = Path(tmp)
        indir, outdir = root / "input", root / "output"
        indir.mkdir()
        outdir.mkdir()
        local = indir / ("drawing" + source.suffix.lower())
        shutil.copy2(source, local)
        expected = outdir / ("drawing" + destination.suffix.lower())
        if kind == "oda":
            args = [exe, str(indir), str(outdir), version.replace("R", "ACAD", 1), destination.suffix[1:].upper(), "0", "1", local.name]
        else:
            args = [exe, "-y", "-o", str(expected)]
            if destination.suffix.lower() == ".dwg":
                args += ["--as", version.lower()]
            args.append(str(local))
        logpath = root / "converter.log"
        started = time.monotonic()
        with logpath.open("wb") as log:
            try:
                process = subprocess.Popen(args, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                           shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                raise ConverterUnavailable(f"Cannot start the configured CAD converter: {exc}") from exc
            try:
                while process.poll() is None:
                    _check_cancel(cancel)
                    elapsed = time.monotonic() - started
                    if elapsed > config.timeout:
                        raise ConversionError(f"CAD converter exceeded the {config.timeout:g}-second timeout.")
                    _progress(progress, min(85, 10 + elapsed / 2), "Running external CAD converter", cancel)
                    time.sleep(.15)
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        produced = [p for p in outdir.rglob("*") if p.is_file() and p.suffix.lower() == destination.suffix.lower()]
        if process.returncode != 0 or not produced:
            details = logpath.read_text(errors="replace")[-2000:].strip()
            raise ConversionError(f"CAD converter failed (exit {process.returncode}); no output was published. {details}")
        produced_file = expected if expected.exists() else produced[0]
        if destination.suffix.lower() == ".dwg":
            with produced_file.open("rb") as stream:
                signature = stream.read(6)
            if not re.fullmatch(b"AC10[0-9]{2}", signature):
                raise ConversionError("External converter output is not a genuine DWG file (missing DWG signature).")
        else:
            _read_dxf(produced_file)
        _atomic_bytes(destination, produced_file.read_bytes(), cancel)
    _progress(progress, 100, "CAD conversion complete", cancel)
    return {"source": str(source), "output": str(destination), "format": destination.suffix[1:].upper(), "converter": kind,
            "version": config.version, "warnings": ["LibreDWG DWG writing is experimental."] if kind == "libredwg" else []}


def convert_file(source: str | Path, destination: str | Path, config: ConverterConfig | None = None, **options: Any) -> dict:
    source, destination = _source(source), _destination(destination, source)
    config = config or ConverterConfig()
    src, dst = source.suffix.lower(), destination.suffix.lower()
    if src not in {".pdf", ".dxf", ".dwg"} or dst not in {".pdf", ".dxf", ".dwg"}:
        raise ConversionError("Supported conversion formats are PDF, DXF, and DWG.")
    progress, cancel = options.get("progress"), options.get("cancel")
    pdf_keys = {"pages", "scale", "password", "progress", "cancel", "raster"}
    plot_keys = {"paper", "orientation", "scale", "area", "monochrome", "lineweights", "progress", "cancel"}
    if src == dst:
        raise ConversionError("Input and output formats are the same. Choose a different format.")
    if src == ".pdf" and dst == ".dxf":
        return pdf_to_dxf(source, destination, **{k: v for k, v in options.items() if k in pdf_keys})
    if src == ".dxf" and dst == ".pdf":
        return dxf_to_pdf(source, destination, **{k: v for k, v in options.items() if k in plot_keys})
    # Fail early if DWG is unavailable, before doing expensive PDF geometry extraction.
    _converter_executable(config, ".dwg" if dst == ".dwg" else ".dxf")
    if {src, dst} == {".dxf", ".dwg"}:
        return _external_cad(source, destination, config, progress, cancel)
    with tempfile.TemporaryDirectory(prefix="lineaforge-route-") as tmp:
        intermediate = Path(tmp) / "drawing.dxf"
        def first_progress(value: int, message: str) -> None:
            _progress(progress, value * .75, message, cancel)
        def last_progress(value: int, message: str) -> None:
            _progress(progress, 75 + value * .25, message, cancel)
        if src == ".pdf":
            route_options = {k: v for k, v in options.items() if k in pdf_keys}
            route_options["progress"] = first_progress
            result = pdf_to_dxf(source, intermediate, **route_options)
            external = _external_cad(intermediate, destination, config, last_progress, cancel)
            result.update(output=str(destination), format="DWG", converter=external["converter"])
            result["warnings"].extend(external["warnings"])
        else:
            _external_cad(source, intermediate, config, first_progress, cancel)
            route_options = {k: v for k, v in options.items() if k in plot_keys}
            route_options["progress"] = last_progress
            result = dxf_to_pdf(intermediate, destination, **route_options)
        result["source"] = str(source)
        return result


def inspect_cad(path: str | Path, config: ConverterConfig | None = None) -> dict:
    path = _source(path)
    if path.suffix.lower() == ".dwg":
        with tempfile.TemporaryDirectory(prefix="lineaforge-inspect-") as tmp:
            intermediate = Path(tmp) / "drawing.dxf"
            _external_cad(path, intermediate, config or ConverterConfig(), None, None)
            info = inspect_cad(intermediate)
            info["source"] = str(path)
            return info
    doc = _read_dxf(path)
    from ezdxf import bbox
    extents = bbox.extents(doc.modelspace(), fast=True)
    return {"source": str(path), "entities": len(doc.modelspace()),
            "entity_types": dict(Counter(e.dxftype() for e in doc.modelspace())),
            "layers": [l.dxf.name for l in doc.layers], "layouts": [l.name for l in doc.layouts],
            "units": doc.units, "bounds": [list(extents.extmin), list(extents.extmax)] if extents.has_data else None}
