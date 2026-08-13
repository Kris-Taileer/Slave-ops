# Single container that runs the Aether panel + the block runner. Blocks are
# NOT containerized individually: they are child processes of backend.py inside
# this one container. Combined with `network_mode: host` in compose.yml, any
# port a block opens is exposed directly on the host machine.
#
# python:3.12-slim already ships pip + the venv module, and its /bin/sh is dash
# (real POSIX sh) — which is exactly what `sh` blocks run under.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN chmod +x entrypoint.sh 2>/dev/null || true

# The panel; overridable via MONITOR_PORT / MONITOR_BIND.
EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
