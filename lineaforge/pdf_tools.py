"""PDF page tools that preserve source vectors and publish outputs atomically."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import tempfile
import fitz

from .core import ConversionError, _check_cancel, _destination, _open_pdf, _progress, parse_pages


def _save(doc: fitz.Document, output: Path, cancel: Any = None, *, clean: bool = False) -> None:
    _check_cancel(cancel)
    fd, name = tempfile.mkstemp(prefix=".lineaforge-pdf-", suffix=".pdf", dir=output.parent)
    os.close(fd)
    try:
        doc.save(name, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=clean,
                 encryption=fitz.PDF_ENCRYPT_NONE)
        _check_cancel(cancel)
        with fitz.open(name) as check:
            if not check.page_count:
                raise ConversionError("PDF writer produced an empty document.")
        os.replace(name, output)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def merge(inputs: list[str | Path], output: str | Path, *, password: str | None = None,
          progress=None, cancel=None) -> dict:
    if not inputs:
        raise ConversionError("Choose at least one PDF to merge.")
    output = _destination(output)
    if output in [Path(p).resolve() for p in inputs]:
        raise ConversionError("The merged output must be different from every input file.")
    result = fitz.open()
    try:
        for i, source in enumerate(inputs):
            _progress(progress, i * 90 / len(inputs), f"Merging {Path(source).name}", cancel)
            with _open_pdf(source, password) as doc:
                for j in range(len(doc)):
                    _check_cancel(cancel)
                    result.insert_pdf(doc, from_page=j, to_page=j)
        _save(result, output, cancel)
        count = len(result)
    finally:
        result.close()
    _progress(progress, 100, "Merge complete", cancel)
    return {"output": str(output), "outputs": [str(output)], "pages": count, "inputs": len(inputs)}


def split(source: str | Path, output_dir: str | Path, *, ranges=None, every_n: int = 1,
          password: str | None = None, progress=None, cancel=None) -> dict:
    """Split by a list of human ranges, e.g. ['1-3', '4-6'], or page intervals."""
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    with _open_pdf(source, password) as doc:
        if ranges:
            selections = [parse_pages(r, len(doc)) for r in (ranges.split(";") if isinstance(ranges, str) else ranges)]
        else:
            try:
                every_n = int(every_n)
            except (TypeError, ValueError) as exc:
                raise ConversionError("Pages per split must be a positive integer.") from exc
            if every_n <= 0:
                raise ConversionError("Pages per split must be a positive integer.")
            selections = [list(range(i, min(i + every_n, len(doc)))) for i in range(0, len(doc), every_n)]
        destinations = [output_dir / f"{Path(source).stem}_part_{i + 1:03}.pdf" for i in range(len(selections))]
        if Path(source).resolve() in destinations:
            raise ConversionError("Split output would overwrite the input file.")
        # Stage all outputs first, so cancellation does not leave a partial split set.
        with tempfile.TemporaryDirectory(prefix=".lineaforge-split-", dir=output_dir) as tmp:
            staged = []
            for i, selection in enumerate(selections):
                _progress(progress, i * 90 / len(selections), f"Splitting part {i + 1} of {len(selections)}", cancel)
                part = fitz.open()
                try:
                    for index in selection:
                        _check_cancel(cancel)
                        part.insert_pdf(doc, from_page=index, to_page=index)
                    temporary = Path(tmp) / destinations[i].name
                    _save(part, temporary, cancel)
                    staged.append(temporary)
                finally:
                    part.close()
            _check_cancel(cancel)
            for staged_path, destination in zip(staged, destinations):
                os.replace(staged_path, destination)
                outputs.append(str(destination))
    _progress(progress, 100, "Split complete", cancel)
    return {"output": str(output_dir), "outputs": outputs, "parts": len(outputs), "pages": sum(map(len, selections))}


def compress(source: str | Path, output: str | Path, *, quality: str = "balanced", password: str | None = None,
             progress=None, cancel=None) -> dict:
    if str(quality).lower() not in {"lossless", "balanced", "small", "high", "medium", "low"}:
        raise ConversionError("Compression quality must be lossless, balanced, or small.")
    output = _destination(output, source)
    original_size = Path(source).stat().st_size if Path(source).is_file() else 0
    _progress(progress, 5, "Opening PDF for lossless compression", cancel)
    with _open_pdf(source, password) as doc:
        for i, page in enumerate(doc):
            _progress(progress, 10 + 65 * i / len(doc), f"Cleaning page {i + 1} content streams", cancel)
            if str(quality).lower() in {"small", "low"}:
                page.clean_contents()
        count = len(doc)
        _progress(progress, 85, "Recompressing PDF without rasterizing vectors", cancel)
        _save(doc, output, cancel)
    size = output.stat().st_size
    _progress(progress, 100, "Compression complete", cancel)
    return {"output": str(output), "outputs": [str(output)], "pages": count,
            "before_bytes": original_size, "after_bytes": size,
            "saved_percent": round((1 - size / original_size) * 100, 1) if original_size else 0,
            "warnings": ["Lossless stream compression preserves vectors and image resolution. Already optimized PDFs may not get smaller."]}


def organize(source: str | Path, output: str | Path, *, order=None, rotations=None, password: str | None = None,
             progress=None, cancel=None) -> dict:
    output = _destination(output, source)
    result = fitz.open()
    rotations = rotations or {}
    try:
        with _open_pdf(source, password) as doc:
            selected = parse_pages(order, len(doc))
            for position, index in enumerate(selected):
                _progress(progress, position * 90 / len(selected), f"Arranging page {position + 1}", cancel)
                result.insert_pdf(doc, from_page=index, to_page=index)
                amount = rotations.get(index, rotations.get(str(index), 0)) if isinstance(rotations, dict) else rotations[position]
                if int(amount) % 90:
                    raise ConversionError("Page rotation must be a multiple of 90 degrees.")
                result[-1].set_rotation((result[-1].rotation + int(amount)) % 360)
        _save(result, output, cancel)
        count = len(result)
    finally:
        result.close()
    _progress(progress, 100, "Page organization complete", cancel)
    return {"output": str(output), "outputs": [str(output)], "pages": count, "order": selected}
