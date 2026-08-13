"""Execution engine for script blocks.

Stateful, lives inside the long-running ``backend.py`` process (which runs
inside the single container). Responsibilities:

- launch a block as a subprocess: python (optionally in its own ``.venv``) or
  POSIX ``sh`` (dash — *not* bash);
- pass data downstream through argv (upstream stdout appended to the child's
  arguments) when ``pass_stdout`` is set;
- two block flavours:
    * ``task``    — runs to completion; success = exit 0; exceeding ``timeout``
                    means it hung and is killed;
    * ``service`` — long-running, holds a port; success = still alive after
                    ``start_period`` (and its ``port`` accepts TCP if set);
                    exiting later counts as a crash;
- capture combined stdout+stderr live into ``blocks/<id>/output.log``;
- stop (SIGTERM→SIGKILL on the whole process group) and restart a block;
- schedule a whole DAG: a block starts only once all its dependencies have
  succeeded; if a dependency fails/hangs/stops, its dependents are marked
  ``blocked`` and wait until the operator fixes and re-runs the upstream block.

Connected blocks (linked by ``depends_on``) form chains that gate each other.
Blocks with no edges run independently — one failing never touches another.
"""

import os
import signal
import socket
import subprocess
import threading
import time

from . import store
from .store import _now


class Runner:
    def __init__(self, root, log=None):
        self.store = store.Store(root)
        # log(level, service, msg) — wired to backend.log_event; no-op otherwise
        self._log = log or (lambda level, service, msg: None)
        self._lock = threading.RLock()
        self._procs = {}      # block_id -> Popen | None(claimed, still starting)
        self._stopping = set()  # ids the operator asked to stop
        self._pipeline_active = False  # a sequential pipeline run is in progress
        self._reconcile()

    # -- startup reconciliation -------------------------------------------------

    def _reconcile(self):
        """After a server restart no child processes survive: clear stale state."""
        data = self.store.load()
        for bid, b in data["blocks"].items():
            if b["status"] in store.ACTIVE_STATUSES:
                self.store.set_status(bid, "idle", pid=None)

    # -- public API (called by the HTTP handler) -------------------------------

    def is_running(self, block_id):
        with self._lock:
            return block_id in self._procs

    def run_block(self, block_id):
        """Run a single block now, ignoring dependency gating (operator override).

        Used both for a fresh single run and for the fix-and-rerun flow: on
        success the scheduler cascades to any waiting dependents.
        """
        if self.store.get_block(block_id) is None:
            raise KeyError(block_id)
        threading.Thread(target=self._launch, args=(block_id,), daemon=True).start()

    def stop_block(self, block_id):
        """Stop a running block; its dependents become blocked."""
        with self._lock:
            proc = self._procs.get(block_id)
            self._stopping.add(block_id)
        if proc is not None:
            self._killpg(proc)
        with self._lock:
            self._procs.pop(block_id, None)
        self.store.set_status(block_id, "stopped", finished_at=_now(), pid=None)
        self._log("warn", block_id, "stopped")
        self._cascade(block_id, "stopped")

    def restart_block(self, block_id):
        self.stop_block(block_id)
        self.run_block(block_id)

    def run_pipeline(self):
        """Run the whole DAG **sequentially**: reset idle-ish blocks to queued,
        then start blocks one at a time in dependency order. The next block is
        only launched once the previous one has actually reached ``running``
        (a visible, ordered roll-out instead of a simultaneous burst). A block
        that also has dependencies additionally waits for them to *succeed*, so
        data passing between connected blocks stays correct.
        """
        blocks = self.store.load()["blocks"]
        store.validate_graph(blocks)  # raises on cycle / bad edge
        with self._lock:
            running = set(self._procs)
        for bid in blocks:
            if bid in running:
                continue
            self.store.set_status(
                bid, "queued", exit_code=None, finished_at=None, last_error=None
            )
        order = store.topo_sort(self.store.load()["blocks"])
        self._pipeline_active = True
        self._log("info", "pipeline", "sequential run started (%d blocks)" % len(order))
        threading.Thread(target=self._pipeline_worker, args=(order,), daemon=True).start()

    def _pipeline_worker(self, order):
        try:
            for bid in order:
                self._pipeline_step(bid)
        finally:
            self._pipeline_active = False
        self._log("info", "pipeline", "run finished")

    def _pipeline_step(self, bid):
        """Bring one block to the point where the next may start: wait until its
        dependencies have succeeded, launch it, then wait until it is running."""
        deadline = time.time() + 3600
        while time.time() < deadline:
            blocks = self.store.load()["blocks"]
            b = blocks.get(bid)
            if b is None:
                return
            if b["status"] in ("running", "success") or self.is_running(bid):
                return  # already up / handled (e.g. a service left running)
            deps = [blocks[d]["status"] for d in b["depends_on"] if d in blocks]
            if any(s in store.FAILED_STATUSES for s in deps):
                self.store.set_status(bid, "blocked", last_error="upstream did not succeed")
                self._log("warn", bid, "blocked (upstream failed)")
                return
            if all(s == "success" for s in deps):  # no deps, or every dep done
                self._launch(bid)
                # advance to the next block only once this one is actually running
                self._wait_state(bid, ("running", "success", "error", "hanging", "stopped"), 60)
                return
            time.sleep(0.1)

    def _wait_state(self, bid, states, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = self.store.get_block(bid)
            if b and b["status"] in states:
                return
            time.sleep(0.05)

    def stop_pipeline(self):
        self._pipeline_active = False
        with self._lock:
            running = list(self._procs)
        for bid in running:
            self.stop_block(bid)
        for bid, b in self.store.load()["blocks"].items():
            if b["status"] == "queued":
                self.store.set_status(bid, "idle")
        self._log("warn", "pipeline", "stopped")

    def output(self, block_id, since=0):
        """Return new bytes of a block's log from byte offset ``since``.

        A re-run truncates the file, so if ``since`` is past the end we start
        over from 0 — the client learns the run restarted.
        """
        path = self.store.output_path(block_id)
        block = self.store.get_block(block_id)
        status = block["status"] if block else None
        try:
            size = os.path.getsize(path)
        except OSError:
            return {"offset": 0, "data": "", "status": status}
        if since > size:
            since = 0
        try:
            with open(path, "rb") as f:
                f.seek(since)
                chunk = f.read()
        except OSError:
            return {"offset": since, "data": "", "status": status}
        return {
            "offset": since + len(chunk),
            "data": chunk.decode("utf-8", "replace"),
            "status": status,
        }

    # -- launching --------------------------------------------------------------

    def _launch(self, block_id):
        with self._lock:
            if block_id in self._procs:
                return  # already running / claimed by another finish event
            self._procs[block_id] = None  # claim the slot
            self._stopping.discard(block_id)
        self.store.set_status(
            block_id, "running", started_at=_now(), finished_at=None,
            exit_code=None, pid=None, last_error=None,
        )
        block = self.store.get_block(block_id)
        try:
            cmd, cwd, env = self._prepare(block)
        except Exception as exc:  # venv build / bad script — surface as error
            with self._lock:
                self._procs.pop(block_id, None)
            self._write_log(block_id, f"[runner] failed to prepare: {exc}\n", truncate=True)
            self.store.set_status(block_id, "error", last_error=str(exc), finished_at=_now())
            self._log("error", block_id, f"prepare failed: {exc}")
            self._cascade(block_id, "error")
            return

        # fresh log for this run
        self._write_log(block_id, "", truncate=True)
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # own process group -> killpg
            )
        except OSError as exc:
            with self._lock:
                self._procs.pop(block_id, None)
            self._write_log(block_id, f"[runner] failed to start: {exc}\n")
            self.store.set_status(block_id, "error", last_error=str(exc), finished_at=_now())
            self._log("error", block_id, f"start failed: {exc}")
            self._cascade(block_id, "error")
            return

        with self._lock:
            # a stop that arrived during startup wins
            if block_id in self._stopping:
                self._killpg(proc)
                self._procs.pop(block_id, None)
                return
            self._procs[block_id] = proc
        self.store.set_status(block_id, "running", pid=proc.pid)
        self._log("info", block_id, f"started ({block['type']}/{block['mode']}) pid {proc.pid}")

        threading.Thread(target=self._pump_output, args=(block_id, proc), daemon=True).start()
        threading.Thread(target=self._watch, args=(block_id, proc, dict(block)), daemon=True).start()

    def _prepare(self, block):
        """Build (cmd, cwd, env) for a block, creating its venv if requested."""
        block_dir = self.store.block_dir(block["id"])
        os.makedirs(block_dir, exist_ok=True)
        script = self.store.script_path(block)
        if not os.path.exists(script):
            self.store.write_script(block, "")
        argv = list(block["args"]) + self._upstream_args(block)

        env = os.environ.copy()
        if block["type"] == "python":
            env["PYTHONUNBUFFERED"] = "1"  # live output despite block buffering
            if block["venv"]:
                py = self._ensure_venv(block)
            else:
                py = _python_exe()
            cmd = [py, script] + argv
        else:  # sh — POSIX shell, deliberately not bash
            cmd = ["/bin/sh", script] + argv
        return cmd, block_dir, env

    def _upstream_args(self, block):
        """stdout of each direct dependency, appended to argv when pass_stdout."""
        if not block["pass_stdout"]:
            return []
        out = []
        for dep in block["depends_on"]:
            try:
                with open(self.store.output_path(dep), encoding="utf-8", errors="replace") as f:
                    out.append(f.read().strip())
            except OSError:
                out.append("")
        return out

    def _ensure_venv(self, block):
        """Create blocks/<id>/.venv on demand, (re)installing requirements.

        Returns the path to the venv's python. Raises on build failure.
        """
        venv_dir = self.store.venv_dir(block["id"])
        py = os.path.join(venv_dir, "bin", "python")
        if not os.path.exists(py):
            r = subprocess.run(
                [_python_exe(), "-m", "venv", venv_dir],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError("venv creation failed: " + (r.stderr or r.stdout).strip())
        reqs = (block.get("requirements") or "").strip()
        marker = os.path.join(venv_dir, ".requirements.txt")
        prev = ""
        try:
            with open(marker, encoding="utf-8") as f:
                prev = f.read().strip()
        except OSError:
            pass
        if reqs and reqs != prev:
            req_file = self.store.requirements_path(block["id"])
            store._atomic_write(req_file, reqs + "\n", 0o644)
            r = subprocess.run(
                [py, "-m", "pip", "install", "-r", req_file],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError("pip install failed: " + (r.stderr or r.stdout).strip()[-500:])
            store._atomic_write(marker, reqs + "\n", 0o644)
        return py

    # -- output pump ------------------------------------------------------------

    def _pump_output(self, block_id, proc):
        """Stream the child's merged stdout/stderr into its log file, live."""
        path = self.store.output_path(block_id)
        try:
            with open(path, "ab", buffering=0) as logf:
                for line in iter(proc.stdout.readline, b""):
                    logf.write(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass

    # -- watching ---------------------------------------------------------------

    def _watch(self, block_id, proc, block):
        if block["mode"] == "service":
            self._watch_service(block_id, proc, block)
        else:
            self._watch_task(block_id, proc, block)

    def _watch_task(self, block_id, proc, block):
        timeout = block["timeout"] or None
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._killpg(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if self._claimed_stop(block_id):
                return
            self._write_log(block_id, f"\n[runner] timed out after {block['timeout']}s, killed\n")
            self._finalize(block_id, "hanging", last_error=f"timeout {block['timeout']}s")
            return
        if self._claimed_stop(block_id):
            return
        if rc == 0:
            self._finalize(block_id, "success", exit_code=0)
        else:
            self._finalize(block_id, "error", exit_code=rc,
                           last_error=self._error_detail(block_id, f"exit code {rc}"))

    def _watch_service(self, block_id, proc, block):
        # give it a moment to either stay up or die immediately
        try:
            rc = proc.wait(timeout=block["start_period"])
        except subprocess.TimeoutExpired:
            rc = None  # still alive after start_period -> good sign
        if rc is not None:
            if self._claimed_stop(block_id):
                return
            self._finalize(block_id, "error", exit_code=rc,
                           last_error=self._error_detail(block_id, f"service exited early (code {rc})"))
            return
        # optional port probe
        port = block.get("port")
        if port and not self._port_up(port, deadline=time.time() + 5):
            self._killpg(proc)
            if self._claimed_stop(block_id):
                return
            self._write_log(block_id, f"\n[runner] port {port} never opened, killed\n")
            self._finalize(block_id, "error", last_error=f"port {port} not reachable")
            return
        # up: let dependents proceed
        self.store.set_status(block_id, "success", last_error=None)
        self._log("success", block_id, "service up")
        self._cascade(block_id, "success")
        # keep watching; a later exit is a crash
        rc = proc.wait()
        if self._claimed_stop(block_id):
            return
        with self._lock:
            self._procs.pop(block_id, None)
        self._write_log(block_id, f"\n[runner] service exited (code {rc})\n")
        self.store.set_status(block_id, "error", exit_code=rc,
                              last_error=self._error_detail(block_id, f"service crashed (code {rc})"),
                              finished_at=_now())
        self._log("error", block_id, f"service crashed (code {rc})")

    def _finalize(self, block_id, status, exit_code=None, last_error=None):
        with self._lock:
            self._procs.pop(block_id, None)
        self.store.set_status(block_id, status, exit_code=exit_code,
                              last_error=last_error, finished_at=_now(), pid=None)
        level = "success" if status == "success" else "error"
        self._log(level, block_id, last_error or status)
        self._cascade(block_id, status)

    # -- scheduler cascade ------------------------------------------------------

    def _cascade(self, block_id, status):
        """Propagate a block's terminal status to its direct dependents.

        On success, auto-run every direct dependent whose dependencies have all
        succeeded — so running one block flows down the whole connected chain by
        itself (idle dependents included). Dependents already running are left
        alone. On failure, still-pending dependents are marked blocked and the
        block propagates further down. Unconnected blocks have no dependents, so
        they are never touched here.

        During a sequential pipeline run the worker drives every launch itself,
        so cascading is skipped to keep the one-at-a-time ordering.
        """
        if self._pipeline_active:
            return
        blocks = self.store.load()["blocks"]
        for dep_id in store.dependents(blocks, block_id):
            dep = blocks[dep_id]
            if dep["status"] == "running" or self.is_running(dep_id):
                continue
            dep_statuses = [blocks[d]["status"] for d in dep["depends_on"] if d in blocks]
            if dep_statuses and all(s == "success" for s in dep_statuses):
                self._launch(dep_id)   # next block in the chain runs automatically
            elif any(s in store.FAILED_STATUSES for s in dep_statuses):
                if dep["status"] != "blocked":
                    self.store.set_status(dep_id, "blocked",
                                          last_error=f"upstream {block_id} did not succeed")
                    self._log("warn", dep_id, f"blocked by upstream {block_id}")
                    self._cascade(dep_id, "blocked")

    # -- process helpers --------------------------------------------------------

    def _claimed_stop(self, block_id):
        """True if the operator asked to stop this block (leave status alone)."""
        with self._lock:
            if block_id in self._stopping:
                self._procs.pop(block_id, None)
                return True
        return False

    def _killpg(self, proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _port_up(self, port, deadline):
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                    return True
            except OSError:
                time.sleep(0.2)
        return False

    def _error_detail(self, block_id, fallback):
        """Last meaningful line of a block's output, for a compact error label.

        Surfaces things like a docker 'Client.Timeout exceeded' pull failure right
        on the block, instead of a generic 'exit code 1'.
        """
        try:
            with open(self.store.output_path(block_id), encoding="utf-8", errors="replace") as f:
                lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        except OSError:
            lines = []
        for ln in reversed(lines):
            low = ln.lower()
            if any(k in low for k in ("error", "failed", "cannot", "exceeded", "refused", "denied")):
                return ln[:200]
        return lines[-1][:200] if lines else fallback

    def _write_log(self, block_id, text, truncate=False):
        path = self.store.output_path(block_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if truncate else "ab") as f:
            f.write(text.encode("utf-8", "replace"))


def _python_exe():
    """A base python interpreter for running scripts / building venvs."""
    import sys
    return sys.executable or "python3"
