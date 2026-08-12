import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "configure", ROOT / "scripts" / "configure.py"
)
CONFIGURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIGURE)


class ConfigSerializationTests(unittest.TestCase):
    def test_round_trip_keeps_nested_values_and_team_names(self):
        cfg = {
            "project": "farm",
            "server": {"public_ip": "10.80.2.3", "timezone": "Europe/Moscow"},
            "teams": {"Team #1": "10.80.1.1"},
            "flags": {"format": r"[A-Z0-9]{31}=", "lifetime": 300},
            "neo": {"build_client_image": True},
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attdef.yml"
            path.write_text(CONFIGURE.dump_simple_yaml(cfg) + "\n")
            parsed = CONFIGURE.parse_simple_yaml(path)

        self.assertEqual(parsed, cfg)

    def test_host_accepts_ip_and_domain(self):
        self.assertEqual(CONFIGURE.host("10.80.2.3"), "10.80.2.3")
        self.assertEqual(CONFIGURE.host("farm.example.org"), "farm.example.org")

    def test_host_rejects_url(self):
        with self.assertRaises(ValueError):
            CONFIGURE.host("http://10.80.2.3")

    def test_project_name_rejects_compose_incompatible_value(self):
        with self.assertRaises(ValueError):
            CONFIGURE.project_name("Farm Main")


if __name__ == "__main__":
    unittest.main()
