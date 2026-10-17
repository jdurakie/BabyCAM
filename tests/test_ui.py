from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from babycam.app import MainWindow  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "keyboard_plate.dxf"
APPLICATION = QApplication.instance() or QApplication([])


class UIWorkflowTests(unittest.TestCase):
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
