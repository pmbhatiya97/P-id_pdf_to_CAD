from pathlib import Path
import threading
import fitz
import pytest

from lineaforge.core import ConversionCancelled, ConversionError
from lineaforge.pdf_tools import compress, merge, organize, split


def fixture_pdf(path: Path, count=4):
    doc = fitz.open()
    for i in range(count):
        page = doc.new_page(width=300 + i, height=200)
        page.insert_text((20, 30), f"Page {i + 1}")
        page.draw_line((10, 50), (200, 50))
    doc.save(path)
    doc.close()


def test_merge_split_organize_preserve_vectors_and_order(tmp_path):
    one, two = tmp_path / "one.pdf", tmp_path / "two.pdf"
    fixture_pdf(one, 3)
    fixture_pdf(two, 2)
    merged = tmp_path / "merged.pdf"
    assert merge([one, two], merged)["pages"] == 5
    result = split(merged, tmp_path / "parts", every_n=2)
    assert result["parts"] == 3
    assert [len(fitz.open(p)) for p in result["outputs"]] == [2, 2, 1]
    ordered = tmp_path / "ordered.pdf"
    organize(one, ordered, order="3,1", rotations={2: 90})
    with fitz.open(ordered) as doc:
        assert "Page 3" in doc[0].get_text()
        assert "Page 1" in doc[1].get_text()
        assert doc[0].rotation == 90
        assert len(doc[0].get_drawings()) == 1


def test_split_ranges_and_compression_without_rasterization(tmp_path):
    source = tmp_path / "source.pdf"
    fixture_pdf(source)
    result = split(source, tmp_path / "parts", ranges=["1-2", "4"])
    assert result["pages"] == 3
    output = tmp_path / "compressed.pdf"
    assert compress(source, output, quality="small")["pages"] == 4
    with fitz.open(output) as doc:
        assert all(page.get_drawings() and not page.get_image_info() for page in doc)


def test_pdf_tools_reject_invalid_and_cancel_cleanly(tmp_path):
    source = tmp_path / "source.pdf"
    fixture_pdf(source)
    with pytest.raises(ConversionError, match="at least one"):
        merge([], tmp_path / "merged.pdf")
    with pytest.raises(ConversionError, match="positive"):
        split(source, tmp_path / "parts", every_n=0)
    with pytest.raises(ConversionError, match="multiple of 90"):
        organize(source, tmp_path / "ordered.pdf", rotations={0: 10})
    cancel = threading.Event()
    cancel.set()
    output = tmp_path / "compressed.pdf"
    with pytest.raises(ConversionCancelled):
        compress(source, output, cancel=cancel)
    assert not output.exists()
    assert not list(tmp_path.glob(".lineaforge-*"))
