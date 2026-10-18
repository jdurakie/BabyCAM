from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ezdxf
from ezdxf import units

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from babycam.app import MainWindow  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "keyboard_plate.dxf"
APPLICATION = QApplication.instance() or QApplication([])


class UIWorkflowTests(unittest.TestCase):
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
        self.assertTrue(window.message.font().bold())
        self.assertTrue(window.message.text().startswith("Error:"))
        self.assertFalse(window.message_icon.isHidden())
        window.open_dxf(str(FIXTURE))
        contour_id = window.contours.item(0).data(Qt.ItemDataRole.UserRole)
        path_item = next(item for item in window.scene.items() if item.data(0) == contour_id)
        normal_width = path_item.pen().widthF()
        window.contours.setCurrentRow(0)
        self.assertGreater(path_item.pen().widthF(), normal_width)
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
        window.cut_tabs.setChecked(False)
        self.assertFalse(window.job.operations[0].tabs)
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()

    def test_explicit_depth_unlocks_export(self):
        window = MainWindow()
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
        self.assertFalse(window.export_action.isEnabled())
        self.assertIn("Depth not selected", window.operations.item(0).text())
        window.depth_mode.setCurrentIndex(1)
        self.assertTrue(window.export_action.isEnabled())
        summary_lines = window.message.text().splitlines()
        self.assertTrue(summary_lines[0].startswith("Ready | Bounds (mm):"))
        self.assertTrue(any(line.startswith("DXF notice:") for line in summary_lines[1:]))
        self.assertEqual(sum(line.startswith("Warning:") for line in summary_lines), 2)
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()


if __name__ == "__main__":
    unittest.main()
