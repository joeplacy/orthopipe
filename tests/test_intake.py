import unittest

import numpy as np
import trimesh

from intake import check_and_fix_scale


class IntakeTests(unittest.TestCase):
    def test_meter_scale_is_converted_to_mm(self):
        mesh = trimesh.creation.box(extents=(0.08, 0.25, 0.05))
        corrected, warnings = check_and_fix_scale(mesh)
        np.testing.assert_allclose(corrected.extents, [80, 250, 50])
        self.assertTrue(any("auto-scaled" in warning for warning in warnings))

    def test_plausible_mm_scale_is_unchanged(self):
        mesh = trimesh.creation.box(extents=(80, 250, 50))
        corrected, warnings = check_and_fix_scale(mesh)
        np.testing.assert_allclose(corrected.extents, [80, 250, 50])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
