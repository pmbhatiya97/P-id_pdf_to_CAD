from pathlib import Path
import threading

import ezdxf
import fitz
import pytest

from lineaforge.core import (
    PT_TO_MM, ConversionCancelled, ConversionError, ConverterConfig,
    ConverterUnavailable, convert_file, dxf_to_pdf, inspect_cad, inspect_pdf,
    parse_pages, pdf_to_dxf,
)


def make_pdf(path: Path, *, text=True, pages=1, rotate=0):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=300)
        shape = page.new_shape()
        shape.draw_line((10, 20), (110, 20))
        shape.draw_bezier((120, 30), (140, 10), (160, 80), (180, 50))
        shape.finish(color=(.2, .4, .7), width=1.25)
        shape.commit()
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(200, 100, 260, 180))
        shape.draw_rect(fitz.Rect(220, 120, 240, 160))
        shape.finish(color=None, fill=(0, 0, 0), even_odd=True)
        shape.commit()
        if text:
            page.insert_text((20, 250), "P-101 Feed Pump", fontsize=12, color=(.3, .1, .2))
        page.set_rotation(rotate)
    doc.save(path)
    doc.close()


def test_pdf_primitives_mm_color_holes_and_semantic_text(tmp_path):
    source, output = tmp_path / "drawing.pdf", tmp_path / "drawing.dxf"
    make_pdf(source)
    info = inspect_pdf(source)
    assert info[0]["kind"] == "vector"
    assert info[0]["images"] == 0
    assert info[0]["text_characters"] > 0
    result = pdf_to_dxf(source, output, scale=2)
    doc = ezdxf.readfile(output)
    assert doc.units == ezdxf.units.MM
    line = list(doc.modelspace().query("LINE"))[0]
    assert line.dxf.start.x == pytest.approx(10 * PT_TO_MM * 2)
    assert line.dxf.start.y == pytest.approx(280 * PT_TO_MM * 2)
    assert line.dxf.true_color == 0x3366B2 or line.dxf.true_color == 0x3366B3
    assert len(doc.modelspace().query("SPLINE")) == 1
    hatch = list(doc.modelspace().query("HATCH"))[0]
    assert len(hatch.paths) == 2
    assert hatch.dxf.hatch_style == 0
    assert len(doc.modelspace().query("TEXT")) == 1
    assert result["counts"]["texts"] == 1
    assert result["pages"] == 1
    assert not doc.audit().has_errors


def test_outlined_source_does_not_invent_text(tmp_path):
    source, output = tmp_path / "outlined.pdf", tmp_path / "outlined.dxf"
    make_pdf(source, text=False)
    assert inspect_pdf(source)[0]["kind"] == "outlined-vector"
    result = pdf_to_dxf(source, output)
    assert len(ezdxf.readfile(output).modelspace().query("TEXT MTEXT")) == 0
    assert any("no semantic text" in w for w in result["warnings"])


def test_page_range_and_rotated_geometry(tmp_path):
    source, output = tmp_path / "pages.pdf", tmp_path / "pages.dxf"
    make_pdf(source, text=False, pages=3, rotate=90)
    assert parse_pages("1,3", 3) == [0, 2]
    result = pdf_to_dxf(source, output, pages="2-3")
    assert result["pages"] == 2
    assert result["page_details"][0]["width_pt"] == 300
    first = list(ezdxf.readfile(output).modelspace().query("LINE"))[0]
    assert first.dxf.start.x == pytest.approx(280 * PT_TO_MM)
    assert first.dxf.start.y == pytest.approx(390 * PT_TO_MM)


def test_dxf_plot_is_vector_and_correct_paper(tmp_path):
    source, dxf, pdf = tmp_path / "source.pdf", tmp_path / "drawing.dxf", tmp_path / "plot.pdf"
    make_pdf(source)
    pdf_to_dxf(source, dxf)
    result = dxf_to_pdf(dxf, pdf, paper="A3", orientation="landscape")
    assert result["pages"] == 1
    with fitz.open(pdf) as doc:
        assert len(doc) == 1
        assert doc[0].rect.width * PT_TO_MM == pytest.approx(420, abs=.02)
        assert doc[0].rect.height * PT_TO_MM == pytest.approx(297, abs=.02)
        assert len(doc[0].get_drawings()) > 0
        assert len(doc[0].get_image_info()) == 0


def test_raster_requires_opt_in_and_generic_trace_works(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=80, height=80)
    image = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 50, 50), False)
    image.clear_with(255)
    for x in range(10, 40):
        for y in range(20, 25):
            image.set_pixel(x, y, (0,))
    page.insert_image(page.rect, pixmap=image)
    source, output = tmp_path / "scan.pdf", tmp_path / "trace.dxf"
    doc.save(source)
    doc.close()
    assert inspect_pdf(source)[0]["kind"] == "raster"
    with pytest.raises(ConversionError, match="raster-only"):
        pdf_to_dxf(source, output)
    assert not output.exists()
    result = pdf_to_dxf(source, output, raster=True)
    assert result["counts"]["raster_contours"] > 0
    assert any("generic contour" in w for w in result["warnings"])


def test_cancel_keeps_existing_destination(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "existing.dxf"
    make_pdf(source)
    output.write_text("existing output")
    event = threading.Event()
    event.set()
    with pytest.raises(ConversionCancelled):
        pdf_to_dxf(source, output, cancel=event)
    assert output.read_text() == "existing output"
    assert not list(tmp_path.glob(".lineaforge-*"))


def test_18k_elements_progress_cancellation_and_valid_output(tmp_path):
    source, output = tmp_path / "large.pdf", tmp_path / "large.dxf"
    doc = fitz.open()
    page = doc.new_page(width=2000, height=2000)
    shape = page.new_shape()
    for i in range(18_000):
        x, y = (i % 180) * 10 + 1, (i // 180) * 10 + 1
        shape.draw_line((x, y), (x + 5, y + 2))
    shape.finish(color=(0, 0, 0), width=.2)
    shape.commit()
    doc.save(source)
    doc.close()
    progress = []
    result = pdf_to_dxf(source, output, progress=lambda p, m: progress.append(p))
    assert result["entities"] == 18_000
    assert len(ezdxf.readfile(output).modelspace()) == 18_000
    assert progress[0] == 0 and progress[-1] == 100
    assert progress == sorted(progress)


def test_dependency_and_invalid_inputs_are_descriptive(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source)
    with pytest.raises(ConverterUnavailable, match="separately installed"):
        convert_file(source, tmp_path / "drawing.dwg", config=ConverterConfig(converter_path=str(tmp_path / "missing.exe")))
    with pytest.raises(ConversionError, match="Scale"):
        pdf_to_dxf(source, tmp_path / "drawing.dxf", scale=-1)
    with pytest.raises(ConversionError, match="page range"):
        pdf_to_dxf(source, tmp_path / "drawing.dxf", pages="9")
    with pytest.raises(ConversionError, match="does not exist"):
        inspect_pdf(tmp_path / "missing.pdf")


def test_encrypted_pdf_requires_password(tmp_path):
    source = tmp_path / "private.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.draw_line((10, 10), (50, 50))
    doc.save(source, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="read")
    doc.close()
    with pytest.raises(ConversionError, match="encrypted"):
        inspect_pdf(source)
    assert inspect_pdf(source, password="read")[0]["paths"] > 0


def test_inspect_cad_entity_bounds(tmp_path):
    source, output = tmp_path / "drawing.pdf", tmp_path / "drawing.dxf"
    make_pdf(source)
    pdf_to_dxf(source, output)
    info = inspect_cad(output)
    assert info["entities"] >= 4
    assert "SPLINE" in info["entity_types"]
    assert info["bounds"] is not None


def test_filled_cubic_outline_and_hole_survive_vector_plot(tmp_path):
    source, dxf, pdf = tmp_path / "rings.pdf", tmp_path / "rings.dxf", tmp_path / "rings-plot.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_circle((100, 100), 70)
    shape.draw_circle((100, 100), 30)
    shape.finish(color=None, fill=(0, 0, 0), even_odd=True)
    shape.commit()
    doc.save(source)
    doc.close()
    pdf_to_dxf(source, dxf)
    cad = ezdxf.readfile(dxf)
    hatch = list(cad.modelspace().query("HATCH"))[0]
    assert len(hatch.paths) == 2
    assert all(len(boundary.edges) == 4 for boundary in hatch.paths)
    dxf_to_pdf(dxf, pdf, paper="A4", orientation="portrait")
    with fitz.open(pdf) as result:
        pix = result[0].get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csGRAY, alpha=False)
        # Centered concentric boundaries should retain their white counter and black ring.
        assert pix.pixel(pix.width // 2, pix.height // 2)[0] > 240
        assert pix.pixel(pix.width // 2 + int(pix.width * .25), pix.height // 2)[0] < 30


def test_cancel_during_save_preparation_preserves_previous_file(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "existing.dxf"
    make_pdf(source)
    output.write_bytes(b"previous result")
    event = threading.Event()
    def progress(value, message):
        if value >= 94:
            event.set()
    with pytest.raises(ConversionCancelled):
        pdf_to_dxf(source, output, progress=progress, cancel=event)
    assert output.read_bytes() == b"previous result"
    assert not list(tmp_path.glob(".lineaforge-*"))


def test_external_converter_rejects_renamed_dxf_and_isolates_inputs(tmp_path, monkeypatch):
    from lineaforge import core
    source, output = tmp_path / "drawing.dxf", tmp_path / "drawing.dwg"
    cad = ezdxf.new("R2018")
    cad.modelspace().add_line((0, 0), (10, 10))
    cad.saveas(source)
    calls = []
    monkeypatch.setattr(core, "_converter_executable", lambda config, suffix: "test-converter")
    class InvalidConverter:
        returncode = 0
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))
            assert kwargs["shell"] is False
            inputs = list(Path(args[1]).iterdir())
            assert len(inputs) == 1 and inputs[0].name == "drawing.dxf"
            Path(args[2], "drawing.dwg").write_bytes(b"0\nSECTION\n2\nHEADER\n")
        def poll(self):
            return 0
    monkeypatch.setattr(core.subprocess, "Popen", InvalidConverter)
    with pytest.raises(ConversionError, match="not a genuine DWG"):
        convert_file(source, output)
    assert not output.exists()
    assert calls[0][0][3:5] == ["ACAD2018", "DWG"]
    assert not Path(calls[0][1]["cwd"]).exists()


def test_explicit_plot_scale_respects_cad_units(tmp_path):
    dxf, pdf = tmp_path / "metres.dxf", tmp_path / "metres.pdf"
    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.M
    doc.modelspace().add_line((0, 0), (1, 0))
    doc.saveas(dxf)
    result = dxf_to_pdf(dxf, pdf, paper="A4", scale="1:10")
    assert result["unit_factor_mm"] == 1000
    with fitz.open(pdf) as plotted:
        widths = [d["rect"].width * PT_TO_MM for d in plotted[0].get_drawings() if d["type"] == "s"]
        assert max(widths) == pytest.approx(100, abs=.1)
