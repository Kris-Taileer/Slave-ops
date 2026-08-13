"""Persistent store and pure DAG logic for script blocks.

No subprocess, no HTTP, no Godot — just data. This module is unit-testable on
its own; the execution engine lives in ``runner.py`` and the HTTP surface in
``backend.py``.

Layout on disk (all relative to ``root``):

    state/pipeline.json        registry of blocks (definitions + last status)
    blocks/<id>/script.py|.sh  the block's script, editable from the web
    blocks/<id>/requirements.txt  optional, for a python block's own .venv
    blocks/<id>/.venv/         optional per-block virtualenv (created by runner)
    blocks/<id>/output.log     last run's combined stdout+stderr (written by runner)
"""

import json
import os
import re
import tempfile
import threading
import time

# --- block status vocabulary -------------------------------------------------

# idle      never run (or reset)
# queued    scheduled as part of a pipeline run, waiting for its turn
# running   process is alive
# success   task exited 0 / service came up and is alive
# error     task exited non-zero / service died or failed to come up
# hanging   task exceeded its timeout and was killed
# blocked   an upstream dependency failed; will not start until it is fixed
# stopped   stopped by the operator
STATUSES = (
    "idle",
    "queued",
    "running",
    "success",
    "error",
    "hanging",
    "blocked",
    "stopped",
)

# Statuses that mean "this dependency did not succeed", so its dependents in a
# connected chain must not run.
FAILED_STATUSES = ("error", "hanging", "blocked", "stopped")

# Statuses that mean "there is (or should be) a live/pending process".
ACTIVE_STATUSES = ("queued", "running")

BLOCK_TYPES = ("python", "sh")
BLOCK_MODES = ("task", "service")

# Blocks are grouped into named tabs ("boards"). This one always exists.
DEFAULT_BOARD = "default"

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class CycleError(Exception):
    """Raised when the dependency graph contains a cycle."""


class ValidationError(Exception):
    """Raised when a block definition or the graph is invalid."""


def slugify(name):
    """Turn a human name into an id matching ``^[A-Za-z0-9_.-]+$``."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", (name or "").strip().lower()).strip("-._")
    return slug or "block"


def _coerce_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_num(value):
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def new_block(block_id, name, **overrides):
    """Build a block dict with defaults. ``overrides`` may set any field."""
    block = {
        "id": block_id,
        "name": name or block_id,
        "type": "python",       # python | sh
        "mode": "task",         # task | service
        "venv": False,          # python-only: run inside blocks/<id>/.venv
        "requirements": "",     # optional pip requirements for the venv
        "args": [],             # static argv passed to the script
        "timeout": 60,          # task: seconds before it counts as hanging
        "port": None,           # service: TCP port to probe for "up"
        "start_period": 2,      # service: seconds alive before it counts as up
        "depends_on": [],       # ids this block waits on (connected edges)
        "pass_stdout": False,   # feed upstream stdout into this block's argv
        "x": None,              # canvas position (set when dragged in the UI)
        "y": None,
        "board": DEFAULT_BOARD, # which named tab/board this block lives on
        "status": "idle",
        "exit_code": None,
        "pid": None,
        "started_at": None,
        "finished_at": None,
        "last_error": None,
    }
    block.update(overrides)
    return _normalize(block)


def _normalize(block):
    """Coerce a block's fields to their expected types/domains in place."""
    block["name"] = str(block.get("name") or block.get("id") or "")
    block["type"] = block.get("type") if block.get("type") in BLOCK_TYPES else "python"
    block["mode"] = block.get("mode") if block.get("mode") in BLOCK_MODES else "task"
    block["venv"] = bool(block.get("venv"))
    block["requirements"] = str(block.get("requirements") or "")
    args = block.get("args") or []
    if isinstance(args, str):
        args = args.split()
    block["args"] = [str(a) for a in args]
    block["timeout"] = max(0, _coerce_int(block.get("timeout"), 60))
    port = block.get("port")
    block["port"] = _coerce_int(port, None) if port not in (None, "") else None
    block["start_period"] = max(0, _coerce_int(block.get("start_period"), 2))
    deps = block.get("depends_on") or []
    # de-dupe, drop self-reference, preserve order
    seen = set()
    clean_deps = []
    for d in deps:
        d = str(d)
        if d == block["id"] or d in seen:
            continue
        seen.add(d)
        clean_deps.append(d)
    block["depends_on"] = clean_deps
    block["pass_stdout"] = bool(block.get("pass_stdout"))
    block["x"] = _coerce_num(block.get("x"))
    block["y"] = _coerce_num(block.get("y"))
    board = str(block.get("board") or DEFAULT_BOARD).strip()
    block["board"] = board or DEFAULT_BOARD
    if block.get("status") not in STATUSES:
        block["status"] = "idle"
    return block


# --- pure graph algorithms ---------------------------------------------------

def dependents(blocks, block_id):
    """Ids that list ``block_id`` in their depends_on (direct downstream)."""
    return [bid for bid, b in blocks.items() if block_id in b.get("depends_on", [])]


def descendants(blocks, block_id):
    """All transitive downstream ids of ``block_id``."""
    out = []
    seen = set()
    stack = list(dependents(blocks, block_id))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        stack.extend(dependents(blocks, cur))
    return out


def topo_sort(blocks):
    """Return ids in dependency order (deps before dependents).

    Raises CycleError if the graph is not a DAG.
    """
    indegree = {bid: 0 for bid in blocks}
    for bid, b in blocks.items():
        for dep in b.get("depends_on", []):
            if dep in blocks:
                indegree[bid] += 1
    # deterministic order: sort ready ids
    ready = sorted(bid for bid, d in indegree.items() if d == 0)
    order = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for child in sorted(dependents(blocks, cur)):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(order) != len(blocks):
        stuck = sorted(set(blocks) - set(order))
        raise CycleError("dependency cycle involving: " + ", ".join(stuck))
    return order


def topo_levels(blocks):
    """Map each id to its column index = longest dependency path length.

    Roots (no deps) are level 0. Used by the frontend for graph layout.
    """
    order = topo_sort(blocks)
    level = {}
    for bid in order:
        deps = [d for d in blocks[bid].get("depends_on", []) if d in blocks]
        level[bid] = 0 if not deps else 1 + max(level[d] for d in deps)
    return level


def validate_graph(blocks):
    """Check that every dependency exists and the graph is acyclic."""
    for bid, b in blocks.items():
        for dep in b.get("depends_on", []):
            if dep not in blocks:
                raise ValidationError(f"{bid}: unknown dependency {dep!r}")
            if dep == bid:
                raise ValidationError(f"{bid}: cannot depend on itself")
    topo_sort(blocks)  # raises CycleError on cycle


# --- persistent store --------------------------------------------------------

class Store:
    """Thread-safe registry of blocks backed by ``state/pipeline.json``.

    All mutating methods take an internal lock and read-modify-write the JSON
    file, so concurrent callers (HTTP handler threads, runner watch threads)
    stay consistent.
    """

    SCRIPT_NAMES = {"python": "script.py", "sh": "script.sh"}

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.state_dir = os.path.join(self.root, "state")
        self.blocks_dir = os.path.join(self.root, "blocks")
        self.pipeline_file = os.path.join(self.state_dir, "pipeline.json")
        self._lock = threading.RLock()
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.blocks_dir, exist_ok=True)

    # -- paths --

    def block_dir(self, block_id):
        return os.path.join(self.blocks_dir, block_id)

    def script_path(self, block):
        """Path to a block's script file, keyed by its type."""
        name = self.SCRIPT_NAMES.get(block.get("type"), "script.py")
        return os.path.join(self.block_dir(block["id"]), name)

    def output_path(self, block_id):
        return os.path.join(self.block_dir(block_id), "output.log")

    def requirements_path(self, block_id):
        return os.path.join(self.block_dir(block_id), "requirements.txt")

    def venv_dir(self, block_id):
        return os.path.join(self.block_dir(block_id), ".venv")

    # -- raw load/save --

    def load(self):
        with self._lock:
            try:
                with open(self.pipeline_file, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("generated_at", None)
            data.setdefault("blocks", {})
            for bid, b in list(data["blocks"].items()):
                b["id"] = bid
                _normalize(b)
            data["boards"] = self._resolve_boards(data)
            return data

    def _resolve_boards(self, data):
        """Stored board list, kept in sync with the boards blocks reference.

        Explicitly-created empty boards persist; any board a block points at is
        also included; DEFAULT_BOARD always exists and comes first.
        """
        stored = data.get("boards")
        boards = [str(x) for x in stored] if isinstance(stored, list) else []
        for b in data["blocks"].values():
            bd = b.get("board") or DEFAULT_BOARD
            if bd not in boards:
                boards.append(bd)
        if DEFAULT_BOARD not in boards:
            boards.insert(0, DEFAULT_BOARD)
        return boards

    def _save(self, data):
        data["generated_at"] = _now()
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        _atomic_write(self.pipeline_file, payload, 0o600)

    # -- block CRUD --

    def list_blocks(self):
        return self.load()["blocks"]

    def get_block(self, block_id):
        return self.load()["blocks"].get(block_id)

    # -- boards (named tabs) --

    def list_boards(self):
        return self.load()["boards"]

    def create_board(self, name):
        name = str(name or "").strip()
        if not name:
            raise ValidationError("board name required")
        with self._lock:
            data = self.load()
            if name not in data["boards"]:
                data["boards"].append(name)
                self._save(data)
            return data["boards"]

    def rename_board(self, old, new):
        new = str(new or "").strip()
        if not new:
            raise ValidationError("board name required")
        with self._lock:
            data = self.load()
            if old not in data["boards"]:
                raise KeyError(old)
            seen = []
            for x in (new if x == old else x for x in data["boards"]):
                if x not in seen:
                    seen.append(x)
            data["boards"] = seen
            for b in data["blocks"].values():
                if (b.get("board") or DEFAULT_BOARD) == old:
                    b["board"] = new
            self._save(data)
            return data["boards"]

    def delete_board(self, name):
        """Remove a board; its blocks move back to DEFAULT_BOARD. The default
        board itself cannot be deleted."""
        if name == DEFAULT_BOARD:
            return False
        with self._lock:
            data = self.load()
            if name not in data["boards"]:
                return False
            data["boards"] = [x for x in data["boards"] if x != name]
            for b in data["blocks"].values():
                if (b.get("board") or DEFAULT_BOARD) == name:
                    b["board"] = DEFAULT_BOARD
            self._save(data)
            return True

    def create_block(self, name, script="", **fields):
        """Create a new block with a unique id derived from ``name``."""
        with self._lock:
            data = self.load()
            block_id = self._unique_id(slugify(name), data["blocks"])
            block = new_block(block_id, name, **fields)
            # Reject a graph-breaking definition before writing anything.
            candidate = dict(data["blocks"])
            candidate[block_id] = block
            validate_graph(candidate)
            os.makedirs(self.block_dir(block_id), exist_ok=True)
            self.write_script(block, script if script else _starter_script(block))
            data["blocks"][block_id] = block
            self._save(data)
            return block

    def update_block(self, block_id, fields=None, script=None):
        """Patch a block's config and/or rewrite its script.

        Validates the resulting graph; on a bad graph nothing is persisted.
        """
        with self._lock:
            data = self.load()
            block = data["blocks"].get(block_id)
            if block is None:
                raise KeyError(block_id)
            old_type = block["type"]
            if fields:
                allowed = {
                    "name", "type", "mode", "venv", "requirements", "args",
                    "timeout", "port", "start_period", "depends_on", "pass_stdout",
                    "x", "y", "board",
                }
                for key, value in fields.items():
                    if key in allowed:
                        block[key] = value
                _normalize(block)
            validate_graph(data["blocks"])
            # If the type changed, the script file name changes too — move it.
            if block["type"] != old_type:
                self._rename_script(block_id, old_type, block["type"])
            if script is not None:
                self.write_script(block, script)
            self._save(data)
            return block

    def set_status(self, block_id, status, **fields):
        """Update just the runtime fields of a block (called a lot by runner)."""
        if status is not None and status not in STATUSES:
            raise ValidationError(f"invalid status {status!r}")
        with self._lock:
            data = self.load()
            block = data["blocks"].get(block_id)
            if block is None:
                return None
            if status is not None:
                block["status"] = status
            for key in ("exit_code", "pid", "started_at", "finished_at", "last_error"):
                if key in fields:
                    block[key] = fields[key]
            self._save(data)
            return block

    def delete_block(self, block_id):
        """Remove a block and detach it from everyone that depended on it."""
        with self._lock:
            data = self.load()
            if block_id not in data["blocks"]:
                return False
            del data["blocks"][block_id]
            for b in data["blocks"].values():
                if block_id in b["depends_on"]:
                    b["depends_on"] = [d for d in b["depends_on"] if d != block_id]
            self._save(data)
        _rmtree(self.block_dir(block_id))
        return True

    # -- scripts --

    def read_script(self, block):
        try:
            with open(self.script_path(block), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def write_script(self, block, content):
        os.makedirs(self.block_dir(block["id"]), exist_ok=True)
        path = self.script_path(block)
        _atomic_write(path, content if content is not None else "", 0o755)

    def _rename_script(self, block_id, old_type, new_type):
        old = os.path.join(self.block_dir(block_id), self.SCRIPT_NAMES[old_type])
        new = os.path.join(self.block_dir(block_id), self.SCRIPT_NAMES[new_type])
        if os.path.exists(old) and not os.path.exists(new):
            os.replace(old, new)

    # -- helpers --

    def _unique_id(self, base, existing):
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"


# --- module helpers ----------------------------------------------------------

def _now():
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if ts and ts[-5] in "+-":
        ts = ts[:-2] + ":" + ts[-2:]
    return ts


def _atomic_write(path, text, mode):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _rmtree(path):
    import shutil
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _starter_script(block):
    """A minimal runnable script so a fresh block does something visible."""
    if block["type"] == "sh":
        return (
            "#!/bin/sh\n"
            "# POSIX sh (dash), not bash.\n"
            "echo \"hello from $0\"\n"
            "# received args: \"$@\"\n"
        )
    return (
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('hello from', sys.argv[0])\n"
        "print('args:', sys.argv[1:])\n"
    )
