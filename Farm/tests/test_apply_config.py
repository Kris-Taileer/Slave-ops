import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "apply_config", ROOT / "scripts" / "apply_config.py"
)
APPLY_CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APPLY_CONFIG)


class NeoClientFarmURLTests(unittest.TestCase):
    def test_defaults_to_loopback_independently_of_public_ip(self):
        cfg = {
            "server": {"public_ip": "10.80.3.2"},
            "ports": {"s4dfarm": 5137, "cacheproxy": 8888},
            "neo": {},
        }

        rendered = APPLY_CONFIG.render_neo_server_config(cfg)

        self.assertIn('client_farm_url: "http://127.0.0.1:5137"', rendered)
        self.assertIn("S4DFARM_URL: 'http://127.0.0.1:5137'", rendered)

    def test_allows_an_explicit_remote_worker_url(self):
        cfg = {
            "server": {"public_ip": "10.80.3.2"},
            "ports": {"s4dfarm": 5137, "cacheproxy": 8888},
            "neo": {"client_farm_url": "http://farm.example:5137/"},
        }

        self.assertEqual(
            APPLY_CONFIG.neo_client_farm_url(cfg), "http://farm.example:5137"
        )


if __name__ == "__main__":
    unittest.main()
