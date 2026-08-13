"""Default demo blocks — a ready-made pipeline that exercises every feature.

``seed(store)`` creates any preset that isn't already present (idempotent, keyed
by id) and returns how many it added. It is called on first run (empty state)
and by the "Загрузить примеры" button (POST /api/pipeline/presets).

The demo shows, at a glance:

  Happy chain (connected, with argv data-passing):
      gen-token ──▶ transform ──▶ report
    gen-token prints a value; transform receives it through argv
    (``pass_stdout``); report is a POSIX-sh block that prints the result.

  flaky      — an *independent* block that fails. Because nothing links it to
               the chain, its failure never blocks the chain. Edit it to
               exit 0 and re-run to watch it go green (the fix-and-rerun flow);
               or add a dependency onto it to see a downstream block go
               "blocked".

  venv-demo  — a python block that runs inside its own ``.venv``.

  web-svc    — a *service* block: it holds a port open instead of exiting.
               Success means "still alive after start_period"; with the
               container's ``network_mode: host`` the port is reachable on the
               host machine. Stop/Restart it from the panel.
"""

from . import store


# Ordered so dependencies are created before their dependents.
PRESETS = [
    {
        "id": "gen-token",
        "name": "gen-token",
        "type": "python",
        "mode": "task",
        "script": '''#!/usr/bin/env python3
"""Produce a value for the next block. Whatever a block prints to stdout can be
handed to its dependents through argv."""
import random
import string

token = "".join(random.choices(string.ascii_uppercase + string.digits, k=31)) + "="
print(token)
''',
    },
    {
        "id": "transform",
        "name": "transform",
        "type": "python",
        "mode": "task",
        "depends_on": ["gen-token"],
        "pass_stdout": True,
        "script": '''#!/usr/bin/env python3
"""Connected block: receives the upstream block's stdout as argv[1] because
this block has `pass_stdout` on. Transforms it and prints the result."""
import sys

data = sys.argv[1].strip() if len(sys.argv) > 1 else "(no input)"
print(f"got {len(data)} chars from upstream")
print("lowered:", data.lower())
''',
    },
    {
        "id": "report",
        "name": "report",
        "type": "sh",
        "mode": "task",
        "depends_on": ["transform"],
        "pass_stdout": True,
        "script": '''#!/bin/sh
# POSIX sh (dash), deliberately not bash. Final step of the chain: it receives
# the previous block's stdout as "$1".
echo "report: chain finished"
echo "---- payload from transform ----"
echo "$1"
''',
    },
    {
        "id": "flaky",
        "name": "flaky",
        "type": "python",
        "mode": "task",
        "timeout": 30,
        "script": '''#!/usr/bin/env python3
"""Independent block that fails on purpose. Notice the happy chain still runs —
unconnected blocks do not affect each other.

Try this: change the exit code to 0 and press Run to watch it turn green
(fix-and-rerun). Or add this block as a dependency of another one, run the
pipeline, and watch the dependent go "blocked" until this one succeeds."""
import sys

print("doing risky work...")
sys.exit(1)
''',
    },
    {
        "id": "venv-demo",
        "name": "venv-demo",
        "type": "python",
        "mode": "task",
        "venv": True,
        "script": '''#!/usr/bin/env python3
"""Runs inside this block's own virtualenv (blocks/venv-demo/.venv). The prefix
below points into that .venv, proving the isolation. Add packages under
`requirements` in the inspector and they install into this venv only."""
import sys

print("interpreter:", sys.executable)
print("prefix     :", sys.prefix)
''',
    },
    {
        "id": "web-svc",
        "name": "web-svc",
        "type": "python",
        "mode": "service",
        "port": 8099,
        "start_period": 2,
        "script": '''#!/usr/bin/env python3
"""Service block: holds a port open instead of returning. It counts as "up"
once it survives start_period (and its port accepts a connection). With the
container running network_mode: host, this port is exposed on the host."""
import http.server
import socketserver

PORT = 8099


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"slave-ops demo service is up\\n")

    def log_message(self, *args):
        pass


class Server(socketserver.TCPServer):
    allow_reuse_address = True


with Server(("0.0.0.0", PORT), Handler) as httpd:
    print("listening on", PORT, flush=True)
    httpd.serve_forever()
''',
    },
]


# Real CTF services from Ferr0x/ad-ctf-infra (challenges/intro/services), vendored
# under services/. Two chains that bring them up as block pipelines.
#
#   blocconote (native): setup ──▶ blocconote(service) ──▶ check
#     Flask app run inside the block's own .venv, no docker needed.
#
#   bandiera (docker compose): bandiera-up ──▶ bandiera-check
#     Node + MySQL, so it comes up via `docker compose`. Needs docker on the host
#     (mount /var/run/docker.sock into the blocks container).
#
# Block scripts run with cwd = <root>/blocks/<id>, so ../../services/<name> is the
# vendored service directory (works both in the container at /app and locally).

_BLOCCONOTE_REQS = (
    "blinker==1.9.0\n"
    "click==8.1.8\n"
    "Flask==3.1.0\n"
    "itsdangerous==2.2.0\n"
    "Jinja2==3.1.6\n"
    "MarkupSafe==3.0.2\n"
    "Werkzeug==3.1.3\n"
)

INTRO_SERVICES = [
    {
        "id": "blocconote-setup",
        "name": "blocconote-setup",
        "type": "sh",
        "mode": "task",
        "script": '''#!/bin/sh
# Ensure the notes directory the Flask service writes into exists.
mkdir -p ../../services/blocconote/notes
echo "blocconote: notes dir ready"
''',
    },
    {
        "id": "blocconote",
        "name": "blocconote",
        "type": "python",
        "mode": "service",
        "venv": True,
        "requirements": _BLOCCONOTE_REQS,
        "port": 5000,
        "start_period": 3,
        "depends_on": ["blocconote-setup"],
        "script": '''#!/usr/bin/env python3
"""Bring up the blocconote Flask service natively, inside this block's own .venv.
Listens on :5000 by default; pass a port as the first argv to override (handy
when 5000 is taken, e.g. AirPlay on macOS). With host networking it lands on the
host."""
import os
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
root = os.path.abspath(os.path.join(os.getcwd(), "..", "..", "services", "blocconote"))
os.chdir(root)
os.makedirs("notes", exist_ok=True)
sys.path.insert(0, os.path.join(root, "src"))

from app import app  # noqa: E402

app.run(host="0.0.0.0", port=port)
''',
    },
    {
        "id": "blocconote-check",
        "name": "blocconote-check",
        "type": "python",
        "mode": "task",
        "timeout": 30,
        "depends_on": ["blocconote"],
        "script": '''#!/usr/bin/env python3
"""Test the chain end-to-end: round-trip a note through the running service
(stdlib only, no venv needed). Runs after blocconote reports "up". Port defaults
to 5000; pass a port as argv[1] to match a non-default blocconote port."""
import json
import sys
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "5000"
BASE = "http://127.0.0.1:" + port


def call(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=5))

put = call("/put_note", {"name": "flag", "value": "CTF{blocconote_up}"})
assert put.get("ok"), put
got = call("/get_note", {"name": "flag"})
assert got.get("ok") and got.get("note") == "CTF{blocconote_up}", got
print("blocconote OK, round-trip:", got["note"])
''',
    },
    {
        "id": "bandiera-up",
        "name": "bandiera-up",
        "type": "sh",
        "mode": "task",
        "timeout": 300,
        "script": '''#!/bin/sh
# bandiera is Node + MySQL, so bring it up with docker compose (app on :8181,
# mysql on :3306). Requires docker on the host — mount /var/run/docker.sock into
# the blocks container. Does nothing here if docker is unavailable (block errors).
cd ../../services/bandiera || exit 1
docker compose up -d --build
echo "bandiera: compose up issued"
''',
    },
    {
        "id": "bandiera-check",
        "name": "bandiera-check",
        "type": "sh",
        "mode": "task",
        "timeout": 120,
        "depends_on": ["bandiera-up"],
        "script": '''#!/bin/sh
# Wait for the API (MySQL init takes a bit), then store a flag to prove it's up.
i=0
while [ "$i" -lt 90 ]; do
  if curl -fsS -X POST http://127.0.0.1:8181/bandiera \\
       -H 'Content-Type: application/json' -d '{"bandiera":"CTF{bandiera_up}"}' >/dev/null 2>&1; then
    echo "bandiera OK on :8181"
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done
echo "bandiera did not come up in time" >&2
exit 1
''',
    },
]

PRESET_SETS = {"demo": PRESETS, "intro": INTRO_SERVICES}


def seed(pipeline_store, which="demo"):
    """Create any preset from set `which` not already present. Returns count."""
    specs = PRESET_SETS.get(which, PRESETS)
    existing = pipeline_store.list_blocks()
    created = 0
    for spec in specs:
        if spec["id"] in existing:
            continue
        fields = {k: v for k, v in spec.items() if k not in ("id", "name", "script")}
        block = pipeline_store.create_block(spec["name"], script=spec["script"], **fields)
        # create_block derives the id from the name; our names slugify to the
        # ids the presets reference, but guard against a surprise just in case.
        if block["id"] != spec["id"]:
            raise store.ValidationError(
                f"preset id mismatch: expected {spec['id']}, got {block['id']}"
            )
        created += 1
    return created
