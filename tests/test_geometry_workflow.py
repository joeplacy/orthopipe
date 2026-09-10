import unittest

import numpy as np
import trimesh

from geometry import bed_plane_contact_metrics


class GeometryWorkflowTests(unittest.TestCase):
    def test_bed_contact_compares_heel_and_metatarsal_regions(self):
        points = np.array([
            [-1, 5, 0.0], [1, 10, 0.0], [0, 15, 0.0],
            [-1, 65, 2.0], [1, 70, 2.0], [0, 75, 2.0],
            [0, 100, 5.0],
        ])
        mesh = trimesh.Trimesh(vertices=points, faces=[], process=False)
        result = bed_plane_contact_metrics(mesh)
        self.assertAlmostEqual(result["delta_mm"], 2.0)
        self.assertFalse(result["review_recommended"])


if __name__ == "__main__":
    unittest.main()
