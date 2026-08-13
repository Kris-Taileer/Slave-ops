"""Unit tests for pipeline.store — pure DAG logic and persistence.

Run:  python3 -m unittest discover -s tests
  or: python3 -m pytest tests/
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline import store  # noqa: E402
from pipeline.store import (  # noqa: E402
    Store,
    CycleError,
    ValidationError,
    new_block,
    slugify,
    topo_sort,
    topo_levels,
    dependents,
    descendants,
)


def _graph(*edges):
    """Build a blocks dict from ('a', ['deps']) tuples."""
    blocks = {}
    for bid, deps in edges:
        blocks[bid] = new_block(bid, bid, depends_on=list(deps))
    return blocks


class SlugTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Fetch Flags"), "fetch-flags")
        self.assertEqual(slugify("  Build #2  "), "build-2")
        self.assertEqual(slugify("привет"), "block")  # non-ascii -> fallback
        self.assertEqual(slugify(""), "block")
        # result always matches the id charset backend expects
        self.assertRegex(slugify("A/B\\C:D"), r"^[A-Za-z0-9_.-]+$")


class NewBlockTests(unittest.TestCase):
    def test_defaults(self):
        b = new_block("x", "X")
        self.assertEqual(b["type"], "python")
        self.assertEqual(b["mode"], "task")
        self.assertEqual(b["status"], "idle")
        self.assertEqual(b["depends_on"], [])

    def test_normalization(self):
        b = new_block(
            "x", "X",
            type="bogus", mode="weird", args="one two three",
            timeout="45", port="", depends_on=["x", "y", "y"], venv=1,
        )
        self.assertEqual(b["type"], "python")   # bad type -> default
        self.assertEqual(b["mode"], "task")     # bad mode -> default
        self.assertEqual(b["args"], ["one", "two", "three"])  # str split
        self.assertEqual(b["timeout"], 45)      # str -> int
        self.assertIsNone(b["port"])            # "" -> None
        self.assertEqual(b["depends_on"], ["y"])  # self + dupes dropped
        self.assertIs(b["venv"], True)


class TopoTests(unittest.TestCase):
    def test_order(self):
        g = _graph(("a", []), ("b", ["a"]), ("c", ["b"]))
        self.assertEqual(topo_sort(g), ["a", "b", "c"])

    def test_diamond_levels(self):
        # a -> b, a -> c, b&c -> d
        g = _graph(("a", []), ("b", ["a"]), ("c", ["a"]), ("d", ["b", "c"]))
        lv = topo_levels(g)
        self.assertEqual(lv["a"], 0)
        self.assertEqual(lv["b"], 1)
        self.assertEqual(lv["c"], 1)
        self.assertEqual(lv["d"], 2)

    def test_cycle_detected(self):
        g = _graph(("a", ["c"]), ("b", ["a"]), ("c", ["b"]))
        with self.assertRaises(CycleError):
            topo_sort(g)

    def test_independent_roots(self):
        g = _graph(("a", []), ("b", []), ("c", []))
        self.assertEqual(set(topo_sort(g)), {"a", "b", "c"})
        self.assertEqual(topo_levels(g), {"a": 0, "b": 0, "c": 0})

    def test_dependents_and_descendants(self):
        g = _graph(("a", []), ("b", ["a"]), ("c", ["b"]), ("x", []))
        self.assertEqual(dependents(g, "a"), ["b"])
        self.assertEqual(set(descendants(g, "a")), {"b", "c"})
        self.assertEqual(descendants(g, "x"), [])


class ValidateTests(unittest.TestCase):
    def test_unknown_dependency(self):
        g = _graph(("a", ["ghost"]))
        with self.assertRaises(ValidationError):
            store.validate_graph(g)

    def test_self_dependency_is_stripped_then_ok(self):
        # _normalize strips self-deps, so the graph validates clean
        b = new_block("a", "a", depends_on=["a"])
        store.validate_graph({"a": b})
        self.assertEqual(b["depends_on"], [])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_writes_script_and_persists(self):
        b = self.store.create_block("Fetch Flags", type="sh")
        self.assertEqual(b["id"], "fetch-flags")
        self.assertTrue(os.path.exists(self.store.script_path(b)))
        # a fresh Store on the same root sees it
        again = Store(self.tmp).get_block("fetch-flags")
        self.assertIsNotNone(again)
        self.assertEqual(again["type"], "sh")

    def test_unique_ids(self):
        a = self.store.create_block("build")
        b = self.store.create_block("build")
        self.assertEqual(a["id"], "build")
        self.assertEqual(b["id"], "build-2")

    def test_update_config_and_script(self):
        b = self.store.create_block("proc")
        self.store.update_block(b["id"], {"timeout": 5, "mode": "service", "port": 8080})
        got = self.store.get_block(b["id"])
        self.assertEqual(got["timeout"], 5)
        self.assertEqual(got["mode"], "service")
        self.assertEqual(got["port"], 8080)
        self.store.update_block(b["id"], script="print('changed')")
        self.assertIn("changed", self.store.read_script(got))

    def test_type_change_renames_script(self):
        b = self.store.create_block("conv", type="python")
        py = self.store.script_path(b)
        self.assertTrue(py.endswith("script.py"))
        self.store.update_block(b["id"], {"type": "sh"})
        got = self.store.get_block(b["id"])
        sh = self.store.script_path(got)
        self.assertTrue(sh.endswith("script.sh"))
        self.assertTrue(os.path.exists(sh))
        self.assertFalse(os.path.exists(py))

    def test_reject_unknown_dependency_on_update(self):
        b = self.store.create_block("dep")
        with self.assertRaises(ValidationError):
            self.store.update_block(b["id"], {"depends_on": ["nope"]})

    def test_reject_cycle_on_update(self):
        a = self.store.create_block("a")
        c = self.store.create_block("c")
        self.store.update_block(c["id"], {"depends_on": [a["id"]]})
        with self.assertRaises(CycleError):
            self.store.update_block(a["id"], {"depends_on": [c["id"]]})

    def test_set_status(self):
        b = self.store.create_block("run")
        self.store.set_status(b["id"], "running", pid=1234)
        got = self.store.get_block(b["id"])
        self.assertEqual(got["status"], "running")
        self.assertEqual(got["pid"], 1234)
        with self.assertRaises(ValidationError):
            self.store.set_status(b["id"], "bogus")

    def test_position_fields(self):
        b = self.store.create_block("pos")
        self.assertIsNone(b["x"])
        self.assertIsNone(b["y"])
        self.store.update_block(b["id"], {"x": 120.5, "y": 40})
        got = self.store.get_block(b["id"])
        self.assertEqual(got["x"], 120.5)
        self.assertEqual(got["y"], 40)
        # blank clears back to None
        self.store.update_block(b["id"], {"x": "", "y": None})
        got = self.store.get_block(b["id"])
        self.assertIsNone(got["x"])
        self.assertIsNone(got["y"])

    def test_delete_detaches_dependents(self):
        a = self.store.create_block("a")
        b = self.store.create_block("b")
        self.store.update_block(b["id"], {"depends_on": [a["id"]]})
        self.assertTrue(self.store.delete_block(a["id"]))
        got_b = self.store.get_block(b["id"])
        self.assertEqual(got_b["depends_on"], [])  # edge removed
        self.assertFalse(os.path.exists(self.store.block_dir(a["id"])))


if __name__ == "__main__":
    unittest.main()
