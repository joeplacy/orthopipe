import unittest

from rx_parser import parse_prescription


class RxParserTests(unittest.TestCase):
    def test_unknown_backend_fails_before_network_access(self):
        with self.assertRaisesRegex(ValueError, "unknown ORTHOPIPE_RX_BACKEND"):
            parse_prescription("4 mm heel lift left", backend="not-a-backend")


if __name__ == "__main__":
    unittest.main()
