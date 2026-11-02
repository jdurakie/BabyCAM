from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ezdxf
from ezdxf import units

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPalette, QPen  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QFileDialog, QGraphicsScene, QMessageBox, QPlainTextEdit, QPushButton,
)

from babycam.app import Canvas, ContourItem, CutHitItem, MainWindow, _tab_crossbar  # noqa: E402
from babycam.cam import planned_path  # noqa: E402
from babycam.path_edits import lengths, place_tabs, point_at  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "keyboard_plate.dxf"
APPLICATION = QApplication.instance() or QApplication([])


class UIWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._recent_config = tempfile.TemporaryDirectory()
        cls._recent_path_patch = patch(
            "babycam.recent_files.recent_files_path",
            return_value=Path(cls._recent_config.name) / "recent_files.json")
        cls._recent_path_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._recent_path_patch.stop()
        cls._recent_config.cleanup()

    def test_open_recent_menu_reopens_file_across_windows(self):
        from babycam import recent_files

        recent_files.save_recent_files([])
        window = MainWindow()
        self.assertFalse(window.recent_button.isEnabled())
        window.open_dxf(str(FIXTURE))
        self.assertTrue(window.recent_button.isEnabled())
        self.assertEqual(recent_files.load_recent_files(), [str(FIXTURE.resolve())])
        window.close()
        window.deleteLater()

        reopened = MainWindow()
        reopened._rebuild_recent_menu()
        entry = reopened.recent_menu.actions()[0]
        self.assertEqual(entry.toolTip(), str(FIXTURE.resolve()))
        entry.trigger()
        self.assertEqual(reopened.job.drawing.path, FIXTURE.resolve())
        self.assertEqual(reopened.windowTitle(), f"BabyCAM — {FIXTURE.name}")
        self.assertEqual(recent_files.load_recent_files(), [str(FIXTURE.resolve())])
        reopened.close()
        reopened.deleteLater()
        APPLICATION.processEvents()

    def test_failed_and_missing_dxf_are_not_added_to_recent_files(self):
        from babycam import recent_files

        recent_files.save_recent_files([])
        window = MainWindow()
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.dxf"
            with patch.object(QMessageBox, "warning") as warning:
                window.open_dxf(str(missing))
            warning.assert_called_once()
            self.assertEqual(recent_files.load_recent_files(), [])
            self.assertFalse(window.recent_button.isEnabled())

            window.open_dxf(str(FIXTURE))
            recent_files.save_recent_files([str(missing), str(FIXTURE)])
            other = MainWindow()
            other._rebuild_recent_menu()
            self.assertFalse(other.recent_menu.actions()[0].isEnabled())
            other.recent_menu.actions()[-1].trigger()
            self.assertEqual(recent_files.load_recent_files(), [])
            self.assertFalse(other.recent_button.isEnabled())
            other.close()
            other.deleteLater()
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_origin_cross_keeps_pixel_size_when_zooming(self):
        window = MainWindow()
        window.show()
        APPLICATION.processEvents()
        window.open_dxf(str(FIXTURE))
        origin = next(item for item in window.scene.items() if item.data(2) == "origin")
        self.assertTrue(origin.flags() & origin.GraphicsItemFlag.ItemIgnoresTransformations)

        for scale in (1, 4, 1 / 12):
            window.canvas.scale(scale, scale)
            transform = origin.deviceTransform(window.canvas.viewportTransform())
            bounds = transform.mapRect(origin.path().boundingRect())
            self.assertAlmostEqual(bounds.width(), 12)
            self.assertAlmostEqual(bounds.height(), 12)
            self.assertLess((transform.map(QPointF(0, 0)) -
                             window.canvas.mapFromScene(QPointF(0, 0))).manhattanLength(), 2)

        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_origin_cross_does_not_affect_fit_for_small_drawing(self):
        scene = QGraphicsScene()
        square = QPainterPath()
        square.addRect(0, 0, 1, 1)
        scene.addPath(square)
        cross = QPainterPath()
        cross.moveTo(-6, 0)
        cross.lineTo(6, 0)
        marker = scene.addPath(cross)
        marker.setFlag(marker.GraphicsItemFlag.ItemIgnoresTransformations)
        canvas = Canvas(scene)
        canvas.resize(300, 200)
        canvas.show()
        APPLICATION.processEvents()

        canvas.fit_drawing()
        scale_with_marker = abs(canvas.transform().m11())
        scene.removeItem(marker)
        canvas.fit_drawing()
        scale_without_marker = abs(canvas.transform().m11())
        self.assertAlmostEqual(scale_with_marker, scale_without_marker)

        canvas.close()
        canvas.deleteLater()
        APPLICATION.processEvents()

    def test_middle_drag_pans_without_changing_selection(self):
        scene = QGraphicsScene()
        scene.setSceneRect(-500, -500, 1000, 1000)
        path = QPainterPath()
        path.addRect(-10, -10, 20, 20)
        contour = ContourItem(path)
        contour.setFlag(contour.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(contour)
        contour.setSelected(True)
        canvas = Canvas(scene)
        canvas.resize(300, 200)
        canvas.show()
        APPLICATION.processEvents()
        canvas.centerOn(0, 0)
        start = canvas.mapFromScene(QPointF(0, 0))
        end = start + QPoint(35, 25)

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=end)

        self.assertEqual(canvas.mapFromScene(QPointF(0, 0)), end)
        self.assertTrue(contour.isSelected())
        self.assertEqual(canvas.dragMode(), canvas.DragMode.RubberBandDrag)
        contour.setSelected(False)
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=end - QPoint(30, 30))
        QTest.mouseMove(canvas.viewport(), end + QPoint(30, 30))
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton,
                           pos=end + QPoint(30, 30))
        self.assertTrue(contour.isSelected())
        canvas.close()
        canvas.deleteLater()
        APPLICATION.processEvents()

    def test_middle_drag_pans_fitted_drawing(self):
        scene = QGraphicsScene()
        path = QPainterPath()
        path.addRect(0, 0, 100, 100)
        scene.addItem(ContourItem(path))
        canvas = Canvas(scene)
        canvas.resize(300, 200)
        canvas.show()
        APPLICATION.processEvents()
        self.assertTrue(canvas.horizontalScrollBar().isVisible())
        self.assertTrue(canvas.verticalScrollBar().isVisible())
        canvas.fit_drawing()
        self.assertGreater(canvas.horizontalScrollBar().maximum(),
                           canvas.horizontalScrollBar().minimum())
        self.assertGreater(canvas.verticalScrollBar().maximum(),
                           canvas.verticalScrollBar().minimum())
        start = canvas.mapFromScene(QPointF(50, 50))
        end = start + QPoint(35, 25)

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=end)

        self.assertEqual(canvas.mapFromScene(QPointF(50, 50)), end)
        canvas.close()
        canvas.deleteLater()
        APPLICATION.processEvents()

    def test_shift_left_drag_pans_without_selecting(self):
        scene = QGraphicsScene()
        scene.setSceneRect(-500, -500, 1000, 1000)
        path = QPainterPath()
        path.addRect(-10, -10, 20, 20)
        contour = ContourItem(path)
        contour.setFlag(contour.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(contour)
        canvas = Canvas(scene)
        canvas.resize(300, 200)
        canvas.show()
        APPLICATION.processEvents()
        canvas.centerOn(0, 0)
        start = canvas.mapFromScene(QPointF(0, 0))
        end = start + QPoint(35, 25)

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.ShiftModifier, pos=end)

        self.assertEqual(canvas.mapFromScene(QPointF(0, 0)), end)
        self.assertFalse(contour.isSelected())
        self.assertIsNone(canvas._pan_last_pos)
        canvas.close()
        canvas.deleteLater()
        APPLICATION.processEvents()

    def test_large_contour_and_cut_hit_areas_follow_canvas_zoom(self):
        scene = QGraphicsScene()
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(4000, 0)
        contour = ContourItem(path)
        contour.setFlag(contour.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(contour)
        clicked = []
        cut = CutHitItem(path, clicked.append)
        scene.addItem(cut)
        canvas = Canvas(scene)
        canvas.resize(400, 250)
        canvas.show()
        APPLICATION.processEvents()
        canvas.fit_drawing()

        for click_count, scale in enumerate((1, 3, 1 / 9), 1):
            canvas.scale(scale, scale)
            canvas.update_hit_widths()
            center = canvas.mapFromScene(QPointF(2000, 0))
            near = canvas.mapToScene(center + QPoint(0, 4))
            far = canvas.mapToScene(center + QPoint(0, 9))
            self.assertIn(contour, scene.items(near))
            self.assertIn(cut, scene.items(near))
            self.assertNotIn(contour, scene.items(far))
            self.assertNotIn(cut, scene.items(far))
            QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
                             pos=center + QPoint(0, 4))
            APPLICATION.processEvents()
            self.assertEqual(len(clicked), click_count)

        scene.removeItem(cut)
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=center + QPoint(0, 4))
        self.assertTrue(contour.isSelected())

        canvas.close()
        canvas.deleteLater()
        APPLICATION.processEvents()

    def test_tab_crossbar_spans_cutter_width_perpendicular_to_path(self):
        points = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0))
        self.assertEqual(_tab_crossbar(points, 5, 2), ((5, -1), (5, 1)))
        self.assertEqual(_tab_crossbar(points, 15, 2), ((11, 5), (9, 5)))

    def test_selected_cut_panel_follows_cut_selection(self):
        window = MainWindow()
        self.assertFalse(window.selected_cut_box.isEnabled())
        window.open_dxf(str(FIXTURE))
        for field, value in ((window.thickness, "6"), (window.tool_name, "flat"),
                             (window.diameter, "1")):
            field.setText(value)
        window.can_plunge.setChecked(True)
        window.contours.setCurrentRow(0)
        window._assign("outside")
        self.assertTrue(window.selected_cut_box.isEnabled())
        self.assertTrue(window.depth_mode.isEnabled())
        window.operations.setCurrentRow(-1)
        self.assertFalse(window.selected_cut_box.isEnabled())
        self.assertFalse(window.depth_mode.isEnabled())
        window.operations.setCurrentRow(0)
        self.assertTrue(window.selected_cut_box.isEnabled())
        window.open_dxf(str(FIXTURE))
        self.assertFalse(window.selected_cut_box.isEnabled())
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_selected_contour_has_no_inset_dotted_box(self):
        scene = QGraphicsScene()
        path = QPainterPath()
        path.addRect(0, 0, 20, 20)
        item = ContourItem(path)
        pen = QPen(QColor("#ffe27a"), 3.5)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setFlag(item.GraphicsItemFlag.ItemIsSelectable)
        scene.addItem(item)

        def render() -> QImage:
            image = QImage(200, 200, QImage.Format.Format_RGB32)
            image.fill(QColor("#181d24"))
            painter = QPainter(image)
            scene.render(painter, QRectF(0, 0, 200, 200), QRectF(-2, -2, 24, 24))
            painter.end()
            return image

        unselected = render()
        item.setSelected(True)
        self.assertEqual(render(), unselected)

    def test_required_fields_turn_red_until_valid(self):
        window = MainWindow()
        for field in (window.thickness, window.safe_z, window.tool_name, window.diameter,
                      window.feed, window.plunge, window.rpm, window.stepdown):
            self.assertTrue(field.property("invalid"))
        self.assertFalse(window.tool_number.property("invalid"))
        self.assertFalse(window.cutting_length.property("invalid"))
        window.thickness.setText("0")
        self.assertTrue(window.thickness.property("invalid"))
        window.thickness.setText("6")
        self.assertFalse(window.thickness.property("invalid"))
        window.feed.setText("abc")
        self.assertTrue(window.feed.property("invalid"))
        window.feed.setText("600")
        self.assertFalse(window.feed.property("invalid"))
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_to_depth_field_stays_editable_and_marks_invalid_values(self):
        window = MainWindow()
        window.open_dxf(str(FIXTURE))
        for field, value in ((window.thickness, "6"), (window.safe_z, "5"),
                             (window.tool_name, "flat"), (window.diameter, "1"),
                             (window.feed, "600"), (window.plunge, "250"),
                             (window.rpm, "18000"), (window.stepdown, "1.5")):
            field.setText(value)
        window.can_plunge.setChecked(True)
        window.contours.setCurrentRow(0)
        window._assign("outside")
        self.assertTrue(window.depth_mode.property("invalid"))
        self.assertFalse(window.cut_depth.isEnabled())
        self.assertFalse(window.cut_depth_label.isEnabled())
        palette = window.cut_depth.palette()
        self.assertNotEqual(
            palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base),
            palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base))
        window.tabs.setCurrentIndex(2)
        window.show()
        APPLICATION.processEvents()
        self.assertLessEqual(
            window.depth_mode.geometry().top() - window.cut_label.geometry().bottom(), 12)
        controls = (window.tabs, window.export_button, window.export_info,
                    window.cut_label, window.depth_mode,
                    window.cut_depth, window.direction, window.tab_auto_button, window.cut_tab_rise)
        positions = [control.geometry() for control in controls]
        window.depth_mode.setCurrentIndex(1)
        APPLICATION.processEvents()
        self.assertEqual([control.geometry() for control in controls], positions)
        self.assertFalse(window.cut_depth.isEnabled())
        self.assertFalse(window.cut_depth_label.isEnabled())
        window.depth_mode.setCurrentIndex(0)
        APPLICATION.processEvents()
        self.assertEqual([control.geometry() for control in controls], positions)
        window.depth_mode.setCurrentIndex(2)
        APPLICATION.processEvents()
        self.assertEqual([control.geometry() for control in controls], positions)
        self.assertTrue(window.cut_depth.isEnabled())
        self.assertTrue(window.cut_depth_label.isEnabled())
        self.assertFalse(window.depth_mode.property("invalid"))
        self.assertTrue(window.cut_depth.property("invalid"))
        sweep = next(item for item in window.scene.items() if item.data(2) == "cutter-sweep")
        self.assertEqual(sweep.pen().color().name(), "#e47d8f")
        window.cut_depth.setText("2.")
        self.assertEqual(window.cut_depth.text(), "2.")
        self.assertFalse(window.cut_depth.property("invalid"))
        window.cut_depth.setText("2.5")
        self.assertEqual(window.job.operations[0].depth, 2.5)
        sweep = next(item for item in window.scene.items() if item.data(2) == "cutter-sweep")
        self.assertEqual(sweep.pen().color().name(), "#7890ff")
        self.assertTrue(window.export_action.isEnabled())
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_preset_update_and_delete_require_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("babycam.tool_library.library_path", return_value=Path(directory) / "tools.json"):
                window = MainWindow()
                for field, value in ((window.tool_name, "flat"), (window.diameter, "1"),
                                     (window.feed, "600"), (window.plunge, "250"),
                                     (window.rpm, "18000"), (window.stepdown, "1.5")):
                    field.setText(value)
                window._save_preset()
                window.feed.setText("800")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                    window._save_preset()
                    window._delete_preset()
                self.assertEqual(window.tool_presets[0].feed, 600)
                self.assertEqual(window.preset_picker.count(), 1)
                window.close()
                window.deleteLater()
                APPLICATION.processEvents()

    def test_selection_and_errors_are_visible(self):
        window = MainWindow()
        window._assign("inside")
        self.assertEqual(window.export_info.text(), "Warnings: 0   Errors: 1")
        self.assertTrue(window.export_details_button.isEnabled())
        self.assertEqual(window.export_button.property("exportState"), "idle")
        window.open_dxf(str(FIXTURE))
        contour_id = window.contours.item(0).data(Qt.ItemDataRole.UserRole)
        path_item = next(item for item in window.scene.items() if item.data(0) == contour_id)
        normal_width = path_item.pen().widthF()
        window.contours.setCurrentRow(0)
        self.assertGreater(path_item.pen().widthF(), normal_width)
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_clicking_inner_contour_replaces_selected_outer_contour(self):
        window = MainWindow()
        window.open_dxf(str(FIXTURE))
        window.show()
        APPLICATION.processEvents()
        outer_id = next(contour.id for contour in window.job.drawing.contours
                        if contour.layer == "OUTER")
        inner_id = next(contour.id for contour in window.job.drawing.contours
                        if contour.layer == "STABILIZER")
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(QPointF(17, 21)))
        self.assertEqual({item.data(Qt.ItemDataRole.UserRole)
                          for item in window.contours.selectedItems()}, {outer_id})

        # This segment is inside the outer contour's enclosed area.
        point = QPointF(17, 3.0191)
        self.assertEqual(window.scene.items(point)[0].data(0), inner_id)
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(point))
        self.assertEqual({item.data(Qt.ItemDataRole.UserRole)
                          for item in window.contours.selectedItems()}, {inner_id})
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_selecting_another_contour_deselects_the_current_cut(self):
        window = MainWindow()
        window.open_dxf(str(FIXTURE))
        window.tool_name.setText("flat")
        window.diameter.setText("1")
        window.can_plunge.setChecked(True)
        outer_id = window.contours.item(0).data(Qt.ItemDataRole.UserRole)
        inner_id = window.contours.item(2).data(Qt.ItemDataRole.UserRole)
        window.contours.setCurrentRow(0)
        window._assign("outside")
        self.assertEqual(window._current_operation().contour_id, outer_id)

        window.contours.setCurrentRow(2)
        self.assertIsNone(window._current_operation())
        self.assertEqual(window.cut_label.text(), "No cut selected")
        self.assertEqual({item.data(Qt.ItemDataRole.UserRole)
                          for item in window.contours.selectedItems()}, {inner_id})

        window.contours.setCurrentRow(0)
        window._select_operation_by_id(outer_id)
        window.show()
        APPLICATION.processEvents()
        point = QPointF(17, 3.0191)
        self.assertTrue(any(item.data(0) == inner_id for item in window.scene.items(point)))
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(point))
        APPLICATION.processEvents()
        self.assertIsNone(window._current_operation())
        self.assertEqual(window.cut_label.text(), "No cut selected")

        window._select_operation_by_id(outer_id)
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(QPointF(50, 50)))
        APPLICATION.processEvents()
        self.assertIsNone(window._current_operation())
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_clicking_cut_centerline_selects_its_cut(self):
        window = MainWindow()
        window.open_dxf(str(FIXTURE))
        for field, value in ((window.thickness, "6"), (window.safe_z, "5"),
                             (window.tool_name, "flat"), (window.diameter, "1"),
                             (window.feed, "600"), (window.plunge, "250"),
                             (window.rpm, "18000"), (window.stepdown, "1.5")):
            field.setText(value)
        window.can_plunge.setChecked(True)
        outer_id = next(contour.id for contour in window.job.drawing.contours
                        if contour.layer == "OUTER")
        inner_id = next(contour.id for contour in window.job.drawing.contours
                        if contour.layer == "STABILIZER")
        window.contours.setCurrentRow(0)
        window._assign("outside")
        window.contours.setCurrentRow(2)
        window._assign("inside")
        window.show()
        APPLICATION.processEvents()

        def centerline_point(identifier):
            hit = next(item for item in window.scene.items()
                       if item.data(2) == "cut-hit" and item.data(3) == identifier)
            path = hit.path()
            segments = [(path.elementAt(i), path.elementAt(i + 1))
                        for i in range(path.elementCount() - 1)]
            start, end = max(segments, key=lambda pair:
                             (pair[1].x - pair[0].x) ** 2 + (pair[1].y - pair[0].y) ** 2)
            return QPointF((start.x + end.x) / 2, (start.y + end.y) / 2)

        window._select_operation_by_id(outer_id)
        window.contours.setCurrentRow(0)  # A selected contour sits above previews.
        point = centerline_point(inner_id)
        self.assertEqual(window.scene.items(point)[0].data(3), inner_id)
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(point))
        APPLICATION.processEvents()
        self.assertEqual(window._current_operation().contour_id, inner_id)
        inner_contour = next(contour for contour in window.job.drawing.contours
                             if contour.id == inner_id)
        inner_number = window.job.drawing.contours.index(inner_contour) + 1
        self.assertEqual(window.cut_label.text(),
                         f"Inside · {inner_contour.layer} (contour {inner_number})")

        point = centerline_point(outer_id)
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(point))
        APPLICATION.processEvents()
        self.assertEqual(window._current_operation().contour_id, outer_id)
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_sharp_inside_corners_have_visible_dogbones(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "square.dxf"
            doc = ezdxf.new("R2010")
            doc.units = units.MM
            doc.modelspace().add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True)
            doc.saveas(source)
            window = MainWindow()
            window.open_dxf(str(source))
            for field, value in ((window.thickness, "6"), (window.safe_z, "5"),
                                 (window.tool_name, "flat"), (window.diameter, "1"),
                                 (window.feed, "600"), (window.plunge, "250"),
                                 (window.rpm, "18000"), (window.stepdown, "1.5")):
                field.setText(value)
            window.can_plunge.setChecked(True)
            window.contours.setCurrentRow(0)
            window._assign("inside")
            window.cut_dogbones.setChecked(True)
            self.assertEqual(window.dogbone_list.count(), 4)
            self.assertIn("Dogbones 4", window.operations.item(0).text())
            self.assertEqual(sum(item.data(2) == "dogbone" for item in window.scene.items()), 4)
            window.close()
            window.deleteLater()
            APPLICATION.processEvents()

    def test_tool_library_copies_preset_into_job(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("babycam.tool_library.library_path", return_value=Path(directory) / "tools.json"):
                window = MainWindow()
                for field, value in ((window.tool_name, "flat"), (window.diameter, "1"),
                                     (window.feed, "600"), (window.plunge, "250"),
                                     (window.rpm, "18000"), (window.stepdown, "1.5")):
                    field.setText(value)
                window.can_plunge.setChecked(True)
                manage = next(button for button in window.findChildren(QPushButton)
                              if button.text() == "Manage presets…")
                manage.menu().actions()[0].trigger()
                self.assertEqual(window.preset_picker.count(), 1)
                window.feed.setText("800")
                self.assertEqual(window.tool_presets[0].feed, 600)
                window._use_preset()
                self.assertEqual(window.job.tool.feed, 600)
                window.feed.setText("900")
                self.assertEqual(window.tool_presets[0].feed, 600)
                window.close()
                window.deleteLater()
                APPLICATION.processEvents()

    def test_default_switches_and_per_cut_edits(self):
        window = MainWindow()
        self.assertFalse(window.auto_spindle.isChecked())
        self.assertFalse(window.default_tabs.isChecked())
        self.assertFalse(window.default_dogbones.isChecked())
        window.open_dxf(str(FIXTURE))
        for field, value in ((window.thickness, "6"), (window.safe_z, "5"),
                             (window.tool_name, "1 mm flat"), (window.diameter, "1"),
                             (window.feed, "600"), (window.plunge, "250"),
                             (window.rpm, "18000"), (window.stepdown, "1.5")):
            field.setText(value)
        window.can_plunge.setChecked(True)
        window.default_tabs.setChecked(True)
        window.default_dogbones.setChecked(True)
        window.contours.setCurrentRow(0)
        window._assign("outside")
        self.assertEqual(window.operations.count(), 1)
        self.assertTrue(window.job.operations[0].tabs)
        self.assertTrue(window.job.operations[0].dogbones)
        self.assertIn("Dogbones 0", window.operations.item(0).text())
        self.assertEqual(window.dogbone_list.item(0).text(), "No sharp cutter-side corners found")
        initial_tabs = planned_path(window.job, window.job.operations[0], None).tabs
        crossbars = [item for item in window.scene.items() if item.data(2) == "tab-crossbar"]
        self.assertEqual(len(crossbars), len(initial_tabs))
        self.assertTrue(all(abs(item.line().length() - 1) < 1e-9 for item in crossbars))
        window.tab_manual_button.click()
        self.assertEqual(len(window.job.operations[0].tab_anchors), len(initial_tabs))
        window._stop_tab_placement()
        window.tab_clear_button.click()
        self.assertFalse(window.job.operations[0].tabs)
        self.assertFalse(any(item.data(2) == "tab-crossbar" for item in window.scene.items()))
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_manual_tab_placement_toggles_on_cut_and_exits_on_right_click(self):
        window = MainWindow()
        window.open_dxf(str(FIXTURE))
        for field, value in ((window.thickness, "6"), (window.safe_z, "5"),
                             (window.tool_name, "flat"), (window.diameter, "1"),
                             (window.feed, "600"), (window.plunge, "250"),
                             (window.rpm, "18000"), (window.stepdown, "1.5")):
            field.setText(value)
        window.can_plunge.setChecked(True)
        window.contours.setCurrentRow(0)
        window._assign("outside")
        window.depth_mode.setCurrentIndex(1)
        operation = window.job.operations[0]
        path = planned_path(window.job, operation, None)
        position = place_tabs(path.points, 6, 2, 4, 0.5)[0]
        point = point_at(path.points, lengths(path.points), position)
        window.show()
        APPLICATION.processEvents()

        window.tab_manual_button.click()
        self.assertEqual(window._tab_placement_id, operation.contour_id)
        self.assertFalse(window.tabs.isEnabled())
        self.assertFalse(window.contours.isEnabled())
        self.assertFalse(window.operations.isEnabled())
        self.assertFalse(window.export_button.isEnabled())
        self.assertFalse(window.export_action.isEnabled())
        self.assertFalse(window.open_action.isEnabled())
        self.assertTrue(window.canvas.mode_hint.isVisible())
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas.mapFromScene(QPointF(50, 50)))
        APPLICATION.processEvents()
        self.assertEqual(window._current_operation().contour_id, operation.contour_id)
        screen_point = window.canvas.mapFromScene(QPointF(*point))
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=screen_point)
        APPLICATION.processEvents()
        self.assertTrue(operation.tabs)
        self.assertEqual(len(planned_path(window.job, operation, None).tabs), 1)
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=screen_point)
        APPLICATION.processEvents()
        self.assertFalse(operation.tabs)
        self.assertEqual(operation.tab_anchors, ())

        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.RightButton, pos=screen_point)
        APPLICATION.processEvents()
        self.assertIsNone(window._tab_placement_id)
        self.assertTrue(window.tabs.isEnabled())
        self.assertTrue(window.contours.isEnabled())
        self.assertTrue(window.operations.isEnabled())
        self.assertTrue(window.open_action.isEnabled())
        self.assertFalse(window.canvas.mode_hint.isVisible())
        window.tab_auto_button.click()
        self.assertEqual(len(planned_path(window.job, operation, None).tabs), 4)
        window.tab_clear_button.click()
        self.assertFalse(operation.tabs)
        operation.tab_width = 100
        window.tab_auto_button.click()
        self.assertFalse(operation.tabs)
        self.assertIn("tab", window.tab_auto_button.toolTip().lower())
        window.tab_manual_button.click()
        QTest.keyClick(window, Qt.Key.Key_Escape)
        APPLICATION.processEvents()
        self.assertIsNone(window._tab_placement_id)
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_explicit_depth_unlocks_export(self):
        window = MainWindow()
        self.assertEqual(window.export_button.text(), "Export GCode")
        self.assertEqual(window.export_details_button.text(), "Status details")
        self.assertEqual(window.export_button.property("exportState"), "idle")
        self.assertFalse(window.export_button.isEnabled())
        self.assertEqual(window.export_info.text(), "Warnings: 0   Errors: 0")
        window.open_dxf(str(FIXTURE))
        self.assertEqual(window.contours.count(), 3)
        for field, value in (
            (window.thickness, "6"), (window.safe_z, "5"),
            (window.tool_name, "1 mm flat"), (window.diameter, "1"),
            (window.feed, "600"), (window.plunge, "250"),
            (window.rpm, "18000"), (window.stepdown, "1.5"),
        ):
            field.setText(value)
        window.can_plunge.setChecked(True)
        window.contours.setCurrentRow(0)
        window._assign("outside")
        self.assertEqual(window.operations.count(), 1)
        self.assertEqual(window.export_button.property("exportState"), "blocked")
        self.assertTrue(window.export_button.isEnabled())
        self.assertEqual(window.export_info.text(), "Warnings: 0   Errors: 1")
        sweep = next(item for item in window.scene.items() if item.data(2) == "cutter-sweep")
        marker = next(item for item in window.scene.items() if item.data(2) == "start-marker")
        self.assertEqual(sweep.pen().color().name(), "#e47d8f")
        self.assertEqual(marker.brush().color().name(), "#e47d8f")
        with patch.object(QMessageBox, "warning") as show_error:
            window.export_button.click()
            show_error.assert_called_once()
        self.assertIn("Depth not selected", window.operations.item(0).text())
        window.depth_mode.setCurrentIndex(1)
        self.assertEqual(window.export_button.property("exportState"), "warnings")
        self.assertTrue(window.export_action.isEnabled())
        marker = next(item for item in window.scene.items() if item.data(2) == "start-marker")
        sweep = next(item for item in window.scene.items() if item.data(2) == "cutter-sweep")
        self.assertEqual(sweep.pen().color().name(), "#42d2e8")
        self.assertEqual(marker.brush().color().name(), "#42d2e8")
        self.assertAlmostEqual(marker.rect().width(), 1)
        self.assertAlmostEqual(sweep.pen().widthF(), 1)
        self.assertFalse(sweep.pen().isCosmetic())
        window.diameter.setText("2")
        marker = next(item for item in window.scene.items() if item.data(2) == "start-marker")
        sweep = next(item for item in window.scene.items() if item.data(2) == "cutter-sweep")
        self.assertAlmostEqual(marker.rect().width(), 2)
        self.assertAlmostEqual(sweep.pen().widthF(), 2)
        self.assertEqual(window.export_info.text(), "Warnings: 2   Errors: 0")
        self.assertTrue(window.export_details_button.isEnabled())
        observed = []

        def inspect_details():
            dialog = APPLICATION.activeModalWidget()
            if isinstance(dialog, QDialog):
                observed.append(dialog.findChild(QPlainTextEdit).toPlainText())
                dialog.accept()

        QTimer.singleShot(0, inspect_details)
        window.export_details_button.click()
        self.assertEqual(len(observed), 1)
        self.assertIn("Warning: Usable cutting length", observed[0])
        self.assertIn("Warning: Cutter path extends into negative XY", observed[0])
        self.assertNotIn("Bounds", observed[0])
        self.assertNotIn("subclass records", observed[0])
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "profile.nc"
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
                window.export_button.click()
            self.assertIn("(BabyCAM profile job)", destination.read_text(encoding="utf-8"))
            self.assertEqual(window.export_info.text(), "Warnings: 2   Errors: 0")
            QTimer.singleShot(0, inspect_details)
            window.export_details_button.click()
            self.assertIn(f"Exported {destination}", observed[-1])
        window.cutting_length.setText("10")
        window.offset_x.setText("1")
        window.offset_y.setText("1")
        self.assertEqual(window.export_button.property("exportState"), "ready")
        self.assertEqual(window.export_info.text(), "Warnings: 0   Errors: 0")
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()


if __name__ == "__main__":
    unittest.main()
