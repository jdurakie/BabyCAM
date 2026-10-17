"""PySide6 desktop shell for BabyCAM's first vertical slice."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import ezdxf
from ezdxf import units
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGraphicsItem,
    QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget, QInputDialog,
)

from .cam import CAMError, grbl_gcode, number, plan_program, preview_paths, profile_path
from .dxf_import import UnitSelectionRequired, load_dxf
from .model import Job, Operation, Point


def _path(points: tuple[Point, ...] | list[Point]) -> QPainterPath:
    graphic = QPainterPath()
    if points:
        graphic.moveTo(*points[0])
        for point in points[1:]:
            graphic.lineTo(*point)
    return graphic


def _float(text: str) -> float | None:
    try:
        value = float(text) if text.strip() else None
        return value if value is not None and math.isfinite(value) else None
    except ValueError:
        return None


class Canvas(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#181d24"))
        self.setTransform(QTransform.fromScale(1, -1))  # CNC coordinates: +Y is up.
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    def wheelEvent(self, event):  # type: ignore[override]
        self.scale(1.15 if event.angleDelta().y() > 0 else 1 / 1.15,
                   1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()

    def fit_drawing(self) -> None:
        bounds = self.scene().itemsBoundingRect().adjusted(-2, -2, 2, 2)
        if bounds.width() > 0 and bounds.height() > 0:
            self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.job = Job()
        self._input_error: str | None = None
        self._refreshing = False
        self.setWindowTitle("BabyCAM")
        self.resize(1300, 800)

        self.scene = QGraphicsScene(self)
        self.canvas = Canvas(self.scene)
        self.scene.selectionChanged.connect(self._scene_selected)
        self.contours = QListWidget()
        self.contours.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.contours.itemSelectionChanged.connect(self._contour_selected)
        self.operations = QListWidget()
        self.operations.itemSelectionChanged.connect(self._operation_selected)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("DXF contours"))
        left_layout.addWidget(self.contours, 3)
        left_layout.addWidget(QLabel("Cuts (inside first on export)"))
        left_layout.addWidget(self.operations, 2)

        self.tabs = QTabWidget()
        self._setup_tab()
        self._tool_tab()
        self._cut_tab()

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.canvas)
        splitter.addWidget(self.tabs)
        splitter.setSizes([235, 760, 305])
        splitter.setStretchFactor(1, 1)

        self.message = QLabel("Open a DXF to begin.")
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(42)
        self.message.setStyleSheet("padding: 6px; color: #4f3a0b; background: #fff3cf;")
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.message)
        self.setCentralWidget(root)

        open_action = QAction("Open DXF…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_dialog)
        export_action = QAction("Export GRBL…", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self._export)
        self.export_action = export_action
        menu = self.menuBar().addMenu("File")
        menu.addAction(open_action)
        menu.addAction(export_action)
        toolbar = self.addToolBar("Main")
        toolbar.addAction(open_action)
        toolbar.addAction(export_action)
        self._refresh()

    def _field(self, form: QFormLayout, label: str, initial: str = "", hint: str = "") -> QLineEdit:
        field = QLineEdit(initial)
        field.setPlaceholderText(hint)
        field.textChanged.connect(self._read_fields)
        form.addRow(label, field)
        return field

    def _setup_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.thickness = self._field(form, "Stock thickness (mm)", hint="required")
        self.safe_z = self._field(form, "Safe Z above top (mm)", hint="required")
        self.stock_width = self._field(form, "Stock width (mm)", hint="optional")
        self.stock_height = self._field(form, "Stock height (mm)", hint="optional")
        self.offset_x = self._field(form, "DXF X offset (mm)", "0")
        self.offset_y = self._field(form, "DXF Y offset (mm)", "0")
        self.allowance = self._field(form, "Through allowance (mm)", "0.1")
        self.delay = self._field(form, "Spindle delay (s)", "2")
        self.auto_spindle = QCheckBox("Control spindle with M3/M5")
        self.auto_spindle.setChecked(True)
        self.auto_spindle.toggled.connect(self._read_fields)
        form.addRow(self.auto_spindle)
        form.addRow(QLabel("XY zero: DXF bounds lower left by default.\nZ zero: stock top."))
        self.tabs.addTab(page, "Setup")

    def _tool_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.tool_name = self._field(form, "Endmill name", hint="required")
        self.tool_number = self._field(form, "Tool number", "1")
        self.diameter = self._field(form, "Diameter (mm)", hint="required")
        self.feed = self._field(form, "Feed (mm/min)", hint="required")
        self.plunge = self._field(form, "Plunge (mm/min)", hint="required")
        self.rpm = self._field(form, "Spindle RPM", hint="required")
        self.stepdown = self._field(form, "Stepdown (mm)", hint="required")
        self.cutting_length = self._field(form, "Usable cutting length (mm)", hint="optional")
        self.can_plunge = QCheckBox("Can plunge into material")
        self.can_plunge.toggled.connect(self._read_fields)
        form.addRow(self.can_plunge)
        self.tabs.addTab(page, "Endmill")

    def _cut_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Select contours in the drawing or left list."))
        row = QHBoxLayout()
        inside = QPushButton("Assign inside")
        outside = QPushButton("Assign outside")
        inside.clicked.connect(lambda: self._assign("inside"))
        outside.clicked.connect(lambda: self._assign("outside"))
        row.addWidget(inside)
        row.addWidget(outside)
        layout.addLayout(row)
        box = QGroupBox("Selected cut")
        form = QFormLayout(box)
        self.cut_label = QLabel("No cut selected")
        form.addRow(self.cut_label)
        self.depth_mode = QComboBox()
        self.depth_mode.addItems(["Depth not selected", "Through", "To depth"])
        self.depth_mode.currentIndexChanged.connect(self._edit_operation)
        form.addRow("Depth mode", self.depth_mode)
        self.cut_depth = QLineEdit()
        self.cut_depth.setPlaceholderText("mm below stock top")
        self.cut_depth.textChanged.connect(self._edit_operation)
        form.addRow("To depth (mm)", self.cut_depth)
        self.direction = QComboBox()
        self.direction.addItems(["Climb", "Conventional"])
        self.direction.currentIndexChanged.connect(self._edit_operation)
        form.addRow("Cut direction", self.direction)
        remove = QPushButton("Remove selected cut")
        remove.clicked.connect(self._remove_operation)
        form.addRow(remove)
        layout.addWidget(box)
        layout.addStretch()
        self.tabs.addTab(page, "Cuts")

    def _read_fields(self, *_args) -> None:
        if self._refreshing:
            return
        setup, tool = self.job.setup, self.job.tool
        setup.thickness = _float(self.thickness.text())
        setup.safe_z = _float(self.safe_z.text())
        setup.stock_width = _float(self.stock_width.text())
        setup.stock_height = _float(self.stock_height.text())
        setup.offset_x = _float(self.offset_x.text()) or 0.0
        setup.offset_y = _float(self.offset_y.text()) or 0.0
        setup.through_allowance = _float(self.allowance.text()) or 0.0
        setup.spindle_delay = _float(self.delay.text()) or 0.0
        setup.automatic_spindle = self.auto_spindle.isChecked()
        tool.name = self.tool_name.text().strip()
        try:
            tool.number = int(self.tool_number.text())
        except ValueError:
            tool.number = 0
        tool.diameter = _float(self.diameter.text())
        tool.feed = _float(self.feed.text())
        tool.plunge = _float(self.plunge.text())
        rpm = _float(self.rpm.text())
        tool.rpm = int(rpm) if rpm is not None and rpm.is_integer() else None
        tool.stepdown = _float(self.stepdown.text())
        tool.cutting_length = _float(self.cutting_length.text())
        tool.can_plunge = self.can_plunge.isChecked()
        self._input_error = None
        for label, field in (("X offset", self.offset_x), ("Y offset", self.offset_y),
                             ("Through allowance", self.allowance), ("Spindle delay", self.delay)):
            if _float(field.text()) is None:
                self._input_error = f"Enter a valid {label}."
                break
        for label, field in (("Stock width", self.stock_width), ("Stock height", self.stock_height),
                             ("Usable cutting length", self.cutting_length)):
            if field.text().strip() and _float(field.text()) is None:
                self._input_error = f"Enter a valid {label}."
                break
        self._refresh()

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open DXF", "", "DXF drawings (*.dxf)")
        if path:
            self.open_dxf(path)

    def open_dxf(self, path: str) -> None:
        try:
            try:
                drawing = load_dxf(path)
            except UnitSelectionRequired:
                choice, accepted = QInputDialog.getItem(
                    self, "DXF units", "This DXF has no usable units. Choose its units:",
                    ["Millimeters", "Inches"], 0, False)
                if not accepted:
                    return
                drawing = load_dxf(path, units.MM if choice == "Millimeters" else units.IN)
            self.job.drawing = drawing
            self.job.operations.clear()
            self.contours.clear()
            for contour in drawing.contours:
                label = f"{contour.layer} · {contour.id}"
                if contour.error:
                    label += " · invalid"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, contour.id)
                self.contours.addItem(item)
            self._update_operation_list()
            self._refresh(fit=True)
            self.setWindowTitle(f"BabyCAM — {Path(path).name}")
        except (OSError, ValueError, ezdxf.DXFError) as exc:
            QMessageBox.warning(self, "Could not open DXF", str(exc))

    def _assign(self, side: str) -> None:
        if self.job.drawing is None:
            self.message.setText("Open a DXF first.")
            return
        if not self.job.tool.name or not self.job.tool.diameter or not self.job.tool.can_plunge:
            self.message.setText("Enter an endmill name and diameter and confirm it can plunge before assigning cuts.")
            self.tabs.setCurrentIndex(1)
            return
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.contours.selectedItems()]
        if not selected:
            self.message.setText("Select one or more DXF contours first.")
            return
        added: list[str] = []
        creation_error: str | None = None
        for identifier in selected:
            contour = next(c for c in self.job.drawing.contours if c.id == identifier)
            if contour.error:
                creation_error = f"Cannot cut {contour.layer}: {contour.error}."
                continue
            if any(op.contour_id == identifier for op in self.job.operations):
                continue
            candidate = Operation(identifier, side)
            try:
                profile_path(self.job, candidate)
            except CAMError as exc:
                creation_error = str(exc)
                continue
            self.job.operations.append(candidate)
            added.append(identifier)
        self._update_operation_list()
        if added:
            for i in range(self.operations.count()):
                if self.operations.item(i).data(Qt.ItemDataRole.UserRole) == added[0]:
                    self.operations.setCurrentRow(i)
                    break
            self.tabs.setCurrentIndex(2)
        self._refresh()
        if creation_error:
            self.message.setText(creation_error)

    def _update_operation_list(self) -> None:
        current = self._current_operation()
        old_id = current.contour_id if current else None
        self.operations.blockSignals(True)
        self.operations.clear()
        if self.job.drawing:
            labels = {c.id: c.layer for c in self.job.drawing.contours}
            for operation in self.job.operations:
                depth = ("Through" if operation.depth_mode == "through" else
                         f"{number(operation.depth)} mm" if operation.depth_mode == "depth" and operation.depth
                         else "Depth not selected")
                item = QListWidgetItem(f"{operation.side.title()} · {labels.get(operation.contour_id, '?')} · {depth}")
                item.setData(Qt.ItemDataRole.UserRole, operation.contour_id)
                self.operations.addItem(item)
                if operation.contour_id == old_id:
                    self.operations.setCurrentItem(item)
        self.operations.blockSignals(False)

    def _current_operation(self) -> Operation | None:
        item = self.operations.currentItem()
        if item is None:
            return None
        identifier = item.data(Qt.ItemDataRole.UserRole)
        return next((op for op in self.job.operations if op.contour_id == identifier), None)

    def _operation_selected(self) -> None:
        operation = self._current_operation()
        self._refreshing = True
        self.cut_label.setText(operation.contour_id if operation else "No cut selected")
        self.depth_mode.setCurrentIndex(
            1 if operation and operation.depth_mode == "through" else
            2 if operation and operation.depth_mode == "depth" else 0)
        self.cut_depth.setText(number(operation.depth) if operation and operation.depth else "")
        self.direction.setCurrentIndex(1 if operation and operation.direction == "conventional" else 0)
        self.cut_depth.setEnabled(bool(operation and operation.depth_mode == "depth"))
        self._refreshing = False
        self._refresh()

    def _edit_operation(self, *_args) -> None:
        if self._refreshing:
            return
        operation = self._current_operation()
        if operation is None:
            return
        operation.depth_mode = (None, "through", "depth")[self.depth_mode.currentIndex()]
        operation.depth = _float(self.cut_depth.text())
        operation.direction = "conventional" if self.direction.currentIndex() else "climb"
        self.cut_depth.setEnabled(operation.depth_mode == "depth")
        self._update_operation_list()
        self._refresh()

    def _remove_operation(self) -> None:
        operation = self._current_operation()
        if operation:
            self.job.operations.remove(operation)
            self._update_operation_list()
            self._operation_selected()

    def _scene_selected(self) -> None:
        if self._refreshing:
            return
        selected_ids = {item.data(0) for item in self.scene.selectedItems() if item.data(0)}
        if not selected_ids:
            return
        self.contours.blockSignals(True)
        self.contours.clearSelection()
        for i in range(self.contours.count()):
            item = self.contours.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in selected_ids)
        self.contours.blockSignals(False)

    def _contour_selected(self) -> None:
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.contours.selectedItems()}
        self._refreshing = True
        for item in self.scene.items():
            if item.data(0):
                item.setSelected(item.data(0) in selected_ids)
        self._refreshing = False

    def _refresh(self, fit: bool = False) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.contours.selectedItems()}
        self.scene.clear()
        drawing = self.job.drawing
        if drawing:
            min_x, min_y, _, _ = drawing.bounds
            shift_x = self.job.setup.offset_x - min_x
            shift_y = self.job.setup.offset_y - min_y
            def placed(points):
                return [(x + shift_x, y + shift_y) for x, y in points]

            origin = QPainterPath()
            origin.moveTo(-1, 0); origin.lineTo(1, 0)
            origin.moveTo(0, -1); origin.lineTo(0, 1)
            origin_pen = QPen(QColor("#e6ba64"), 1)
            origin_pen.setCosmetic(True)
            self.scene.addPath(origin, origin_pen)
            for reference in drawing.references:
                pen = QPen(QColor("#65717f"), 1, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                self.scene.addPath(_path(placed(reference.points)), pen)
            for contour in drawing.contours:
                pen = QPen(QColor("#d3d8de" if not contour.error else "#e79081"), 1.5)
                pen.setCosmetic(True)
                item = self.scene.addPath(_path(placed(contour.points)), pen)
                item.setData(0, contour.id)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                item.setZValue(2)
                item.setSelected(contour.id in selected_ids)
            paths, errors = preview_paths(self.job)
            selected_op = self._current_operation()
            for planned in paths:
                graphic = _path(planned.points)
                selected = selected_op and planned.operation.contour_id == selected_op.contour_id
                if selected and self.job.tool.diameter:
                    sweep_pen = QPen(QColor(80, 175, 255, 45), self.job.tool.diameter)
                    self.scene.addPath(graphic, sweep_pen).setZValue(3)
                color = "#e47d8f" if planned.operation.depth_mode == "through" else "#6bbce8"
                pen = QPen(QColor(color), 2.5 if selected else 1.5)
                pen.setCosmetic(True)
                self.scene.addPath(graphic, pen).setZValue(4)
                x, y = planned.points[0]
                radius = max((self.job.tool.diameter or 1) / 5, 0.12)
                marker = self.scene.addEllipse(x - radius, y - radius, 2 * radius, 2 * radius,
                                               QPen(QColor(color)), QColor(color))
                marker.setZValue(5)
            if fit:
                self.canvas.fit_drawing()
        else:
            errors = []
        try:
            if self._input_error:
                raise CAMError(self._input_error)
            program = plan_program(self.job)
            self.export_action.setEnabled(True)
            travel = QPainterPath()
            current_x: float | None = None
            current_y: float | None = None
            for move in program.moves:
                new_x = move.x if move.x is not None else current_x
                new_y = move.y if move.y is not None else current_y
                if move.kind == "G0" and (move.x is not None or move.y is not None):
                    if current_x is not None and current_y is not None and new_x is not None and new_y is not None:
                        travel.moveTo(current_x, current_y)
                        travel.lineTo(new_x, new_y)
                current_x, current_y = new_x, new_y
            travel_pen = QPen(QColor("#8190a3"), 1, Qt.PenStyle.DashLine)
            travel_pen.setCosmetic(True)
            self.scene.addPath(travel, travel_pen).setZValue(3)
            x0, x1, y0, y1, z0, z1 = program.bounds
            summary = f"Ready · X {number(x0)}…{number(x1)} · Y {number(y0)}…{number(y1)} · Z {number(z0)}…{number(z1)} mm"
            if drawing and drawing.warnings:
                ordered = sorted(drawing.warnings,
                                 key=lambda text: not any(word in text.lower() for word in
                                                          ("unsupported", "invalid", "skipped")))
                summary += f" · {len(ordered)} DXF notice(s): " + ordered[0]
                self.message.setToolTip("\n".join(ordered))
            else:
                self.message.setToolTip("")
            if program.warnings:
                summary += " · " + " ".join(program.warnings)
            self.message.setText(summary)
        except CAMError as exc:
            self.export_action.setEnabled(False)
            issue = errors[0] if errors else str(exc)
            self.message.setText(issue)
        self._refreshing = False

    def _export(self) -> None:
        try:
            if self._input_error:
                raise CAMError(self._input_error)
            program = plan_program(self.job)
        except CAMError as exc:
            QMessageBox.warning(self, "Cannot export", str(exc))
            return
        suggestion = str(self.job.drawing.path.with_suffix(".nc")) if self.job.drawing else ""
        destination, _ = QFileDialog.getSaveFileName(self, "Export GRBL G-code", suggestion, "G-code (*.nc)")
        if not destination:
            return
        try:
            Path(destination).write_text(grbl_gcode(self.job, program), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Could not export", str(exc))
            return
        self.statusBar().showMessage(f"Exported {destination}", 10000)


def main() -> int:
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    result = application.exec()
    window.deleteLater()
    application.processEvents()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
