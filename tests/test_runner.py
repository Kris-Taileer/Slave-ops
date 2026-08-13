"""Integration tests for pipeline.runner — real subprocesses in a temp dir.

Covers the semantics the user asked for: connected chains gate each other,
unconnected blocks run independently, task vs service, timeout/hang, venv,
argv data-passing, stop, and the fix-and-rerun resume flow.

Run:  python3 -m unittest tests.test_runner
"""

import os
import socket
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.runner import Runner  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.r = Runner(self.tmp)

    def tearDown(self):
        # make sure nothing is left running
        for bid in list(self.r._procs):
            self.r.stop_block(bid)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_status(self, block_id, target, timeout=20):
        targets = {target} if isinstance(target, str) else set(target)
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = self.r.store.get_block(block_id)
            if b and b["status"] in targets:
                return b["status"]
            time.sleep(0.05)
        actual = self.r.store.get_block(block_id)
        self.fail(f"{block_id} did not reach {targets}; stuck at "
                  f"{actual['status'] if actual else None}")

    def output(self, block_id):
        return self.r.output(block_id)["data"]


class TaskTests(RunnerTestCase):
    def test_task_success(self):
        b = self.r.store.create_block("ok", script="print('done')\n")
        self.r.run_block(b["id"])
        self.wait_status(b["id"], "success")
        self.assertIn("done", self.output(b["id"]))
        self.assertEqual(self.r.store.get_block(b["id"])["exit_code"], 0)

    def test_task_nonzero_is_error(self):
        b = self.r.store.create_block("boom", script="import sys\nsys.exit(3)\n")
        self.r.run_block(b["id"])
        self.wait_status(b["id"], "error")
        self.assertEqual(self.r.store.get_block(b["id"])["exit_code"], 3)

    def test_timeout_hangs_and_is_killed(self):
        b = self.r.store.create_block(
            "slow", script="import time\ntime.sleep(30)\n", timeout=1
        )
        self.r.run_block(b["id"])
        self.wait_status(b["id"], "hanging", timeout=15)
        self.assertNotIn(b["id"], self.r._procs)  # process reaped

    def test_sh_block_uses_posix_sh(self):
        # $0 works in sh; the script prints via echo
        b = self.r.store.create_block(
            "shell", type="sh", script="#!/bin/sh\necho shell-ran\n"
        )
        self.r.run_block(b["id"])
        self.wait_status(b["id"], "success")
        self.assertIn("shell-ran", self.output(b["id"]))


class VenvTests(RunnerTestCase):
    def test_python_block_in_own_venv(self):
        b = self.r.store.create_block(
            "isolated", venv=True,
            script="import sys\nprint(sys.prefix)\n",
        )
        self.r.run_block(b["id"])
        self.wait_status(b["id"], "success", timeout=60)  # venv build can be slow
        out = self.output(b["id"])
        self.assertIn(".venv", out)  # ran inside blocks/<id>/.venv
        self.assertTrue(os.path.isdir(self.r.store.venv_dir(b["id"])))


class ServiceTests(RunnerTestCase):
    def test_service_holds_port_then_stops(self):
        port = free_port()
        script = (
            "import socket\n"
            "s = socket.socket()\n"
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"s.bind(('127.0.0.1', {port}))\n"
            "s.listen()\n"
            "print('listening', flush=True)\n"
            "while True:\n"
            "    conn, _ = s.accept()\n"
            "    conn.close()\n"
        )
        b = self.r.store.create_block(
            "svc", mode="service", port=port, start_period=1, script=script
        )
        self.r.run_block(b["id"])
        self.wait_status(b["id"], "success", timeout=15)  # "up"
        self.assertTrue(port_open(port))
        self.r.stop_block(b["id"])
        self.wait_status(b["id"], "stopped")
        deadline = time.time() + 5
        while port_open(port) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(port_open(port))


class GraphTests(RunnerTestCase):
    def _chain(self, a_script, b_script, pass_stdout=False):
        a = self.r.store.create_block("a", script=a_script)
        b = self.r.store.create_block("b", script=b_script)
        self.r.store.update_block(
            b["id"], {"depends_on": [a["id"]], "pass_stdout": pass_stdout}
        )
        return a["id"], b["id"]

    def test_connected_chain_blocks_on_failure(self):
        a, b = self._chain("import sys\nsys.exit(1)\n", "print('should not run')\n")
        self.r.run_pipeline()
        self.wait_status(a, "error")
        self.wait_status(b, "blocked")
        self.assertNotIn("should not run", self.output(b))

    def test_connected_chain_runs_in_order(self):
        a, b = self._chain("print('first')\n", "print('second')\n")
        self.r.run_pipeline()
        self.wait_status(b, "success")
        self.assertEqual(self.r.store.get_block(a)["status"], "success")

    def test_unconnected_blocks_are_independent(self):
        # a fails; b has no edge to a and must still run
        a = self.r.store.create_block("fail", script="import sys\nsys.exit(1)\n")
        b = self.r.store.create_block("indep", script="print('independent')\n")
        self.r.run_pipeline()
        self.wait_status(a["id"], "error")
        self.wait_status(b["id"], "success")
        self.assertIn("independent", self.output(b["id"]))

    def test_pass_stdout_into_argv(self):
        a, b = self._chain(
            "print('PAYLOAD-42')\n",
            "import sys\nprint('GOT=' + (sys.argv[1] if len(sys.argv) > 1 else 'NONE'))\n",
            pass_stdout=True,
        )
        self.r.run_pipeline()
        self.wait_status(b, "success")
        self.assertIn("GOT=PAYLOAD-42", self.output(b))

    def test_single_run_auto_chains_idle_dependents(self):
        # a -> b -> c, all idle; running ONLY a should flow down the whole chain
        a, b = self._chain("print('a')\n", "print('b')\n")
        c = self.r.store.create_block("c", script="print('c ran')\n")
        self.r.store.update_block(c["id"], {"depends_on": [b]})
        self.r.run_block(a)
        self.wait_status(c["id"], "success", 15)
        self.assertEqual(self.r.store.get_block(a)["status"], "success")
        self.assertEqual(self.r.store.get_block(b)["status"], "success")
        self.assertIn("c ran", self.output(c["id"]))

    def test_pipeline_runs_all_blocks_sequentially(self):
        # three independent roots — the sequential worker must start every one
        ids = [self.r.store.create_block("n%d" % i, script="print('ok')\n")["id"]
               for i in range(3)]
        self.r.run_pipeline()
        for i in ids:
            self.wait_status(i, "success", 20)

    def test_fix_and_rerun_resumes_chain(self):
        a, b = self._chain("import sys\nsys.exit(1)\n", "print('downstream ran')\n")
        self.r.run_pipeline()
        self.wait_status(a, "error")
        self.wait_status(b, "blocked")
        # operator fixes the upstream script and re-runs just that block
        self.r.store.update_block(a, script="print('fixed now')\n")
        self.r.run_block(a)
        self.wait_status(a, "success")
        self.wait_status(b, "success")  # cascade resumed the blocked dependent
        self.assertIn("downstream ran", self.output(b))


if __name__ == "__main__":
    unittest.main()
