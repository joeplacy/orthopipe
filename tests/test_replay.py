import json
import tempfile
import unittest
from pathlib import Path

from replay import _scrub_pii, load_order


class ReplayTests(unittest.TestCase):
    def test_pii_scrubber_is_recursive(self):
        cleaned, found = _scrub_pii({
            "order_id": "SAFE-001",
            "patient": {"name": "Do not retain", "mrn": "123"},
            "mods": [{"type": "relief", "email": "private@example.com"}],
        })
        serialized = json.dumps(cleaned).lower()
        self.assertNotIn("patient", serialized)
        self.assertNotIn("private@example.com", serialized)
        self.assertEqual(found, ["email", "mrn", "name", "patient"])

    def test_order_requires_an_explicit_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "order.json").write_text(json.dumps({"order_id": "SAFE-001"}))
            (root / "scan.obj").touch()
            (root / "reference.stl").touch()
            with self.assertRaisesRegex(ValueError, "explicit side"):
                load_order(root)


if __name__ == "__main__":
    unittest.main()
