import json
import tempfile
import unittest
from pathlib import Path

import trimesh

from print_prep import PrintPrepProfile, prepare_pair


class PrintPrepTests(unittest.TestCase):
    def test_pair_is_vertical_separate_and_manifested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left_path, right_path = root / "left.stl", root / "right.stl"
            # Canonical source: +Y is the long heel-to-toe axis.
            trimesh.creation.box(extents=(20, 100, 5)).export(left_path)
            trimesh.creation.box(extents=(20, 100, 5)).export(right_path)

            output = root / "pair.stl"
            result = prepare_pair(left_path, right_path, output, "TEST-PAIR")

            self.assertTrue(result["pass"])
            self.assertEqual(result["output"]["component_count"], 2)
            self.assertAlmostEqual(result["placement"]["actual_pair_gap_mm"], 5.0, places=3)
            self.assertAlmostEqual(result["placement"]["minimum_z_mm"], -0.3, places=3)
            self.assertAlmostEqual(result["output"]["extents_mm"][2], 100.0, places=3)
            self.assertEqual(result["workflow_checkpoint"]["current"], "print_layout_ready")
            self.assertTrue(result["workflow_checkpoint"]["gcode_release_blocked"])
            self.assertTrue(output.exists())
            self.assertTrue(output.with_suffix(".preview.png").exists())
            self.assertFalse(result["bed_fit_verified"])
            self.assertIsNone(result["checks"]["bed_fit"])
            saved = json.loads(output.with_suffix(".manifest.json").read_text())
            self.assertEqual(saved["output"]["sha256"], result["output"]["sha256"])

    def test_profile_rejects_touching_pair(self):
        with self.assertRaisesRegex(ValueError, "never touch"):
            PrintPrepProfile(pair_gap_mm=0)

    def test_order_id_rejects_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            mesh_path = Path(tmp) / "foot.stl"
            trimesh.creation.box().export(mesh_path)
            with self.assertRaisesRegex(ValueError, "order_id"):
                prepare_pair(mesh_path, mesh_path, Path(tmp) / "pair.stl", "../../escape")


if __name__ == "__main__":
    unittest.main()
