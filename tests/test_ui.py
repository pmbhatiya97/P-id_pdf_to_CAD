"""Offscreen integration: real Qt signals, conversion jobs, review and PDF tools."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path
import fitz
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from lineaforge.desktop import MainWindow
from lineaforge.models import Element, PidGraph, ReviewItem


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    view = MainWindow()
    view.destination.setText(str(tmp_path / "output"))
    yield view
    deadline = time.monotonic()+20
    while view.task_refs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    view.close()


def pdf(path: Path, pages=1):
    with fitz.open() as doc:
        for i in range(pages):
            page = doc.new_page()
            page.draw_rect(fitz.Rect(30, 40, 200, 200))
            page.insert_text((40, 65), f"UI integration page {i+1}")
        doc.save(path)
    return path


def wait_for(app, condition, seconds=20):
    deadline = time.monotonic()+seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    assert condition(), "Qt worker did not complete in time"


def test_native_workspace_and_directions(window):
    assert "PDF & DWG Bidirectional Converter" in window.windowTitle()
    assert len(window.nav_actions) == 5
    window.set_direction(False)
    assert window.format.currentText() == "PDF"
    assert window.scale.text() == "fit"
    assert window.paper.isEnabled()
    window.set_direction(True)
    assert window.format.currentText() == "DXF"
    assert not window.paper.isEnabled()
    assert not window.cloud_consent.isChecked()


def test_queue_worker_keeps_converting_after_bad_file(window, app, tmp_path):
    sources = [pdf(tmp_path / "first.pdf"), tmp_path / "corrupt.pdf", pdf(tmp_path / "last.pdf")]
    sources[1].write_bytes(b"not a PDF")
    window.add_files([str(p) for p in sources])
    window.start_conversion()
    wait_for(app, lambda: not window.running)
    assert [j["status"] for j in window.jobs] == ["Done", "Failed", "Done"]
    assert window.jobs[0]["result"]["entities"] > 0
    assert Path(window.jobs[2]["result"]["output"]).exists()
    assert "2 done" in window.summary.text()
    assert window.pool.maxThreadCount() == 2


def test_provider_configuration_error_stays_recoverable(window, tmp_path):
    window.ai_source.setText(str(pdf(tmp_path / "pid.pdf")))
    window.model.setText("configured-test-model")
    window.endpoint.setText("https://external.example/v1")
    window.provider_mode.setCurrentText("local")
    window.run_ai()
    assert "loopback" in window.ai_log.toPlainText()
    assert not window.ai_running
    assert window.ai_run.isEnabled()
    window.provider_mode.setCurrentText("cloud")
    window.run_ai()
    assert "Cloud job blocked" in window.ai_log.toPlainText()
    assert not window.task_refs


def test_pdf_tool_ui_calls_real_signatures(window, app, tmp_path, monkeypatch):
    source = pdf(tmp_path / "tools.pdf", 3)
    split_dir = tmp_path / "parts"
    split_dir.mkdir()
    window.split_source.setText(str(source))
    window.split_range.setText("2-3")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(split_dir))
    window.split_pdf()
    wait_for(app, lambda: not window.task_refs)
    with fitz.open(next(split_dir.glob("*.pdf"))) as doc:
        assert len(doc) == 2
        assert "page 2" in doc[0].get_text()
    target = tmp_path / "compressed.pdf"
    window.compress_source.setText(str(source))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(target), "PDF"))
    window.compress_pdf()
    wait_for(app, lambda: not window.task_refs)
    assert target.is_file()
    assert "saved_percent" in window.pdf_log.toPlainText()


def test_review_records_explicit_decisions(window):
    window.ai_graph = PidGraph(page_id="test", width_px=300, height_px=200, width_mm=300, height_mm=200,
        elements=[Element(id="one", kind="valve", tag="V-1", bbox=[10, 10, 30, 30], confidence=.7, tile_id="t", source_agent="fixture")],
        review=[ReviewItem(id="flag", element_id="one", reason="Confirm connection")])
    window.populate_review()
    assert window.review_list.count() == 2
    window.review_list.setCurrentRow(0)
    window.review_decision("ACCEPTED")
    assert window.ai_graph.review[0].status == "ACCEPTED"
    window.select_element("one")
    window.element_tag.setText("V-2")
    window.review_decision("ACCEPTED", edit=True)
    assert window.ai_graph.elements[0].tag == "V-2"
    assert window.ai_graph.elements[0].attributes["reviewed_by"] == "human"
    assert window.ai_graph.elements[0].status == "ACCEPTED"
