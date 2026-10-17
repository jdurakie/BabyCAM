from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf
from ezdxf import units

from babycam.cam import CAMError, grbl_gcode, plan_program, profile_path
from babycam.dxf_import import UnitSelectionRequired, load_dxf
from babycam.model import Job, Operation, Setup, Tool


FIXTURE = Path(__file__).parent / "fixtures" / "keyboard_plate.dxf"


def keyboard_job() -> Job:
    drawing = load_dxf(FIXTURE)
    job = Job(
        drawing=drawing,
        tool=Tool(3, "1 mm flat", 1.0, 600, 250, 18000, 1.5, True, 8),
        setup=Setup(thickness=6, safe_z=5),
    )
    job.operations = [Operation(contour.id, "outside" if contour.layer == "OUTER" else "inside", "through")
                      for contour in drawing.contours]
    return job


class ImportTests(unittest.TestCase):
    def test_keyboard_minimal_dxf_has_three_valid_closed_contours(self):
        drawing = load_dxf(FIXTURE)
        self.assertEqual(drawing.bounds, (0, 0, 34, 21))
        self.assertEqual({c.layer for c in drawing.contours}, {"OUTER", "SWITCH", "STABILIZER"})
        self.assertTrue(all(c.error is None and c.points[0] == c.points[-1] for c in drawing.contours))
        self.assertEqual(drawing.references, [])

    def test_joined_lines_and_unitless_dxf(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "segments.dxf"
            doc = ezdxf.new("R2010")
            doc.units = units.MM
            msp = doc.modelspace()
            for start, end in [((0, 0), (10, 0)), ((10, 0), (10, 5)),
                               ((10, 5), (0, 5)), ((0, 5), (0, 0))]:
                msp.add_line(start, end, dxfattribs={"layer": "CUT"})
            doc.saveas(source)
            drawing = load_dxf(source)
            self.assertEqual(len(drawing.contours), 1)
            self.assertEqual(drawing.bounds, (0, 0, 10, 5))
            self.assertEqual(drawing.references, [])
            doc.units = 0
            doc.saveas(source)
            with self.assertRaises(UnitSelectionRequired):
                load_dxf(source)
            inch_drawing = load_dxf(source, units.IN)
            self.assertEqual(len(inch_drawing.contours), 1)
            for actual, expected in zip(inch_drawing.bounds, (0, 0, 254, 127)):
                self.assertAlmostEqual(actual, expected, places=5)

    def test_circle_and_bulge_are_flattened_into_closed_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "curves.dxf"
            doc = ezdxf.new("R2010")
            doc.units = units.MM
            doc.modelspace().add_circle((10, 10), 5)
            doc.modelspace().add_lwpolyline(
                [(0, 0, 0.5), (4, 0, 0), (4, 4, 0), (0, 4, 0)],
                format="xyb", close=True)
            doc.saveas(source)
            drawing = load_dxf(source)
            self.assertEqual(len(drawing.contours), 2)
            self.assertTrue(all(len(c.points) > 10 and c.error is None for c in drawing.contours))

    def test_arc_and_line_join_into_closed_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "arc.dxf"
            doc = ezdxf.new("R2010")
            doc.units = units.MM
            doc.modelspace().add_arc((0, 0), 5, 0, 180)
            doc.modelspace().add_line((-5, 0), (5, 0))
            doc.saveas(source)
            drawing = load_dxf(source)
            self.assertEqual(len(drawing.contours), 1)
            self.assertEqual(drawing.references, [])
            self.assertIsNone(drawing.contours[0].error)


class ProgramTests(unittest.TestCase):
    def test_keyboard_profiles_depths_safe_rapids_and_parking(self):
        job = keyboard_job()
        program = plan_program(job)
        self.assertEqual([path.operation.side for path in program.paths],
                         ["inside", "inside", "outside"])
        self.assertEqual(program.bounds, (-0.5, 34.5, -0.5, 21.5, -6.1, 5))
        self.assertEqual(program.moves[-1].y, max(y for path in program.paths for _, y in path.points))
        self.assertIsNone(program.moves[-1].x)  # Last X is retained.
        current_z = None
        for move in program.moves:
            if move.kind == "G0" and (move.x is not None or move.y is not None):
                self.assertEqual(current_z, 5)
            if move.z is not None:
                current_z = move.z
        lines = grbl_gcode(job, program).splitlines()
        self.assertLess(lines.index("G0 Z5"), lines.index("M3 S18000"))
        self.assertEqual(lines[-3:], ["M5", "G0 Y21.5", "M2"])
        self.assertIn("Tool T3 1 mm flat diameter 1 mm", lines[1])

    def test_missing_depth_and_short_cutting_length_block_export(self):
        job = keyboard_job()
        job.operations[0].depth_mode = None
        with self.assertRaisesRegex(CAMError, "Select Through"):
            plan_program(job)
        job.operations[0].depth_mode = "through"
        job.tool.cutting_length = 6
        with self.assertRaisesRegex(CAMError, "usable cutting length"):
            plan_program(job)

    def test_impossible_inside_offset_is_rejected(self):
        job = keyboard_job()
        job.tool.diameter = 3.175
        stabilizer = next(op for op in job.operations if
                          next(c.layer for c in job.drawing.contours if c.id == op.contour_id) == "STABILIZER")
        with self.assertRaisesRegex(CAMError, "cannot fit"):
            profile_path(job, stabilizer)

    def test_negative_xy_and_missing_cutting_length_only_warn(self):
        job = keyboard_job()
        job.tool.cutting_length = None
        program = plan_program(job)
        self.assertTrue(any("negative XY" in warning for warning in program.warnings))
        self.assertTrue(any("Usable cutting length" in warning for warning in program.warnings))

    def test_to_depth_uses_selected_depth_without_through_allowance(self):
        job = keyboard_job()
        job.operations = [job.operations[0]]
        job.operations[0].depth_mode = "depth"
        job.operations[0].depth = 2.5
        program = plan_program(job)
        self.assertEqual(program.bounds[4], -2.5)
        self.assertEqual([move.z for move in program.moves if move.kind == "G1" and move.z is not None],
                         [-1.5, -2.5])

    def test_manual_spindle_mode_omits_spindle_commands(self):
        job = keyboard_job()
        job.setup.automatic_spindle = False
        gcode = grbl_gcode(job, plan_program(job))
        self.assertIn("Start spindle manually before cutting", gcode)
        self.assertNotIn("M3", gcode)
        self.assertNotIn("M5", gcode)


if __name__ == "__main__":
    unittest.main()
