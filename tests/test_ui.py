from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from babycam.app import MainWindow  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "keyboard_plate.dxf"
APPLICATION = QApplication.instance() or QApplication([])


class UIWorkflowTests(unittest.TestCase):
    def test_tool_library_copies_preset_into_job(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("babycam.tool_library.library_path", return_value=Path(directory) / "tools.json"):
                window = MainWindow()
                for field, value in ((window.tool_name, "flat"), (window.diameter, "1"),
                                     (window.feed, "600"), (window.plunge, "250"),
                                     (window.rpm, "18000"), (window.stepdown, "1.5")):
                    field.setText(value)
                window.can_plunge.setChecked(True)
                window._save_preset()
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
        window.close()
        window.deleteLater()
        APPLICATION.processEvents()


if __name__ == "__main__":
    unittest.main()
