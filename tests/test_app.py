import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from pydantic import ValidationError

import app as app_module


class AppTests(unittest.TestCase):
    def test_health(self):
        self.assertEqual(app_module.health()["status"], "ok")

    def test_replay_order_id_rejects_glob_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            replay_dir = Path(tmp)
            (replay_dir / "SAFE_left_scorecard.json").write_text("{}")
            with mock.patch.object(app_module, "REPLAY_DIR", replay_dir):
                self.assertIsNotNone(app_module._find_scorecard("SAFE"))
                self.assertIsNone(app_module._find_scorecard("*"))

    def test_generate_order_id_rejects_path_characters(self):
        with self.assertRaises(ValidationError):
            app_module.GenerateRequest(scan_id="abc", side="left", order_id="../../escape",
                                       rx={"mods": []})

    def test_heatmap_filename_cannot_escape_replay_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            replay_dir = Path(tmp)
            card = replay_dir / "SAFE_left_scorecard.json"
            card.write_text(json.dumps({"heatmap_png": "../../outside.png"}))
            with mock.patch.object(app_module, "REPLAY_DIR", replay_dir):
                with self.assertRaises(HTTPException) as caught:
                    app_module.get_replay_heatmap("SAFE")
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
