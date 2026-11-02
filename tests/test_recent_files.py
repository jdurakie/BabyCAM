from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from babycam.recent_files import (
    MAX_RECENT_FILES, add_recent_file, load_recent_files, save_recent_files,
)


class RecentFilesTests(unittest.TestCase):
    def test_newest_first_deduplicated_and_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [str(Path(directory) / f"drawing-{index}.dxf")
                     for index in range(MAX_RECENT_FILES + 5)]
            recent = []
            for path in paths:
                recent = add_recent_file(recent, path)
            recent = add_recent_file(recent, paths[10])
            self.assertEqual(len(recent), MAX_RECENT_FILES)
            self.assertEqual(recent[0], paths[10])
            self.assertEqual(recent.count(paths[10]), 1)
            self.assertNotIn(paths[0], recent)

            config = Path(directory) / "recent_files.json"
            save_recent_files(recent, config)
            self.assertEqual(load_recent_files(config), recent)

    def test_load_ignores_invalid_entries_and_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "recent_files.json"
            drawing = Path(directory) / "drawing.dxf"
            config.write_text(json.dumps([str(drawing), 42, "", str(drawing.parent / "." / drawing.name)]),
                              encoding="utf-8")
            self.assertEqual(load_recent_files(config), [str(drawing)])


if __name__ == "__main__":
    unittest.main()
