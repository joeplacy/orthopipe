import unittest

from pydantic import ValidationError

from schema import FootRx, HeelLift, MedialWedge, Prescription, Shell


class SchemaTests(unittest.TestCase):
    def test_mutable_defaults_are_isolated(self):
        first = FootRx()
        second = FootRx()
        first.mods.append(MedialWedge())
        self.assertEqual(second.mods, [])

        p1 = Prescription(order_id="ONE")
        p2 = Prescription(order_id="TWO")
        p1.ambiguities.append("review")
        self.assertEqual(p2.ambiguities, [])

    def test_clinical_ranges_are_enforced(self):
        with self.assertRaises(ValidationError):
            HeelLift(height_mm=16)
        with self.assertRaises(ValidationError):
            Shell(thickness_mm=1.9)

    def test_prescription_round_trip(self):
        rx = Prescription(
            order_id="TEST-001",
            left=FootRx(mods=[HeelLift(height_mm=6)]),
        )
        restored = Prescription.model_validate_json(rx.model_dump_json())
        self.assertEqual(restored, rx)


if __name__ == "__main__":
    unittest.main()
