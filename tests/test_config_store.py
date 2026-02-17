import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from acrfetcher.config_store import cfg_to_dict, load_config, migrate_legacy_keys, save_config


class ConfigStoreTests(unittest.TestCase):
    def test_migrate_legacy(self):
        raw = {"push_enabled": True, "push_bot_token": "x"}
        out, changed = migrate_legacy_keys(raw)
        self.assertTrue(changed)
        self.assertNotIn("push_enabled", out)
        self.assertTrue(out.get("webhook_enabled"))
        self.assertEqual(out.get("webhook_bot_token"), "x")

    def test_load_save_roundtrip(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            cfg = load_config(p)
            cfg.channel = "@test"
            cfg.gotem = 12
            save_config(p, cfg)
            cfg2 = load_config(p)
            self.assertEqual(cfg2.channel, "@test")
            self.assertEqual(cfg2.gotem, 12)
            d = cfg_to_dict(cfg2)
            self.assertEqual(d["channel"], "@test")
            self.assertEqual(d["gotem"], 12)

    def test_preserve_unknown_keys(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text(json.dumps({"api_id": 1, "api_hash": "a", "x_unknown": 7}), encoding="utf-8")
            cfg = load_config(p)
            save_config(p, cfg)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data.get("x_unknown"), 7)


if __name__ == "__main__":
    unittest.main()
