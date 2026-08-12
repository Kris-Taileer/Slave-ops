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


class RuntimeSecretsTests(unittest.TestCase):
    def test_runtime_tokens_override_public_config(self):
        cfg = {
            "checksystem": {"token": "public", "team_token": "public-team"}
        }

        APPLY_CONFIG.apply_runtime_secrets(
            cfg,
            {
                "CHECKSYSTEM_TOKEN": "secret",
                "CHECKSYSTEM_TEAM_TOKEN": "secret-team",
            },
        )

        self.assertEqual(cfg["checksystem"]["token"], "secret")
        self.assertEqual(cfg["checksystem"]["team_token"], "secret-team")

    def test_rendered_config_does_not_contain_tokens(self):
        cfg = {
            "server": {},
            "teams": {"Team #1": "127.0.0.1"},
            "flags": {},
            "submitter": {},
            "checksystem": {
                "protocol": "ctfcup_tcp",
                "token": "must-not-be-rendered",
                "team_token": "also-secret",
            },
        }

        rendered = APPLY_CONFIG.render_s4d_config(cfg)

        self.assertNotIn("must-not-be-rendered", rendered)
        self.assertNotIn("also-secret", rendered)
        self.assertIn("os.getenv('CHECKSYSTEM_TOKEN')", rendered)
        self.assertIn("os.getenv('CHECKSYSTEM_TEAM_TOKEN')", rendered)


if __name__ == "__main__":
    unittest.main()
