"""LineaForge's native desktop workspace. No drawing leaves this app by default."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import fitz
from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal, QSettings, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter,
    QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QToolBar,
    QVBoxLayout, QWidget,
)

STYLE = """
QMainWindow,QWidget { background:#10292d; color:#f3f0e7; font:10pt 'Segoe UI'; }
QToolBar { background:#0c2225; border:0; spacing:8px; padding:12px; }
QToolBar QToolButton { padding:12px 19px; border-radius:6px; color:#b9cac8; }
QToolBar QToolButton:checked { background:#254b4c; color:#7ae3c2; }
QLabel#eyebrow { color:#77d8bd; font-size:9pt; font-weight:700; }
QLabel#title { font-size:24pt; font-weight:650; }
QLabel#subtitle { color:#b4c6c5; }
QLabel#notice { color:#efd09f; background:#283b37; padding:11px; border-radius:6px; }
QGroupBox { border:1px solid #355155; border-radius:9px; margin-top:15px; padding:15px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:14px; padding:0 6px; }
QPushButton { background:#28474a; color:#f3f0e7; border:1px solid #456063; padding:9px 14px; border-radius:5px; }
QPushButton:hover { background:#355c5e; border-color:#83cbbb; }
QPushButton:focus { border:2px solid #a3ecd6; }
QPushButton:disabled { color:#6d8a8b; background:#203a3d; }
QPushButton#primary { background:#8ce3c4; color:#102c2c; font-weight:700; border:0; padding:13px 18px; }
QPushButton#direction:checked { background:#edddbe; color:#153436; font-weight:700; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit { background:#19373b; border:1px solid #416063; padding:7px; border-radius:4px; selection-background-color:#427d71; }
QComboBox QAbstractItemView { background:#19373b; selection-background-color:#326d60; }
QTableWidget,QListWidget { background:#142f33; alternate-background-color:#19373b; gridline-color:#2b494e; border:1px solid #355155; border-radius:5px; }
QHeaderView::section { background:#254448; color:#b9d3ce; border:0; padding:9px; }
QTableWidget::item { padding:8px; }
QTableWidget::item:selected,QListWidget::item:selected { background:#315e5b; }
QProgressBar { border:0; border-radius:4px; background:#29474b; text-align:center; min-height:9px; }
QProgressBar::chunk { background:#83d9bc; border-radius:4px; }
QTabWidget::pane { border:1px solid #365457; border-radius:6px; }
QTabBar::tab { background:#1d3a3e; padding:10px 17px; }
QTabBar::tab:selected { background:#39625f; color:#a3f1d6; }
QGraphicsView { background:#091d20; border:1px solid #365457; border-radius:4px; }
QScrollArea { border:0; }
QSlider::groove:horizontal { background:#345659; height:5px; }
QSlider::handle:horizontal { background:#92e4c6; width:16px; margin:-5px 0; border-radius:7px; }
QCheckBox { spacing:8px; }
QStatusBar { background:#0b2124; color:#adc5c0; }
QToolTip { background:#f4eedf; color:#143538; border:1px solid #698e83; }
"""


def brand_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor("#8ce3c4"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#123436"), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
    painter.drawLine(19, 15, 19, 47)
    painter.drawLine(19, 47, 46, 47)
    painter.drawLine(31, 16, 47, 16)
    painter.drawLine(31, 16, 31, 34)
    painter.drawLine(31, 34, 43, 34)
    painter.end()
    return QIcon(pix)


class TaskSignals(QObject):
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, fn: Callable):
        super().__init__()
        self.fn = fn
        self.signals = TaskSignals()

    def run(self):
        try:
            self.signals.result.emit(self.fn(self.signals.progress.emit))
        except Exception as exc:
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.signals.finished.emit()


class Preview(QGraphicsView):
    """Bounded PDF raster preview with pan, wheel zoom, and source coordinates."""
    element_selected = Signal(str)
    region_drawn = Signal(list)

    def __init__(self):
        self.canvas = QGraphicsScene()
        super().__init__(self.canvas)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumHeight(210)
        self.source_size = (1, 1)
        self.overlay_items: list[QGraphicsRectItem] = []
        self.draw_mode = False
        self.draw_start = None
        self.draw_rect = None
        self.canvas.selectionChanged.connect(self._selected)
        self.message("Select a drawing to preview")

    def message(self, text: str):
        self.canvas.clear()
        self.overlay_items = []
        item = self.canvas.addText(text)
        item.setDefaultTextColor(QColor("#b5ceca"))
        self.resetTransform()
        self.canvas.setSceneRect(item.boundingRect())

    def load(self, path: str | Path, page: int = 0, password: str | None = None):
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            with fitz.open(path) as doc:
                if doc.needs_pass and not doc.authenticate(password or ""):
                    self.message("Encrypted PDF · supply a password in conversion options")
                    return
                if page >= len(doc):
                    page = 0
                rect = doc[page].rect
                zoom = min(2.0, 1800 / max(rect.width, rect.height))
                pix = doc[page].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        else:
            image = QImage(str(path))
            if image.isNull():
                self.message("CAD preview is generated after conversion to PDF")
                return
        self.canvas.clear()
        self.overlay_items = []
        self.canvas.addPixmap(QPixmap.fromImage(image))
        self.source_size = (image.width(), image.height())
        self.canvas.setSceneRect(0, 0, image.width(), image.height())
        self.fit()

    def fit(self):
        self.fitInView(self.canvas.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        current = self.transform().m11()
        if .02 < current * factor < 30:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self.draw_start = self.mapToScene(event.position().toPoint())
            self.draw_rect = self.canvas.addRect(self.draw_start.x(), self.draw_start.y(), 1, 1, QPen(QColor("#e8b77b"), 2))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.draw_mode and self.draw_start and self.draw_rect:
            point = self.mapToScene(event.position().toPoint())
            from PySide6.QtCore import QRectF
            self.draw_rect.setRect(QRectF(self.draw_start, point).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.draw_mode and self.draw_start and self.draw_rect:
            rect = self.draw_rect.rect()
            self.region_drawn.emit([rect.left(), rect.top(), rect.right(), rect.bottom()])
            self.draw_mode = False
            self.draw_start = None
            self.canvas.removeItem(self.draw_rect)
            self.draw_rect = None
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def compare(self, before: QImage, after: QImage, fraction: float):
        if before.isNull() or after.isNull():
            return
        image = QPixmap.fromImage(before)
        painter = QPainter(image)
        split = int(image.width() * fraction)
        painter.setClipRect(split, 0, image.width()-split, image.height())
        painter.drawImage(0, 0, after.scaled(before.size()))
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#df9c4c"), 4))
        painter.drawLine(split, 0, split, image.height())
        painter.end()
        self.canvas.clear()
        self.overlay_items = []
        self.canvas.addPixmap(image)
        self.canvas.setSceneRect(0, 0, image.width(), image.height())
        self.fit()

    def overlays(self, elements, width: float, height: float, kinds: set[str]):
        for item in self.overlay_items:
            self.canvas.removeItem(item)
        self.overlay_items.clear()
        sx, sy = self.source_size[0] / max(width, 1), self.source_size[1] / max(height, 1)
        for element in elements:
            if element.kind not in kinds or element.status == "REJECTED":
                continue
            x0, y0, x1, y1 = element.bbox
            item = self.canvas.addRect(x0 * sx, y0 * sy, max(2, (x1-x0) * sx), max(2, (y1-y0) * sy))
            color = "#edb573" if element.confidence < .85 else "#1bb893"
            pen = QPen(QColor(color), 2)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
            item.setData(0, element.id)
            item.setToolTip(f"{element.kind} · {element.tag or element.text or element.id}\nConfidence {element.confidence:.0%}")
            self.overlay_items.append(item)

    def _selected(self):
        for item in self.canvas.selectedItems():
            if item.data(0):
                self.element_selected.emit(item.data(0))


class DropTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__(0, 6)
        self.setHorizontalHeaderLabels(["DRAWING", "TYPE", "SIZE", "PAGES", "STATUS", "DETAILS"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setAcceptDrops(True)
        self.verticalHeader().hide()
        self.setMinimumHeight(165)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        self.files_dropped.emit([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()


def button(text: str, fn, primary=False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(fn)
    return widget


def combo(items: list[str]) -> QComboBox:
    widget = QComboBox()
    widget.addItems(items)
    return widget


def heading(title: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    eyebrow = QLabel("LINEAFORGE  /  DRAWING WORKSPACE")
    eyebrow.setObjectName("eyebrow")
    label = QLabel(title)
    label.setObjectName("title")
    sub = QLabel(subtitle)
    sub.setObjectName("subtitle")
    sub.setWordWrap(True)
    layout.addWidget(eyebrow)
    layout.addWidget(label)
    layout.addWidget(sub)
    return layout


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LineaForge — PDF & DWG Bidirectional Converter")
        self.setWindowIcon(brand_icon())
        self.resize(1450, 950)
        self.setMinimumSize(1024, 740)
        self.preferences = QSettings("LineaForge", "DrawingWorkspace")
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.jobs: list[dict] = []
        self.running = False
        self.pending: list[int] = []
        self.active = 0
        self.task_refs: list[Task] = []
        self.ai_graph = None
        self.ai_result = None
        self.ai_cancel = threading.Event()
        self.ai_running = False
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self._toolbar()
        self._convert_page()
        self._pdf_page()
        self._ai_page()
        self._settings_page()
        self.setStyleSheet(STYLE)
        self.statusBar().showMessage("Local workspace · 2D drawings · source files remain on your machine")

    def _toolbar(self):
        bar = QToolBar("Main navigation")
        bar.setMovable(False)
        self.addToolBar(bar)
        brand = QLabel("  ◧  LineaForge   ")
        brand.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        bar.addWidget(brand)
        self.nav_actions = []
        for label, index in [("⇄  Convert", 0), ("▤  Batch", 0), ("▧  PDF Tools", 1), ("◎  P&&ID AI", 2), ("⚙  Settings", 3)]:
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, i=index, a=action: self._navigate(i, a))
            bar.addAction(action)
            self.nav_actions.append(action)
        self.nav_actions[0].setChecked(True)

    def _navigate(self, index, action=None):
        self.stack.setCurrentIndex(index)
        for item in self.nav_actions:
            item.setChecked(item is action)

    def _page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(15)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        return layout

    def _convert_page(self):
        layout = self._page()
        layout.addLayout(heading("Make every drawing workable.", "Convert vector drawings, trace scans, or hand a P&ID to the review workflow."))
        switch = QHBoxLayout()
        self.pdf_direction = QPushButton("PDF → DWG / DXF")
        self.cad_direction = QPushButton("DWG / DXF → PDF")
        for widget in [self.pdf_direction, self.cad_direction]:
            widget.setCheckable(True)
            widget.setObjectName("direction")
            widget.setMinimumWidth(190)
            switch.addWidget(widget)
        self.pdf_direction.setChecked(True)
        self.pdf_direction.clicked.connect(lambda: self.set_direction(True))
        self.cad_direction.clicked.connect(lambda: self.set_direction(False))
        switch.addStretch()
        switch.addWidget(QLabel("2D ONLY   ·   BATCH READY"))
        layout.addLayout(switch)
        addbox = QGroupBox("01  Add your drawings")
        addlayout = QVBoxLayout(addbox)
        row = QHBoxLayout()
        row.addWidget(QLabel("Drop drawings into the queue, or browse your computer."))
        row.addStretch()
        row.addWidget(button("Add files…", self.choose_files))
        row.addWidget(button("Remove selected", self.remove_selected))
        addlayout.addLayout(row)
        self.table = DropTable()
        self.table.files_dropped.connect(self.add_files)
        self.table.itemSelectionChanged.connect(self.show_selected)
        addlayout.addWidget(self.table)
        layout.addWidget(addbox)
        middle = QSplitter(Qt.Orientation.Horizontal)
        options = QGroupBox("02  Choose your output")
        form = QFormLayout(options)
        self.drawing_mode = combo(["2D Vector", "2D Raster/Scanned", "P&ID (AI)"])
        self.drawing_mode.currentIndexChanged.connect(self._mode_hint)
        self.format = combo(["DXF", "DWG"])
        self.pages = QLineEdit("all")
        self.pages.setPlaceholderText("all or 1-3,5")
        self.version = combo(["R2018", "R2013", "R2010", "R2007", "R2004", "R2000"])
        self.scale = QLineEdit("1")
        self.scale.setToolTip("PDF→CAD: numeric multiplier. CAD→PDF: fit or 1:100.")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("If required; kept only in memory")
        self.paper = combo(["A3", "A1", "A0", "A2", "A4", "LETTER", "LEGAL"])
        self.orientation = combo(["landscape", "portrait"])
        self.area = QLineEdit("model")
        self.area.setToolTip("model or a paper-space layout name supported by the converter")
        self.monochrome = QCheckBox("Monochrome output")
        self.monochrome.setChecked(True)
        self.lineweights = QCheckBox("Honor lineweights")
        self.lineweights.setChecked(True)
        self.destination = QLineEdit(str(Path.home() / "Documents" / "LineaForge output"))
        dest = QHBoxLayout()
        dest.addWidget(self.destination)
        dest.addWidget(button("Browse…", self.choose_destination))
        for label, widget in [("Drawing mode", self.drawing_mode), ("Output format", self.format), ("Pages", self.pages), ("DWG version", self.version), ("Scale", self.scale), ("PDF password", self.password), ("Paper", self.paper), ("Orientation", self.orientation), ("Drawing area", self.area)]:
            form.addRow(label, widget)
        form.addRow(self.monochrome)
        form.addRow(self.lineweights)
        form.addRow("Destination", dest)
        self.conversion_hint = QLabel("Vector PDFs preserve editable geometry. Scanned results depend on image quality. DWG requires an external converter; DXF works offline.")
        self.conversion_hint.setWordWrap(True)
        self.conversion_hint.setObjectName("notice")
        form.addRow(self.conversion_hint)
        middle.addWidget(options)
        previews = QWidget()
        pv = QVBoxLayout(previews)
        pv.setContentsMargins(8, 0, 0, 0)
        previewbar = QHBoxLayout()
        previewbar.addWidget(QLabel("SOURCE  /  OUTPUT"))
        previewbar.addStretch()
        previewbar.addWidget(button("Fit views", self.fit_previews))
        pv.addLayout(previewbar)
        self.source_preview = Preview()
        self.output_preview = Preview()
        pv.addWidget(self.source_preview)
        pv.addWidget(self.output_preview)
        self.output_stats = QLabel("Pan by dragging · zoom with the mouse wheel")
        self.output_stats.setObjectName("subtitle")
        pv.addWidget(self.output_stats)
        middle.addWidget(previews)
        middle.setStretchFactor(0, 1)
        middle.setStretchFactor(1, 1)
        layout.addWidget(middle)
        delivery = QHBoxLayout()
        self.convert_button = button("Convert PDF ↔ DWG drawing ⇄", self.start_conversion, True)
        self.cancel_button = button("Cancel", self.cancel_conversion)
        self.cancel_button.setEnabled(False)
        self.retry_button = button("Retry failed", self.retry_failed)
        delivery.addWidget(self.convert_button)
        delivery.addWidget(self.cancel_button)
        delivery.addWidget(self.retry_button)
        delivery.addStretch()
        delivery.addWidget(button("Open file", self.open_output))
        delivery.addWidget(button("Open folder", self.open_folder))
        delivery.addWidget(button("Share…", self.share_output))
        layout.addLayout(delivery)
        self.overall_progress = QProgressBar()
        layout.addWidget(self.overall_progress)
        self.summary = QLabel("03  Convert and deliver · queue is empty")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.set_direction(True)

    def set_direction(self, pdf_to_cad: bool):
        self.pdf_direction.setChecked(pdf_to_cad)
        self.cad_direction.setChecked(not pdf_to_cad)
        self.format.clear()
        self.format.addItems(["DXF", "DWG"] if pdf_to_cad else ["PDF"])
        self.scale.setText("1" if pdf_to_cad else "fit")
        for widget in [self.drawing_mode, self.pages, self.version, self.password]:
            widget.setEnabled(pdf_to_cad)
        for widget in [self.paper, self.orientation, self.area, self.monochrome, self.lineweights]:
            widget.setEnabled(not pdf_to_cad)

    def _mode_hint(self):
        if self.drawing_mode.currentText() == "P&ID (AI)":
            self.conversion_hint.setText("P&ID mode uses the dedicated AI workspace, evidence overlays, and mandatory human review before CAD export.")
        else:
            self.conversion_hint.setText("Vector PDFs preserve editable geometry. Scanned results depend on image quality. DWG requires an external converter; DXF works offline.")

    def choose_files(self):
        kind = "PDF drawings (*.pdf)" if self.pdf_direction.isChecked() else "CAD drawings (*.dwg *.dxf)"
        paths, _ = QFileDialog.getOpenFileNames(self, "Add drawings", "", kind)
        self.add_files(paths)

    def add_files(self, paths):
        accepted = {".pdf"} if self.pdf_direction.isChecked() else {".dwg", ".dxf"}
        for raw in paths:
            path = Path(raw)
            if not path.is_file() or path.suffix.lower() not in accepted:
                self.statusBar().showMessage(f"Skipped unsupported input: {path.name}", 7000)
                continue
            if any(j["source"] == str(path) for j in self.jobs):
                continue
            job = {"source": str(path), "status": "Queued", "result": None, "cancel": threading.Event(), "progress": 0}
            row = len(self.jobs)
            self.jobs.append(job)
            self.table.insertRow(row)
            values = [path.name, path.suffix[1:].upper(), f"{path.stat().st_size / 1048576:.2f} MB", "—", "Queued", "Ready"]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
            if path.suffix.lower() == ".pdf":
                try:
                    with fitz.open(path) as doc:
                        self.table.item(row, 3).setText(str(len(doc)))
                        if doc.needs_pass:
                            self.table.item(row, 5).setText("Encrypted · password required")
                except Exception as exc:
                    self.table.item(row, 5).setText(f"Will validate on conversion: {exc}")
        self.summary.setText(f"03  Convert and deliver · {len(self.jobs)} drawing(s) in queue")

    def remove_selected(self):
        if self.running:
            self.statusBar().showMessage("Wait for the running batch before removing rows", 5000)
            return
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.jobs.pop(row)
            self.table.removeRow(row)

    def choose_destination(self):
        path = QFileDialog.getExistingDirectory(self, "Output folder", self.destination.text())
        if path:
            self.destination.setText(path)

    def show_selected(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.jobs):
            return
        job = self.jobs[row]
        try:
            result = job.get("result")
            source_view = result.get("source_preview", job["source"]) if result else job["source"]
            self.source_preview.load(source_view, password=self.password.text())
            if result:
                preview = result.get("preview") or result.get("preview_pdf") or result["output"]
                self.output_preview.load(preview)
                self.output_stats.setText(f"{result.get('entities', '—')} entities · {result.get('layers', '—')} layers · {Path(result['output']).name}")
        except Exception as exc:
            self.statusBar().showMessage(f"Preview: {exc}", 6000)

    def fit_previews(self):
        self.source_preview.fit()
        self.output_preview.fit()

    def _config(self):
        from .core import ConverterConfig
        return ConverterConfig(converter_path=self.converter_path.text().strip() or None, converter_kind=self.converter_kind.currentText(), version=self.version.currentText())

    def start_conversion(self, checked=False, retry=False):
        if self.running:
            return
        if self.drawing_mode.currentText() == "P&ID (AI)" and self.pdf_direction.isChecked():
            self._navigate(2, self.nav_actions[3])
            if self.table.currentRow() >= 0:
                self.ai_source.setText(self.jobs[self.table.currentRow()]["source"])
            return
        indices = [i for i, job in enumerate(self.jobs) if job["status"] in ({"Failed", "Cancelled"} if retry else {"Queued", "Failed", "Cancelled"})]
        if not indices:
            self.summary.setText("Add one or more drawings to begin.")
            return
        try:
            destination = Path(self.destination.text()).expanduser()
            destination.mkdir(parents=True, exist_ok=True)
            scale = float(self.scale.text()) if self.pdf_direction.isChecked() else self.scale.text().strip()
            if self.pdf_direction.isChecked() and scale <= 0:
                raise ValueError("Scale must be positive")
            self.batch_config = self._config()
            self.batch_options = {"pages": self.pages.text(), "scale": scale, "password": self.password.text() or None, "raster": self.drawing_mode.currentIndex() == 1} if self.pdf_direction.isChecked() else {"paper": self.paper.currentText(), "orientation": self.orientation.currentText(), "scale": scale, "area": self.area.text(), "monochrome": self.monochrome.isChecked(), "lineweights": self.lineweights.isChecked()}
            self.batch_destination = destination
            self.batch_suffix = "." + self.format.currentText().lower()
        except Exception as exc:
            QMessageBox.warning(self, "Check output options", str(exc))
            return
        self.running = True
        self.pending = indices
        self.active = 0
        self.batch_total = len(indices)
        self.batch_done = 0
        self.batch_started = time.monotonic()
        self.convert_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.retry_button.setEnabled(False)
        self.overall_progress.setValue(0)
        for i in indices:
            self.jobs[i]["cancel"].clear()
            self._job_status(i, "Queued", "Waiting for worker")
        self._pump_queue()

    def _job_status(self, index, status, detail):
        self.jobs[index]["status"] = status
        self.table.item(index, 4).setText(status)
        self.table.item(index, 5).setText(detail)
        self.table.item(index, 5).setToolTip(detail)

    def _pump_queue(self):
        while self.pending and self.active < 2:
            index = self.pending.pop(0)
            job = self.jobs[index]
            if job["cancel"].is_set():
                self._job_status(index, "Cancelled", "Cancelled before start")
                self.batch_done += 1
                continue
            self.active += 1
            self._job_status(index, "Converting", "Reading drawing…")
            output = self.batch_destination / (Path(job["source"]).stem + self.batch_suffix)
            if output.resolve() == Path(job["source"]).resolve():
                output = output.with_name(output.stem + "-converted" + output.suffix)
            used = {Path(j.get("output", "")).resolve() for j in self.jobs if j.get("output")}
            number = 2
            base = output
            while output.exists() or output.resolve() in used:
                output = base.with_name(f"{base.stem}-{number}{base.suffix}")
                number += 1
            job["output"] = str(output)
            def execute(progress, source=job["source"], target=output, cancel=job["cancel"]):
                from .core import convert_file, inspect_cad
                result = convert_file(source, target, config=self.batch_config, progress=progress, cancel=cancel, **self.batch_options)
                if target.suffix.lower() in {".dxf", ".dwg"}:
                    try:
                        preview = target.parent / ".lineaforge-preview" / (target.stem + ".pdf")
                        preview.parent.mkdir(exist_ok=True)
                        convert_file(target, preview, config=self.batch_config, paper="A3", scale="fit", cancel=cancel)
                        result["preview_pdf"] = str(preview)
                        metrics = inspect_cad(target, config=self.batch_config)
                        result["layers"] = len(metrics.get("layers", {}))
                    except Exception as exc:
                        result.setdefault("warnings", []).append(f"CAD output saved; preview unavailable: {exc}")
                elif Path(source).suffix.lower() in {".dwg", ".dxf"}:
                    result["source_preview"] = str(target)
                return result
            task = Task(execute)
            self.task_refs.append(task)
            task.signals.progress.connect(lambda value, msg, i=index: self._conversion_progress(i, value, msg))
            task.signals.result.connect(lambda result, i=index: self._conversion_done(i, result))
            task.signals.error.connect(lambda error, i=index: self._conversion_error(i, error))
            task.signals.finished.connect(lambda i=index, t=task: self._conversion_finished(i, t))
            self.pool.start(task)
        if not self.pending and not self.active:
            self.running = False
            self.convert_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.retry_button.setEnabled(True)
            done = sum(j["status"] == "Done" for j in self.jobs)
            failed = sum(j["status"] == "Failed" for j in self.jobs)
            cancelled = sum(j["status"] == "Cancelled" for j in self.jobs)
            self.overall_progress.setValue(100)
            lead = "Two-way conversion ready" if done else "Batch finished"
            self.summary.setText(f"{lead} · {done} done · {failed} failed · {cancelled} cancelled · {time.monotonic()-self.batch_started:.1f}s")

    def _conversion_progress(self, index, value, message):
        self.jobs[index]["progress"] = value
        self.table.item(index, 5).setText(f"{value}% · {message}")
        self.overall_progress.setValue(int(100 * (self.batch_done + sum(j["progress"] / 100 for j in self.jobs if j["status"] == "Converting")) / self.batch_total))

    def _conversion_done(self, index, result):
        self.jobs[index]["result"] = result
        warnings = result.get("warnings", [])
        self._job_status(index, "Done", f"{result.get('entities', '—')} entities" + (" · " + "; ".join(warnings) if warnings else ""))
        if self.table.currentRow() == index:
            self.show_selected()

    def _conversion_error(self, index, message):
        status = "Cancelled" if "ConversionCancelled" in message else "Failed"
        self._job_status(index, status, message)

    def _conversion_finished(self, index, task):
        self.active -= 1
        self.batch_done += 1
        self.task_refs.remove(task)
        self._pump_queue()

    def cancel_conversion(self):
        for job in self.jobs:
            if job["status"] in {"Queued", "Converting"}:
                job["cancel"].set()
        self.summary.setText("Cancellation requested · workers stop at the next safe checkpoint")

    def retry_failed(self):
        self.start_conversion(retry=True)

    def _selected_output(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.jobs) and self.jobs[row].get("result"):
            return Path(self.jobs[row]["result"]["output"])
        return None

    def open_output(self):
        path = self._selected_output()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.statusBar().showMessage("Select a completed drawing first", 5000)

    def open_folder(self):
        path = self._selected_output()
        path = path.parent if path else Path(self.destination.text())
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def share_output(self):
        path = self._selected_output()
        if path:
            QApplication.clipboard().setText(str(path))
            QMessageBox.information(self, "Ready to share", "The output path is copied. Attach this file in your preferred sharing app. No upload was performed.")

    def _pdf_page(self):
        layout = self._page()
        layout.addLayout(heading("A little order for your PDFs.", "Merge, split, compress, and arrange pages without uploading your files."))
        tabs = QTabWidget()
        layout.addWidget(tabs)
        merge = QWidget()
        m = QVBoxLayout(merge)
        m.addWidget(QLabel("Merge PDFs in the order below. Drag rows to reorder."))
        self.merge_list = QListWidget()
        self.merge_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        m.addWidget(self.merge_list)
        controls = QHBoxLayout()
        controls.addWidget(button("Add PDFs…", self.add_merge_files))
        controls.addWidget(button("Remove selected", lambda: self.merge_list.takeItem(self.merge_list.currentRow())))
        controls.addStretch()
        controls.addWidget(button("Merge PDF files", self.merge_pdf, True))
        m.addLayout(controls)
        tabs.addTab(merge, "Merge")
        split = QWidget()
        s = QFormLayout(split)
        self.split_source = QLineEdit()
        self.split_range = QLineEdit("1-3")
        self.split_every = QSpinBox()
        self.split_every.setRange(0, 10000)
        self.split_every.setSpecialValueText("Use page range")
        self._file_field(s, "Source PDF", self.split_source, "PDF (*.pdf)")
        s.addRow("Page range", self.split_range)
        s.addRow("Every N pages", self.split_every)
        s.addRow(QLabel("A positive N splits the entire PDF into numbered parts; otherwise the range is extracted."))
        s.addRow(button("Split PDF…", self.split_pdf, True))
        tabs.addTab(split, "Split")
        compress = QWidget()
        c = QFormLayout(compress)
        self.compress_source = QLineEdit()
        self.compress_level = combo(["balanced", "lossless", "small"])
        self._file_field(c, "Source PDF", self.compress_source, "PDF (*.pdf)")
        c.addRow("Compression", self.compress_level)
        note = QLabel("Compression is content-dependent. The result reports actual size change; a smaller file is not guaranteed.")
        note.setWordWrap(True)
        c.addRow(note)
        c.addRow(button("Compress PDF…", self.compress_pdf, True))
        tabs.addTab(compress, "Compress")
        organize = QWidget()
        o = QVBoxLayout(organize)
        pathrow = QHBoxLayout()
        self.organize_source = QLineEdit()
        pathrow.addWidget(self.organize_source)
        pathrow.addWidget(button("Load PDF…", self.load_organize))
        o.addLayout(pathrow)
        body = QSplitter()
        self.organize_pages = QListWidget()
        self.organize_pages.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.organize_pages.currentRowChanged.connect(self.preview_organize)
        body.addWidget(self.organize_pages)
        self.organize_preview = Preview()
        body.addWidget(self.organize_preview)
        o.addWidget(body)
        row = QHBoxLayout()
        row.addWidget(button("Rotate 90°", self.rotate_page))
        row.addWidget(button("Delete page", lambda: self.organize_pages.takeItem(self.organize_pages.currentRow())))
        row.addWidget(QLabel("Drag pages to reorder"))
        row.addStretch()
        row.addWidget(button("Save organized PDF…", self.organize_pdf, True))
        o.addLayout(row)
        tabs.addTab(organize, "Organize")
        self.pdf_progress = QProgressBar()
        self.pdf_log = QPlainTextEdit()
        self.pdf_log.setReadOnly(True)
        self.pdf_log.setMaximumHeight(130)
        layout.addWidget(self.pdf_progress)
        layout.addWidget(self.pdf_log)

    def _file_field(self, form, label, field, file_filter):
        row = QHBoxLayout()
        row.addWidget(field)
        def choose():
            path, _ = QFileDialog.getOpenFileName(self, label, "", file_filter)
            if path:
                field.setText(path)
        row.addWidget(button("Browse…", choose))
        form.addRow(label, row)

    def add_merge_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs", "", "PDF (*.pdf)")
        self.merge_list.addItems(paths)

    def _pdf_task(self, fn):
        task = Task(fn)
        self.task_refs.append(task)
        task.signals.progress.connect(lambda value, message: (self.pdf_progress.setValue(value), self.pdf_log.appendPlainText(message)))
        task.signals.result.connect(lambda result: self.pdf_log.appendPlainText(json.dumps(result, indent=2, default=str)))
        task.signals.error.connect(self.pdf_log.appendPlainText)
        task.signals.finished.connect(lambda: self.task_refs.remove(task))
        self.pdf_progress.setValue(0)
        self.pool.start(task)

    def merge_pdf(self):
        sources = [self.merge_list.item(i).text() for i in range(self.merge_list.count())]
        target, _ = QFileDialog.getSaveFileName(self, "Merged PDF", "merged.pdf", "PDF (*.pdf)")
        if target and sources:
            from . import pdf_tools
            self._pdf_task(lambda progress: pdf_tools.merge(sources, target, progress=progress))

    def split_pdf(self):
        from . import pdf_tools
        source, page_range, every = self.split_source.text(), self.split_range.text(), self.split_every.value()
        target = QFileDialog.getExistingDirectory(self, "Split output folder")
        if target:
            self._pdf_task(lambda progress: pdf_tools.split(source, target, ranges=page_range if not every else None, every_n=every or 1, progress=progress))

    def compress_pdf(self):
        from . import pdf_tools
        target, _ = QFileDialog.getSaveFileName(self, "Compressed PDF", "compressed.pdf", "PDF (*.pdf)")
        source, level = self.compress_source.text(), self.compress_level.currentText()
        if target:
            self._pdf_task(lambda progress: pdf_tools.compress(source, target, quality=level, progress=progress))

    def load_organize(self):
        path, _ = QFileDialog.getOpenFileName(self, "Organize PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            with fitz.open(path) as doc:
                if doc.needs_pass:
                    raise ValueError("Encrypted PDF: decrypt a working copy before organizing")
                self.organize_source.setText(path)
                self.organize_pages.clear()
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=fitz.Matrix(.12, .12), alpha=False)
                    image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                    item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), f"Page {i+1}")
                    item.setData(Qt.ItemDataRole.UserRole, [i, 0])
                    self.organize_pages.addItem(item)
        except Exception as exc:
            self.pdf_log.appendPlainText(str(exc))

    def preview_organize(self, row):
        if row >= 0:
            data = self.organize_pages.item(row).data(Qt.ItemDataRole.UserRole)
            try:
                self.organize_preview.load(self.organize_source.text(), data[0])
            except Exception as exc:
                self.pdf_log.appendPlainText(str(exc))

    def rotate_page(self):
        item = self.organize_pages.currentItem()
        if item:
            data = item.data(Qt.ItemDataRole.UserRole)
            data[1] = (data[1] + 90) % 360
            item.setData(Qt.ItemDataRole.UserRole, data)
            item.setText(f"Page {data[0]+1} · {data[1]}°")

    def organize_pdf(self):
        from . import pdf_tools
        target, _ = QFileDialog.getSaveFileName(self, "Organized PDF", "organized.pdf", "PDF (*.pdf)")
        source = self.organize_source.text()
        rows = [self.organize_pages.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.organize_pages.count())]
        if target and rows:
            self._pdf_task(lambda progress: pdf_tools.organize(source, target, order=[r[0] for r in rows], rotations={r[0]: r[1] for r in rows}, progress=progress))

    def _ai_page(self):
        layout = self._page()
        layout.addLayout(heading("From scanned sheet to reviewed graph.", "VLM evidence guides extraction. Unreadable content stays flagged until you resolve it."))
        self.stepper = QLabel("01 Intake   →   02 Denoise   →   03 Understand   →   04 Review   →   05 Build CAD   →   06 QA")
        self.stepper.setObjectName("notice")
        layout.addWidget(self.stepper)
        source = QHBoxLayout()
        self.ai_source = QLineEdit()
        self.ai_source.setPlaceholderText("Select a P&ID PDF; files remain local unless you explicitly consent below")
        self.ai_page_number = QSpinBox()
        self.ai_page_number.setMinimum(1)
        self.ai_page_number.setMaximum(100000)
        self.ai_page_number.setPrefix("Page ")
        source.addWidget(self.ai_source)
        source.addWidget(button("Browse…", self.choose_ai_source))
        source.addWidget(self.ai_page_number)
        source.addWidget(button("Load graph…", self.load_graph))
        layout.addLayout(source)
        privacy = QHBoxLayout()
        self.cloud_consent = QCheckBox("For this job only: allow this drawing to the configured cloud VLM")
        privacy.addWidget(self.cloud_consent)
        privacy.addStretch()
        self.ai_run = button("Run P&ID analysis", self.run_ai, True)
        privacy.addWidget(self.ai_run)
        privacy.addWidget(button("Cancel", lambda: self.ai_cancel.set()))
        layout.addLayout(privacy)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.overlay_filters = {}
        for kind in ["equipment", "valve", "instrument", "pipe", "text"]:
            check = QCheckBox(kind.title())
            check.setChecked(True)
            check.toggled.connect(self.refresh_overlays)
            self.overlay_filters[kind] = check
            filters.addWidget(check)
        ll.addLayout(filters)
        self.ai_preview = Preview()
        self.ai_preview.element_selected.connect(self.select_element)
        self.ai_preview.region_drawn.connect(self.manual_bounds)
        ll.addWidget(self.ai_preview)
        noise_controls = QHBoxLayout()
        noise_controls.addWidget(QLabel("Noise comparison tile"))
        self.noise_tile = QComboBox()
        self.noise_tile.currentIndexChanged.connect(self.show_noise_tile)
        noise_controls.addWidget(self.noise_tile)
        self.noise_slider = QSlider(Qt.Orientation.Horizontal)
        self.noise_slider.setRange(0, 100)
        self.noise_slider.setValue(50)
        self.noise_slider.setAccessibleName("Before and after noise removal split position")
        self.noise_slider.valueChanged.connect(self.update_noise_comparison)
        noise_controls.addWidget(self.noise_slider)
        ll.addLayout(noise_controls)
        self.noise_tabs = QTabWidget()
        self.noise_before = Preview()
        self.noise_after = Preview()
        self.noise_compare = Preview()
        self.noise_diff = Preview()
        self.noise_tabs.addTab(self.noise_before, "Before")
        self.noise_tabs.addTab(self.noise_after, "After")
        self.noise_tabs.addTab(self.noise_compare, "Before / after slider")
        self.noise_tabs.addTab(self.noise_diff, "Diff / content loss")
        self.noise_tabs.setMaximumHeight(240)
        ll.addWidget(self.noise_tabs)
        self.loss_report = QLabel("Content-loss check has not run. No cleaned image is approved.")
        self.loss_report.setWordWrap(True)
        ll.addWidget(self.loss_report)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.addWidget(QLabel("REVIEW QUEUE · LOWEST CONFIDENCE FIRST"))
        self.review_list = QListWidget()
        self.review_list.currentItemChanged.connect(self.review_current)
        rl.addWidget(self.review_list)
        editor = QFormLayout()
        self.element_kind = combo(["equipment", "valve", "instrument", "pipe", "signal", "text", "connector", "junction", "annotation"])
        self.element_tag = QLineEdit()
        self.element_text = QLineEdit()
        self.element_bbox = QLineEdit()
        self.element_bbox.setPlaceholderText("x0,y0,x1,y1 in source pixels")
        self.element_evidence = QLabel("Select an element or its overlay")
        self.element_evidence.setWordWrap(True)
        editor.addRow("Class", self.element_kind)
        editor.addRow("Tag", self.element_tag)
        editor.addRow("Text", self.element_text)
        editor.addRow("Bounds", self.element_bbox)
        editor.addRow(self.element_evidence)
        rl.addLayout(editor)
        row = QHBoxLayout()
        row.addWidget(button("Accept (Alt+A)", lambda: self.review_decision("ACCEPTED")))
        row.addWidget(button("Save edit (Alt+E)", lambda: self.review_decision("ACCEPTED", edit=True)))
        row.addWidget(button("Reject (Alt+R)", lambda: self.review_decision("REJECTED")))
        rl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("Draw bounds", self.draw_manual_bounds))
        row.addWidget(button("Add missing element", self.manual_element))
        self.accept_threshold = QDoubleSpinBox()
        self.accept_threshold.setRange(.5, 1)
        self.accept_threshold.setSingleStep(.05)
        self.accept_threshold.setValue(.95)
        row.addWidget(self.accept_threshold)
        row.addWidget(button("Accept above", self.bulk_accept))
        rl.addLayout(row)
        self.export_mode = combo(["fidelity", "clean"])
        rl.addWidget(QLabel("Export placement · Fidelity preserves source coordinates"))
        rl.addWidget(self.export_mode)
        rl.addWidget(button("Save reviewed graph JSON…", self.save_graph))
        rl.addWidget(button("Export reviewed CAD…", self.export_cad, True))
        split.addWidget(right)
        split.setSizes([720, 480])
        layout.addWidget(split)
        self.ai_progress = QProgressBar()
        self.ai_log = QPlainTextEdit()
        self.ai_log.setReadOnly(True)
        self.ai_log.setMaximumHeight(135)
        layout.addWidget(self.ai_progress)
        layout.addWidget(self.ai_log)
        for key, fn in [("A", lambda: self.review_decision("ACCEPTED")), ("E", lambda: self.review_decision("ACCEPTED", edit=True)), ("R", lambda: self.review_decision("REJECTED"))]:
            action = QAction(self)
            action.setShortcut("Alt+" + key)
            action.triggered.connect(fn)
            right.addAction(action)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def choose_ai_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "P&ID source", "", "PDF (*.pdf)")
        if path:
            self.ai_source.setText(path)
            self.ai_preview.load(path, self.ai_page_number.value()-1)

    def run_ai(self):
        if self.ai_running:
            return
        source = Path(self.ai_source.text())
        if not source.is_file():
            self.ai_log.appendPlainText("Select a valid PDF first.")
            return
        mode = self.provider_mode.currentText()
        consent = self.cloud_consent.isChecked()
        if mode == "cloud" and not consent:
            self.ai_log.appendPlainText("Cloud job blocked: enable explicit consent for this job before transmitting any page.")
            return
        endpoint, model = self.endpoint.text().strip(), self.model.text().strip()
        if not endpoint or not model:
            self.ai_log.appendPlainText("Configure a running local VLM endpoint and model in Settings. Analysis cannot run without a real model.")
            return
        from .pipeline import Pipeline, PipelineSettings
        from .providers import EndpointProvider
        artifact_dir = Path(self.destination.text()) / "pid-artifacts" / f"{source.stem}-{time.strftime('%Y%m%d-%H%M%S')}"
        settings = PipelineSettings(provider_mode=mode, dpi=self.dpi.value(), tile_size=self.tile_size.value(), overlap=self.overlap.value(), confidence_threshold=self.confidence.value(), keep_markups=self.keep_markups.isChecked(), cloud_consent=consent)
        try:
            provider = EndpointProvider(base_url=endpoint, model=model, mode=mode, allow_external=consent, artifact_dir=artifact_dir, api_key=os.environ.get("LINEAFORGE_VLM_API_KEY"))
        except Exception as exc:
            self.ai_log.appendPlainText(str(exc))
            return
        self.ai_cancel.clear()
        self.ai_running = True
        self.ai_run.setEnabled(False)
        page = self.ai_page_number.value()-1
        def execute(progress):
            def report(event):
                percent = min(95, int(100 * event.get("calls", 0) / settings.max_calls))
                progress(percent, json.dumps(event, ensure_ascii=False))
            return Pipeline(provider, settings, artifact_dir).run(source, page=page, progress=report, cancel=self.ai_cancel)
        task = Task(execute)
        self.task_refs.append(task)
        task.signals.progress.connect(self._ai_progress)
        task.signals.result.connect(self._ai_done)
        task.signals.error.connect(self.ai_log.appendPlainText)
        task.signals.finished.connect(lambda: self._ai_finished(task))
        self.pool.start(task)
        self.cloud_consent.setChecked(False)
        self.ai_log.appendPlainText(f"Job started · {mode} provider · {endpoint} · page {page+1}")

    def _ai_progress(self, value, message):
        self.ai_progress.setValue(value)
        self.ai_log.appendPlainText(message)
        self.stepper.setText(message)

    def _ai_done(self, result):
        self.ai_result = result
        self.ai_graph = result.graph
        self.ai_log.appendPlainText(f"Status: {result.status}\nArtifacts: {result.artifact_dir}")
        self.populate_review()
        try:
            self.ai_preview.load(self.ai_source.text(), self.ai_page_number.value()-1)
            self.refresh_overlays()
        except Exception as exc:
            self.ai_log.appendPlainText(str(exc))
        self.load_noise_artifacts(Path(result.artifact_dir))

    def _ai_finished(self, task):
        self.ai_running = False
        self.ai_run.setEnabled(True)
        self.task_refs.remove(task)

    def load_noise_artifacts(self, directory):
        self.noise_tile.clear()
        for tile in sorted((directory / "tiles").glob("tile-*")):
            if tile.is_dir():
                self.noise_tile.addItem(tile.name, str(tile))
        self.show_noise_tile()

    def show_noise_tile(self, index=None):
        tile_path = self.noise_tile.currentData()
        if not tile_path:
            return
        tile = Path(tile_path)
        for filename, viewer in [("source.png", self.noise_before), ("cleaned.png", self.noise_after), ("noise_diff.png", self.noise_diff)]:
            if (tile / filename).exists():
                viewer.load(tile / filename)
            else:
                viewer.message("Artifact not available")
        self.noise_images = (QImage(str(tile / "source.png")), QImage(str(tile / "cleaned.png")))
        self.update_noise_comparison()
        report = tile / "noise_warnings.json"
        reports = sorted((tile.parent.parent / "agents").glob(f"*{tile.name}*ContentLossVerifier*.json"))
        if report.exists():
            self.loss_report.setText(report.read_text(encoding="utf-8")[:1500])
        elif reports:
            self.loss_report.setText(reports[-1].read_text(encoding="utf-8")[:1500])
        else:
            self.loss_report.setText("Content-loss evidence not available for this tile. Inspect artifacts before approval.")

    def update_noise_comparison(self):
        if hasattr(self, "noise_images"):
            self.noise_compare.compare(*self.noise_images, self.noise_slider.value()/100)

    def load_graph(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load P&ID graph", "", "Graph JSON (*.json)")
        if not path:
            return
        try:
            from .models import PidGraph
            self.ai_graph = PidGraph.model_validate_json(Path(path).read_text(encoding="utf-8"))
            self.populate_review()
            self.refresh_overlays()
        except Exception as exc:
            self.ai_log.appendPlainText(f"Graph validation failed: {exc}")

    def populate_review(self):
        selected = self.review_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.review_list.currentItem() else None
        self.review_list.clear()
        if self.ai_graph:
            for issue in self.ai_graph.review:
                item = QListWidgetItem(f"ISSUE  {issue.reason}  [{issue.status}]")
                item.setData(Qt.ItemDataRole.UserRole, "review:" + issue.id)
                self.review_list.addItem(item)
            for element in sorted(self.ai_graph.elements, key=lambda e: (e.status == "ACCEPTED", e.confidence)):
                item = QListWidgetItem(f"{element.confidence:.0%}  {element.kind}  {element.tag or element.text or element.id}  [{element.status}]")
                item.setData(Qt.ItemDataRole.UserRole, element.id)
                self.review_list.addItem(item)
                if element.id == selected:
                    self.review_list.setCurrentItem(item)
        self.refresh_overlays()

    def refresh_overlays(self):
        if self.ai_graph:
            kinds = {k for k, check in self.overlay_filters.items() if check.isChecked()}
            if "pipe" in kinds:
                kinds |= {"signal", "connector", "junction"}
            if "text" in kinds:
                kinds.add("annotation")
            self.ai_preview.overlays(self.ai_graph.elements, self.ai_graph.width_px, self.ai_graph.height_px, kinds)

    def select_element(self, element_id):
        for i in range(self.review_list.count()):
            if self.review_list.item(i).data(Qt.ItemDataRole.UserRole) == element_id:
                self.review_list.setCurrentRow(i)
                return

    def current_element(self):
        item = self.review_list.currentItem()
        if not item or not self.ai_graph:
            return None
        return next((e for e in self.ai_graph.elements if e.id == item.data(Qt.ItemDataRole.UserRole)), None)

    def review_current(self, item, previous=None):
        if item and str(item.data(Qt.ItemDataRole.UserRole)).startswith("review:"):
            issue = next(i for i in self.ai_graph.review if i.id == item.data(Qt.ItemDataRole.UserRole)[7:])
            self.element_evidence.setText(f"Validation issue: {issue.reason}\nEvidence: {issue.evidence or 'Inspect source location'}\nStatus: {issue.status}")
            return
        element = self.current_element()
        if not element:
            return
        self.element_kind.setCurrentText(element.kind)
        self.element_tag.setText(element.tag or "")
        self.element_text.setText(element.text or "")
        self.element_bbox.setText(",".join(f"{v:g}" for v in element.bbox))
        self.element_evidence.setText(f"{element.id} · {element.confidence:.0%} confidence\nAgent: {element.source_agent} · tile: {element.tile_id}\nStatus: {element.status}")

    def review_decision(self, status, edit=False):
        item = self.review_list.currentItem()
        if item and str(item.data(Qt.ItemDataRole.UserRole)).startswith("review:"):
            issue = next(i for i in self.ai_graph.review if i.id == item.data(Qt.ItemDataRole.UserRole)[7:])
            issue.status = status
            self.populate_review()
            return
        element = self.current_element()
        if not element:
            return
        if status == "ACCEPTED" and element.status == "UNREADABLE" and not edit:
            self.ai_log.appendPlainText("Unreadable element: use Save edit to supply observed text, or reject it explicitly.")
            return
        if edit:
            try:
                bbox = [float(x.strip()) for x in self.element_bbox.text().split(",")]
                if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    raise ValueError("Use x0,y0,x1,y1 with positive width and height")
                edited = element.model_copy(deep=True)
                edited.status = "ACCEPTED"
                edited.bbox = bbox
                edited.kind = self.element_kind.currentText()
                edited.tag = self.element_tag.text() or None
                edited.text = self.element_text.text()
                edited.attributes["human_edited"] = "true"
                from .models import Element, PidGraph
                edited = Element.model_validate(edited.model_dump())
                graph = self.ai_graph.model_dump()
                graph["elements"] = [edited.model_dump() if e.id == element.id else e.model_dump() for e in self.ai_graph.elements]
                self.ai_graph = PidGraph.model_validate(graph)
                element = next(e for e in self.ai_graph.elements if e.id == edited.id)
            except Exception as exc:
                self.ai_log.appendPlainText(str(exc))
                return
        element.status = status
        element.attributes["reviewed_by"] = "human"
        self.populate_review()

    def draw_manual_bounds(self):
        if not self.ai_graph:
            self.ai_log.appendPlainText("Load a graph and its source page first.")
            return
        self.ai_preview.draw_mode = True
        self.ai_preview.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.ai_log.appendPlainText("Drag a rectangle over the source page, then choose class/tag and Add missing element.")

    def manual_bounds(self, bounds):
        if self.ai_graph:
            sx = self.ai_graph.width_px / self.ai_preview.source_size[0]
            sy = self.ai_graph.height_px / self.ai_preview.source_size[1]
            values = [max(0, min(self.ai_graph.width_px if i % 2 == 0 else self.ai_graph.height_px, v * (sx if i % 2 == 0 else sy))) for i, v in enumerate(bounds)]
            self.element_bbox.setText(",".join(f"{v:.1f}" for v in values))

    def manual_element(self):
        if not self.ai_graph:
            self.ai_log.appendPlainText("Load or extract a graph before adding an element.")
            return
        try:
            from .models import Element
            bbox = [float(x.strip()) for x in self.element_bbox.text().split(",")]
            if len(bbox) != 4:
                raise ValueError("Enter four bounds in source-page pixels")
            element = Element(id=f"manual-{time.time_ns()}", kind=self.element_kind.currentText(), subclass="human", tag=self.element_tag.text() or None, text=self.element_text.text(), bbox=bbox, confidence=1, tile_id="manual", source_agent="human", status="ACCEPTED")
            from .models import PidGraph
            graph = self.ai_graph.model_dump()
            graph["elements"].append(element.model_dump())
            self.ai_graph = PidGraph.model_validate(graph)
            self.populate_review()
        except Exception as exc:
            self.ai_log.appendPlainText(f"Cannot add element: {exc}")

    def bulk_accept(self):
        if self.ai_graph:
            count = 0
            for element in self.ai_graph.elements:
                if element.confidence >= self.accept_threshold.value() and element.status not in {"UNREADABLE", "REJECTED"}:
                    element.status = "ACCEPTED"
                    element.attributes["reviewed_by"] = "human_bulk"
                    count += 1
            self.ai_log.appendPlainText(f"Human accepted {count} elements at threshold {self.accept_threshold.value():.0%}.")
            self.populate_review()

    def save_graph(self):
        if not self.ai_graph:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Reviewed graph", "pid_graph_reviewed.json", "JSON (*.json)")
        if path:
            Path(path).write_text(self.ai_graph.model_dump_json(indent=2), encoding="utf-8")
            self.ai_log.appendPlainText(f"Reviewed graph saved: {path}")

    def export_cad(self):
        if not self.ai_graph:
            self.ai_log.appendPlainText("No graph is loaded.")
            return
        unreviewed = [e for e in self.ai_graph.elements if e.status not in {"ACCEPTED", "REJECTED"}]
        issues = [i for i in self.ai_graph.review if i.status == "NEEDS_HUMAN"]
        if unreviewed or issues:
            self.ai_log.appendPlainText(f"CAD export gated: review {len(unreviewed)} elements and {len(issues)} validation issues. Unreadable content must be resolved or rejected explicitly.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CAD", "pid-reviewed.dxf", "DXF (*.dxf);;DWG, external converter required (*.dwg)")
        if not path:
            return
        from .cad import EzdxfBackend, AutoCadMcpBackend, SolidWorksMcpBackend, build_graph_cad
        graph, mode, config = self.ai_graph.model_copy(deep=True), self.export_mode.currentText(), self._config()
        backend_index = self.cad_backend.currentIndex()
        def build(progress):
            output = Path(path)
            dxf = output.with_suffix(".dxf")
            backend = EzdxfBackend() if backend_index == 0 else (AutoCadMcpBackend(output_root=output.parent) if backend_index == 1 else SolidWorksMcpBackend(output_root=output.parent))
            progress(10, "Building accepted graph through selected CAD backend")
            result = build_graph_cad(graph, backend, dxf if backend_index == 0 else output, mode=mode)
            if backend_index == 0:
                from .cad_qa import run_cad_qa
                progress(80, "Reopening CAD and rendering the source comparison")
                result["cad_qa"] = run_cad_qa(graph, dxf, output.parent / (output.stem + "-cad-qa"))
            if output.suffix.lower() == ".dwg" and backend_index == 0:
                from .core import convert_file
                result["dwg_conversion"] = convert_file(dxf, output, config=config, progress=progress)
            progress(100, "CAD built. Independent VLM render-compare QA is still required before release.")
            return result
        task = Task(build)
        self.task_refs.append(task)
        task.signals.progress.connect(self._ai_progress)
        task.signals.result.connect(lambda result: self.ai_log.appendPlainText(str(result)))
        task.signals.error.connect(self.ai_log.appendPlainText)
        task.signals.finished.connect(lambda: self.task_refs.remove(task))
        self.pool.start(task)

    def _settings_page(self):
        layout = self._page()
        layout.addLayout(heading("Your tools. Your boundary.", "Connect the conversion and model services you operate. Cloud transmission requires consent for each job."))
        converter = QGroupBox("CAD conversion")
        f = QFormLayout(converter)
        self.converter_path = QLineEdit(self.preferences.value("converter_path", ""))
        self.converter_kind = combo(["oda", "libredwg"])
        self.converter_kind.setCurrentText(self.preferences.value("converter_kind", "oda"))
        self._file_field(f, "Converter executable", self.converter_path, "Executable (*.exe);;All files (*)")
        f.addRow("Converter kind", self.converter_kind)
        note = QLabel("DWG conversion needs ODA File Converter or LibreDWG. Install separately and review their licensing before redistribution. DXF and PDF operate without licensed CAD.")
        note.setWordWrap(True)
        f.addRow(note)
        layout.addWidget(converter)
        ai = QGroupBox("P&ID model and review")
        a = QFormLayout(ai)
        self.provider_mode = combo(["local", "cloud"])
        self.endpoint = QLineEdit(self.preferences.value("endpoint", "http://127.0.0.1:8000/v1"))
        self.model = QLineEdit(self.preferences.value("model", ""))
        self.model.setPlaceholderText("Your deployed vision-language model name")
        self.dpi = QSpinBox()
        self.dpi.setRange(300, 600)
        self.dpi.setValue(300)
        self.tile_size = QSpinBox()
        self.tile_size.setRange(512, 4096)
        self.tile_size.setValue(1536)
        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(.1, .5)
        self.overlap.setSingleStep(.05)
        self.overlap.setValue(.2)
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(.5, 1)
        self.confidence.setValue(.85)
        self.confidence.setSingleStep(.05)
        self.keep_markups = QCheckBox("Keep meaningful markups on a separate layer")
        self.keep_markups.setChecked(True)
        self.standard = combo(["Custom from supplied examples", "ISA 5.1 reference", "ISO 14617 reference"])
        self.standard.setEnabled(False)
        self.standard.setToolTip("The current extraction prompts follow the supplied symbol catalog. Additional standard profiles are not implemented in this beta.")
        self.cad_backend = combo(["Offline DXF (ezdxf)", "AutoCAD MCP — requires licensed local CAD", "SolidWorks MCP — alternate local backend"])
        for label, widget in [("Provider", self.provider_mode), ("OpenAI-compatible endpoint", self.endpoint), ("Vision model", self.model), ("Render DPI", self.dpi), ("Tile size", self.tile_size), ("Overlap", self.overlap), ("Review threshold", self.confidence), ("Standard profile reference", self.standard), ("CAD backend availability", self.cad_backend)]:
            a.addRow(label, widget)
        a.addRow(self.keep_markups)
        a.addRow(button("Open block library / layer map folder", self.open_library))
        privacy = QLabel("Local mode requires a literal loopback address such as 127.0.0.1. Cloud mode logs transmitted artifacts and requires per-job consent. Set LINEAFORGE_VLM_API_KEY in the environment for authenticated endpoints; secrets are never saved by this UI.")
        privacy.setWordWrap(True)
        privacy.setObjectName("notice")
        a.addRow(privacy)
        layout.addWidget(ai)
        layout.addWidget(button("Save local preferences", self.save_settings, True))
        layout.addStretch()

    def open_library(self):
        directory = Path(__file__).resolve().parent.parent / "docs"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def save_settings(self):
        for name, value in [("converter_path", self.converter_path.text()), ("converter_kind", self.converter_kind.currentText()), ("endpoint", self.endpoint.text()), ("model", self.model.text())]:
            self.preferences.setValue(name, value)
        self.statusBar().showMessage("Local preferences saved. Cloud consent is never persisted.", 7000)

    def closeEvent(self, event):
        if self.running or self.ai_running or self.task_refs:
            self.cancel_conversion()
            self.ai_cancel.set()
            self.statusBar().showMessage("Stopping active work at a safe checkpoint. Close again after workers finish.")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LineaForge")
    app.setOrganizationName("LineaForge")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
