# packmate-setup.sh — deploying Packmate on a vulnbox

A wrapper around [Packmate](https://gitlab.com/packmate/Packmate) (vendored in
`./Packmate-master`), a traffic capture and analysis tool for Attack-Defense
CTFs: live pcap per service, colored regex/substring/hex pattern matches,
TLS decryption via RSA key, HTTP/WebSocket decoding.

Unlike a manual Packmate install, `start` also runs an interactive wizard that
asks which game services exist and wires up two regex patterns per service —
**Flag OUT** (direction `OUTPUT`, catches your own flags leaking) and
**Flag IN** (direction `INPUT`, tells the checker apart from an exploit) —
exactly the split the [Packmate docs](Packmate-master/docs/USAGE.md) and
[firegex-setup.md](../firegex/firegex-setup.md) both recommend.

---

## Requirements

| Requirement | Notes |
|---|---|
| Linux | packet capture needs `libpcap`; not tested on macOS/WSL1 |
| Docker + compose plugin | the stack is two containers: `packmate` (host network, port 65000) and `db` (postgres, port 65001, localhost only) |
| python3 | stdlib only, runs the services/patterns wizard |
| network | to pull `registry.gitlab.com/packmate/packmate` — see [Offline](#offline) if the vulnbox has none |

---

## Quick start

```bash
cd packmate
./packmate-setup.sh --interface game --local-ip 10.10.10.5
```

Without `--interface`/`--local-ip` the script tries to auto-detect the IP on
the given interface, and otherwise asks for both interactively. Output looks
like:

```
[+] Packmate is up
    web UI   : http://10.10.10.5:65000
    login    : BinaryBears
    password : xK3nQ8vTpL2mWd9sZa
    saved to : /.../packmate/.packmate-credentials

== Сервисы ==
Ещё сколько сервисов добавить вручную (0 — пропустить) [1]: 2
Сервис 1: имя: checker-web
Сервис 1: порт: 8080
  checker-web:8080 — HTTP сервис [Д/н]:
...
```

Credentials are also saved to `.packmate-credentials` (mode 600).

## Actions

```bash
./packmate-setup.sh [action] [options]
```

| Action | What it does |
|---|---|
| `start` (default) | deps check → generate `.env` → `docker compose up -d` → wait for health → run the services/patterns wizard |
| `configure` | re-run just the wizard against an already-running stack — safe to repeat, skips services/patterns that already exist |
| `status` | `docker compose ps` |
| `logs` | follow container logs (Ctrl-C to quit) |
| `restart` | restart with the current `.env` |
| `stop` | stop the stack |
| `update` | pull fresh images + restart (prebuilt mode only) |
| `clean` | stop and **delete the postgres volume with all captured traffic** — asks for confirmation |

`start` is idempotent: rerunning it keeps the existing DB password and
re-applies `.env`, so it's safe to use as "make sure packmate is up" at the
start of every game.

## Options

| Option | Default | Purpose |
|---|---|---|
| `--interface NAME` | asked interactively | capture interface (e.g. `game`, `eth0`) |
| `--local-ip IP` | auto-detected on the interface, else asked | must be an IP actually bound to that interface |
| `--web-login NAME` | `BinaryBears` | web UI login |
| `--web-password PSW` | generated | web UI password |
| `--build` | — | build the image from `./Packmate-master` instead of pulling `registry.gitlab.com/packmate/packmate` |
| `--no-configure` | — | on `start`, skip the interactive wizard (run `./packmate-setup.sh configure` later) |

Environment variables instead of flags: `PACKMATE_INTERFACE`, `PACKMATE_LOCAL_IP`,
`PACKMATE_WEB_LOGIN`, `PACKMATE_WEB_PASSWORD`, `PACKMATE_DB_PASSWORD`.

---

## The services/patterns wizard

`packmate-configure.py` (invoked automatically by `start`, or manually via
`configure`):

1. If `../state/state.json` exists (the board's `monitor.sh discover` output),
   it's offered as a starting list of services/ports.
2. Asks for any remaining services by name + port + HTTP-or-binary. Binary
   services skip urldecode/packet-merge; both get separate control later in
   Packmate's own UI if you need TLS decryption or WebSocket inflation.
3. Picks a flag regex — reused from `../Farm/attdef.yml`'s `flags.format` if
   that file exists, otherwise asked (default `[A-Z0-9]{31}=`).
4. Registers each service (`POST /api/service/` — this is what makes Packmate
   save streams for that port at all) and creates `Flag OUT — <name>` /
   `Flag IN — <name>` regex patterns (`POST /api/pattern/`) scoped to that
   service.

Re-running it — because a new service just spawned mid-game, or the flag
format changed — only adds what's missing; it never creates duplicates.

**A new service showed up mid-game?**

```bash
./packmate-setup.sh configure
```

---

## Offline

The default `start` pulls `registry.gitlab.com/packmate/packmate`. On a
vulnbox cut off from the internet:

- Pre-pull on a machine with network access, then move the image over:
  ```bash
  docker save registry.gitlab.com/packmate/packmate:latest postgres:15.2 | gzip > packmate-images.tar.gz
  gunzip -c packmate-images.tar.gz | docker load
  ```
  then run `./packmate-setup.sh` as usual — it won't need to reach the registry.
- Or build from the vendored source with `--build` — note the vendored tree's
  `frontend/` git submodule is empty in this checkout (`.gitmodules` points at
  `packmate-frontend.git`); fetch it once while online (`git submodule update
  --init` inside `Packmate-master/`) before relying on `--build` offline.

---

## Troubleshooting

**No response on :65000** — check `./packmate-setup.sh logs`; the port and
bind address are baked into the Docker image (`--server.port=65000
--server.address=0.0.0.0`), they're not configurable through `.env`.

**Wizard says "no tty"** — it was triggered through the board
(`backend.py` → `UTIL_SCRIPTS['packmate']`), which runs scripts headless.
Run it over SSH instead: `cd packmate && ./packmate-setup.sh configure`.

**Restrict access** — `network_mode: host` means Packmate binds directly on
the host, docker doesn't gate the port. Same advice as
[firegex](../firegex/firegex-setup.md#quick-start): keep 65000/65001 closed to
everyone but your team via the host firewall, or tunnel over SSH instead of
exposing it.

**Forgot the password** — `cat packmate/.packmate-credentials`.

---

## Game-start checklist

1. `./packmate-setup.sh --interface <game iface> --local-ip <vulnbox ip>` —
   before the network goes live.
2. Answer the services wizard for every game port; confirm both `Flag OUT`
   and `Flag IN` patterns show up under **Patterns** in the UI.
3. Firewall off 65000/65001 to your team's network only.
4. When a new service appears mid-game: `./packmate-setup.sh configure`.
