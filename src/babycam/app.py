"""PySide6 desktop shell for BabyCAM's first vertical slice."""

from __future__ import annotations

import math
import os
import sys
from ctypes import CDLL
from dataclasses import replace
from pathlib import Path

import ezdxf
from ezdxf import units
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsItem,
    QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSplitter, QSpinBox, QStyle, QTabWidget, QVBoxLayout, QWidget, QInputDialog,
)

from .cam import (CAMError, dogbone_centers, grbl_gcode, number, plan_program, planned_path,
                  preview_paths)
from .dxf_import import UnitSelectionRequired, load_dxf
from .model import Job, Operation, Point, Tool
from .path_edits import split_for_tabs
from .tool_library import copy_tool, load_tools, save_tools


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


def _positive_field(field: QLineEdit) -> bool:
    value = _float(field.text())
    return value is not None and value > 0


def _positive_integer_field(field: QLineEdit) -> bool:
    try:
        return int(field.text()) > 0
    except ValueError:
        return False


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
        try:
            self.tool_presets = load_tools()
            self.library_error = None
        except (OSError, ValueError, TypeError) as exc:
            self.tool_presets = []
            self.library_error = f"Could not read tool library: {exc}"
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

        self.message_panel = QFrame()
        self.message_panel.setFrameShape(QFrame.Shape.StyledPanel)
        message_layout = QHBoxLayout(self.message_panel)
        message_layout.setContentsMargins(8, 5, 8, 5)
        self.message_icon = QLabel()
        message_layout.addWidget(self.message_icon)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message_panel.setMinimumHeight(42)
        message_layout.addWidget(self.message, 1)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.message_panel)
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
        if self.library_error:
            self._set_message(self.library_error, error=True)

    def _set_message(self, text: str, *, error: bool = False) -> None:
        self.message.setText(f"Error: {text}" if error else text)
        font = self.message.font()
        font.setBold(error)
        self.message.setFont(font)
        self.message_icon.setVisible(error)
        if error:
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
            self.message_icon.setPixmap(icon.pixmap(20, 20))

    @staticmethod
    def _mark_invalid(widget: QWidget, invalid: bool) -> None:
        if widget.property("invalid") == invalid:
            return
        widget.setProperty("invalid", invalid)
        kind = "QComboBox" if isinstance(widget, QComboBox) else "QSpinBox" if isinstance(widget, QSpinBox) else "QLineEdit"
        widget.setStyleSheet(f"{kind} {{ border: 2px solid #c94343; border-radius: 3px; }}" if invalid else "")

    def _update_field_highlights(self) -> None:
        for field in (self.thickness, self.safe_z, self.diameter, self.feed,
                      self.plunge, self.stepdown):
            self._mark_invalid(field, not _positive_field(field))
        self._mark_invalid(self.tool_name, not bool(self.tool_name.text().strip()))
        for field in (self.tool_number, self.rpm):
            self._mark_invalid(field, not _positive_integer_field(field))
        for field in (self.stock_width, self.stock_height, self.cutting_length, self.tab_width):
            self._mark_invalid(field, bool(field.text().strip()) and not _positive_field(field))
        for field in (self.offset_x, self.offset_y, self.allowance, self.delay,
                      self.dogbone_clearance):
            value = _float(field.text())
            self._mark_invalid(field, value is None or (field in (self.allowance, self.delay,
                                                                  self.dogbone_clearance) and value < 0))
        rise = _float(self.tab_rise.text())
        self._mark_invalid(self.tab_rise, rise is None or not 0 < rise <= 100)
        count_invalid = self.tab_min.value() > self.tab_max.value()
        self._mark_invalid(self.tab_min, count_invalid)
        self._mark_invalid(self.tab_max, count_invalid)
        operation = self._current_operation()
        self._mark_invalid(self.depth_mode, operation is not None and self.depth_mode.currentIndex() == 0)
        self._mark_invalid(self.cut_depth, operation is not None and
                           self.depth_mode.currentIndex() == 2 and not _positive_field(self.cut_depth))
        if operation is not None and self.cut_tabs.isChecked():
            self._mark_invalid(self.cut_tab_width, not _positive_field(self.cut_tab_width))
            cut_rise = _float(self.cut_tab_rise.text())
            self._mark_invalid(self.cut_tab_rise, cut_rise is None or not 0 < cut_rise <= 100)
        else:
            self._mark_invalid(self.cut_tab_width, False)
            self._mark_invalid(self.cut_tab_rise, False)

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
        self.auto_spindle.toggled.connect(self._read_fields)
        form.addRow(self.auto_spindle)
        edits = QGroupBox("Defaults for newly assigned cuts")
        edit_form = QFormLayout(edits)
        self.default_tabs = QCheckBox("Always create tabs")
        self.default_dogbones = QCheckBox("Always create dogbones")
        for box in (self.default_tabs, self.default_dogbones):
            box.toggled.connect(self._read_fields)
            edit_form.addRow(box)
        self.tab_min = QSpinBox()
        self.tab_min.setRange(1, 99)
        self.tab_min.setValue(2)
        self.tab_max = QSpinBox()
        self.tab_max.setRange(1, 99)
        self.tab_max.setValue(4)
        for label, spin in (("Minimum tabs", self.tab_min), ("Maximum tabs", self.tab_max)):
            spin.valueChanged.connect(self._read_fields)
            edit_form.addRow(label, spin)
        self.tab_width = self._field(edit_form, "Tab width (mm)", hint="stock thickness")
        self.tab_rise = self._field(edit_form, "Tab rise (%)", "75")
        self.dogbone_clearance = self._field(edit_form, "Dogbone extra clearance (mm)", "0.25")
        form.addRow(edits)
        form.addRow(QLabel("XY zero: DXF bounds lower left by default.\nZ zero: stock top."))
        self.tabs.addTab(page, "Setup")

    def _tool_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.preset_picker = QComboBox()
        self._refresh_preset_picker()
        form.addRow("Global tool library", self.preset_picker)
        preset_row = QHBoxLayout()
        use_button = QPushButton("Use selected preset")
        use_button.clicked.connect(self._use_preset)
        preset_row.addWidget(use_button)
        manage_button = QPushButton("Manage presets…")
        manage_menu = QMenu(manage_button)
        manage_menu.addAction("Save or update current endmill…", self._save_preset)
        manage_menu.addSeparator()
        manage_menu.addAction("Delete selected preset…", self._delete_preset)
        manage_button.setMenu(manage_menu)
        preset_row.addWidget(manage_button)
        form.addRow(preset_row)
        form.addRow(QLabel("The job uses a copy. Editing it does not change the preset."))
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
        blank = QPushButton("New blank job endmill")
        blank.clicked.connect(self._blank_tool)
        form.addRow(blank)
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
        self.cut_tabs = QCheckBox("Holding tabs on this cut")
        self.cut_dogbones = QCheckBox("Dogbones on this cut")
        for checkbox in (self.cut_tabs, self.cut_dogbones):
            checkbox.toggled.connect(self._edit_operation)
            form.addRow(checkbox)
        self.cut_tab_min = QSpinBox()
        self.cut_tab_min.setRange(1, 99)
        self.cut_tab_max = QSpinBox()
        self.cut_tab_max.setRange(1, 99)
        for label, spin in (("Minimum tabs", self.cut_tab_min), ("Maximum tabs", self.cut_tab_max)):
            spin.valueChanged.connect(self._edit_operation)
            form.addRow(label, spin)
        self.cut_tab_width = QLineEdit()
        self.cut_tab_width.setPlaceholderText("mm")
        self.cut_tab_width.editingFinished.connect(self._edit_operation)
        form.addRow("Tab width (mm)", self.cut_tab_width)
        self.cut_tab_rise = QLineEdit()
        self.cut_tab_rise.editingFinished.connect(self._edit_operation)
        form.addRow("Tab rise (%)", self.cut_tab_rise)
        self.dogbone_list = QListWidget()
        self.dogbone_list.setMaximumHeight(100)
        self.dogbone_list.itemChanged.connect(self._toggle_dogbone_candidate)
        form.addRow("Relief corners", self.dogbone_list)
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
        setup.default_tabs = self.default_tabs.isChecked()
        setup.default_dogbones = self.default_dogbones.isChecked()
        setup.tab_min = self.tab_min.value()
        setup.tab_max = self.tab_max.value()
        setup.tab_width = _float(self.tab_width.text())
        setup.tab_rise = _float(self.tab_rise.text()) or 0
        setup.dogbone_clearance = _float(self.dogbone_clearance.text()) or 0
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
                             ("Usable cutting length", self.cutting_length), ("Tab width", self.tab_width)):
            if field.text().strip() and _float(field.text()) is None:
                self._input_error = f"Enter a valid {label}."
                break
        if (setup.tab_max < setup.tab_min or not 0 < setup.tab_rise <= 100
                or setup.dogbone_clearance < 0 or
                (setup.tab_width is not None and setup.tab_width <= 0)):
            self._input_error = "Check tab count, width, rise (0–100%), and dogbone clearance."
        self._refresh()

    def _refresh_preset_picker(self) -> None:
        current = self.preset_picker.currentText() if hasattr(self, "preset_picker") else ""
        if not hasattr(self, "preset_picker"):
            return
        self.preset_picker.clear()
        for tool in self.tool_presets:
            self.preset_picker.addItem(f"T{tool.number} · {tool.name}")
        index = self.preset_picker.findText(current)
        if index >= 0:
            self.preset_picker.setCurrentIndex(index)

    def _show_tool(self, tool: Tool) -> None:
        self._refreshing = True
        for field, value in ((self.tool_name, tool.name), (self.tool_number, str(tool.number)),
                             (self.diameter, number(tool.diameter) if tool.diameter is not None else ""),
                             (self.feed, number(tool.feed) if tool.feed is not None else ""),
                             (self.plunge, number(tool.plunge) if tool.plunge is not None else ""),
                             (self.rpm, str(tool.rpm) if tool.rpm is not None else ""),
                             (self.stepdown, number(tool.stepdown) if tool.stepdown is not None else ""),
                             (self.cutting_length, number(tool.cutting_length) if tool.cutting_length is not None else "")):
            field.setText(value)
        self.can_plunge.setChecked(tool.can_plunge)
        self._refreshing = False
        self._read_fields()

    def _use_preset(self) -> None:
        index = self.preset_picker.currentIndex()
        if index < 0:
            self._set_message("The tool library is empty. Enter an endmill and save it as a preset.", error=True)
            return
        self.job.tool = copy_tool(self.tool_presets[index])
        self._show_tool(self.job.tool)

    def _save_preset(self) -> None:
        if self.library_error:
            self._set_message(self.library_error + " Repair the library file before saving presets.", error=True)
            return
        tool = self.job.tool
        if (not tool.name or tool.number < 1 or
                any(value is None or value <= 0 for value in
                    (tool.diameter, tool.feed, tool.plunge, tool.rpm, tool.stepdown))):
            self._set_message("Complete the endmill name, number, diameter, feeds, RPM, and stepdown before saving a preset.", error=True)
            return
        previous = next((i for i, preset in enumerate(self.tool_presets)
                         if preset.name == tool.name), None)
        if previous is not None and QMessageBox.question(
                self, "Update endmill preset",
                f"Replace the global preset '{tool.name}' with the current job endmill?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        updated = [copy_tool(preset) for preset in self.tool_presets]
        if previous is None:
            updated.append(copy_tool(tool))
            selected = len(updated) - 1
        else:
            updated[previous] = copy_tool(tool)
            selected = previous
        try:
            save_tools(updated)
        except OSError as exc:
            self._set_message(f"Could not save tool library: {exc}", error=True)
            return
        self.tool_presets = updated
        self._refresh_preset_picker()
        self.preset_picker.setCurrentIndex(selected)
        self._set_message(f"Saved global endmill preset: {tool.name}.")

    def _delete_preset(self) -> None:
        index = self.preset_picker.currentIndex()
        if index < 0:
            return
        name = self.tool_presets[index].name
        if QMessageBox.question(
                self, "Delete endmill preset", f"Delete the global preset '{name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        remaining = self.tool_presets[:index] + self.tool_presets[index + 1:]
        try:
            save_tools(remaining)
        except OSError as exc:
            self._set_message(f"Could not update tool library: {exc}", error=True)
            return
        self.tool_presets = remaining
        self._refresh_preset_picker()
        self._set_message("Deleted preset. The current job endmill is unchanged.")

    def _blank_tool(self) -> None:
        self.job.tool = Tool()
        self._show_tool(self.job.tool)

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
            self._set_message("Open a DXF first.", error=True)
            return
        if not self.job.tool.name or not self.job.tool.diameter or not self.job.tool.can_plunge:
            self._set_message("Enter an endmill name and diameter and confirm it can plunge before assigning cuts.", error=True)
            self.tabs.setCurrentIndex(1)
            return
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.contours.selectedItems()]
        if not selected:
            self._set_message("Select one or more DXF contours first.", error=True)
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
            setup = self.job.setup
            candidate = Operation(
                identifier, side, tabs=setup.default_tabs, dogbones=setup.default_dogbones,
                tab_min=setup.tab_min, tab_max=setup.tab_max,
                tab_width=setup.tab_width if setup.tab_width is not None else setup.thickness,
                tab_rise=setup.tab_rise)
            try:
                planned_path(self.job, candidate, None)
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
            self._set_message(creation_error, error=True)

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
                if operation.tabs:
                    item.setText(item.text() + " · Tabs")
                if operation.dogbones:
                    try:
                        count = len(dogbone_centers(self.job, operation))
                        item.setText(item.text() + f" · Dogbones {count}")
                    except CAMError:
                        item.setText(item.text() + " · Dogbones")
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
        self.cut_tabs.setChecked(bool(operation and operation.tabs))
        self.cut_dogbones.setChecked(bool(operation and operation.dogbones))
        self.cut_tab_min.setValue((operation.tab_min if operation and operation.tab_min is not None
                                   else self.job.setup.tab_min))
        self.cut_tab_max.setValue((operation.tab_max if operation and operation.tab_max is not None
                                   else self.job.setup.tab_max))
        width = operation.tab_width if operation and operation.tab_width is not None else (
            self.job.setup.tab_width or self.job.setup.thickness)
        self.cut_tab_width.setText(number(width) if width is not None else "")
        rise = operation.tab_rise if operation and operation.tab_rise is not None else self.job.setup.tab_rise
        self.cut_tab_rise.setText(number(rise))
        self.dogbone_list.blockSignals(True)
        self.dogbone_list.clear()
        if operation and operation.dogbones:
            try:
                candidates = dogbone_centers(self.job, operation, include_suppressed=True)
                if not candidates:
                    item = QListWidgetItem("No sharp cutter-side corners found")
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    self.dogbone_list.addItem(item)
                for index, (x, y) in enumerate(candidates):
                    item = QListWidgetItem(f"Corner {index + 1}: X {number(x)}, Y {number(y)}")
                    item.setData(Qt.ItemDataRole.UserRole, index)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked if index in operation.suppressed_dogbones
                                       else Qt.CheckState.Checked)
                    self.dogbone_list.addItem(item)
            except CAMError:
                pass
        self.dogbone_list.blockSignals(False)
        self.cut_depth.setEnabled(bool(operation and operation.depth_mode == "depth"))
        for widget in (self.cut_tabs, self.cut_dogbones, self.cut_tab_min, self.cut_tab_max,
                       self.cut_tab_width, self.cut_tab_rise):
            widget.setEnabled(operation is not None)
        self._refreshing = False
        self._refresh()

    def _edit_operation(self, *_args) -> None:
        if self._refreshing:
            return
        operation = self._current_operation()
        if operation is None:
            return
        editing_depth = self.sender() is self.cut_depth
        if (self.cut_tabs.isChecked() and
                ((self.cut_tab_width.text().strip() and _float(self.cut_tab_width.text()) is None)
                 or _float(self.cut_tab_rise.text()) is None)):
            self._operation_selected()
            self._set_message("Enter a valid tab width and tab rise for this cut.", error=True)
            return
        old = replace(operation, suppressed_dogbones=set(operation.suppressed_dogbones))
        operation.depth_mode = (None, "through", "depth")[self.depth_mode.currentIndex()]
        operation.depth = _float(self.cut_depth.text())
        operation.direction = "conventional" if self.direction.currentIndex() else "climb"
        operation.tabs = self.cut_tabs.isChecked()
        operation.dogbones = self.cut_dogbones.isChecked()
        operation.tab_min = self.cut_tab_min.value()
        operation.tab_max = self.cut_tab_max.value()
        operation.tab_width = _float(self.cut_tab_width.text())
        operation.tab_rise = _float(self.cut_tab_rise.text())
        if operation.tabs and operation.tab_width is None:
            operation.tab_width = self.job.setup.tab_width or self.job.setup.thickness
        try:
            planned_path(self.job, operation, None)
        except CAMError as exc:
            operation.__dict__.update(old.__dict__)
            self._operation_selected()
            self._set_message(str(exc), error=True)
            return
        self.cut_depth.setEnabled(operation.depth_mode == "depth")
        self._update_operation_list()
        if editing_depth:
            self._refresh()  # Keep partially typed values in the depth field.
        else:
            self._operation_selected()

    def _toggle_dogbone_candidate(self, item: QListWidgetItem) -> None:
        if self._refreshing:
            return
        operation = self._current_operation()
        if operation is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        old = set(operation.suppressed_dogbones)
        if item.checkState() == Qt.CheckState.Checked:
            operation.suppressed_dogbones.discard(index)
        else:
            operation.suppressed_dogbones.add(index)
        try:
            planned_path(self.job, operation, None)
        except CAMError as exc:
            operation.suppressed_dogbones = old
            self._operation_selected()
            self._set_message(str(exc), error=True)
            return
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
        self.contours.blockSignals(True)
        self.contours.clearSelection()
        for i in range(self.contours.count()):
            item = self.contours.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in selected_ids)
        self.contours.blockSignals(False)
        self._highlight_contours(selected_ids)

    def _contour_selected(self) -> None:
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.contours.selectedItems()}
        self._refreshing = True
        for item in self.scene.items():
            if item.data(0):
                item.setSelected(item.data(0) in selected_ids)
        self._refreshing = False
        self._highlight_contours(selected_ids)

    def _highlight_contours(self, selected_ids: set[str]) -> None:
        for item in self.scene.items():
            identifier = item.data(0)
            if not identifier:
                continue
            selected = identifier in selected_ids
            color = "#ffe27a" if selected else "#e79081" if item.data(1) else "#d3d8de"
            pen = QPen(QColor(color), 3.5 if selected else 1.5)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(5 if selected else 2)

    def _refresh(self, fit: bool = False) -> None:
        if self._refreshing:
            return
        self._update_field_highlights()
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
                is_selected = contour.id in selected_ids
                pen = QPen(QColor("#ffe27a" if is_selected else
                                  "#e79081" if contour.error else "#d3d8de"),
                           3.5 if is_selected else 1.5)
                pen.setCosmetic(True)
                item = self.scene.addPath(_path(placed(contour.points)), pen)
                item.setData(0, contour.id)
                item.setData(1, bool(contour.error))
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                item.setZValue(5 if is_selected else 2)
                item.setSelected(is_selected)
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
                if planned.tabs:
                    segments = split_for_tabs(planned.points, planned.tabs, planned.tab_width)
                    tab_pen = QPen(QColor("#f5c46b"), 4)
                    tab_pen.setCosmetic(True)
                    for (a, is_tab), (b, _) in zip(segments, segments[1:]):
                        if is_tab:
                            self.scene.addLine(a[0], a[1], b[0], b[1], tab_pen).setZValue(6)
                relief_pen = QPen(QColor("#f6a552"), 2.5)
                relief_pen.setCosmetic(True)
                for i, (bx, by) in enumerate(planned.points):
                    if (bx, by) not in planned.dogbones:
                        continue
                    foot = planned.points[i - 1]
                    self.scene.addLine(foot[0], foot[1], bx, by, relief_pen).setZValue(7)
                    cutter_radius = (self.job.tool.diameter or 1) / 2
                    sweep = self.scene.addEllipse(
                        bx - cutter_radius, by - cutter_radius, 2 * cutter_radius, 2 * cutter_radius,
                        QPen(QColor("#f6a552")), QColor(246, 165, 82, 35))
                    sweep.setZValue(7)
                    relief = self.scene.addEllipse(-3, -3, 6, 6, relief_pen, QColor(246, 165, 82, 180))
                    relief.setPos(bx, by)
                    relief.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                    relief.setData(2, "dogbone")
                    relief.setZValue(8)
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
            lines = [
                f"Ready | Bounds (mm): X {number(x0)} to {number(x1)}, "
                f"Y {number(y0)} to {number(y1)}, Z {number(z0)} to {number(z1)}"
            ]
            if drawing and drawing.warnings:
                ordered = sorted(drawing.warnings,
                                 key=lambda text: not any(word in text.lower() for word in
                                                          ("unsupported", "invalid", "skipped")))
                label = "DXF notice" if len(ordered) == 1 else f"DXF notices ({len(ordered)})"
                lines.append(f"{label}: {ordered[0]}")
                self.message.setToolTip("\n".join(ordered))
            else:
                self.message.setToolTip("")
            lines.extend(f"Warning: {warning}" for warning in program.warnings)
            self._set_message("\n".join(lines))
        except CAMError as exc:
            self.export_action.setEnabled(False)
            issue = errors[0] if errors else str(exc)
            is_error = bool(errors or self._input_error or (
                self.job.operations and all(op.depth_mode is not None for op in self.job.operations)))
            self._set_message(issue, error=is_error)
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
    if (sys.platform.startswith("linux")
            and (os.environ.get("QT_QPA_PLATFORM") == "xcb"
                 or (not os.environ.get("QT_QPA_PLATFORM")
                     and os.environ.get("XDG_SESSION_TYPE") != "wayland"))
            and os.environ.get("DISPLAY")):
        try:
            CDLL("libxcb-cursor.so.0")
        except OSError:
            print("BabyCAM needs libxcb-cursor.so.0 to use Qt's X11 backend. "
                  "On Ubuntu, install it with: sudo apt install libxcb-cursor0",
                  file=sys.stderr)
            return 1
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    result = application.exec()
    window.deleteLater()
    application.processEvents()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
