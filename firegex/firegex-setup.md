# firegex-setup.sh — deploying Firegex on a vulnbox

A wrapper around [Pwnzer0tt1/firegex](https://github.com/Pwnzer0tt1/firegex), a firewall for
Attack-Defense CTFs. The script does everything you would otherwise do by hand in the first
minutes of a game: installs dependencies, brings up docker, clones the repo, starts the
service, and hands you the web UI address together with the password.

---

## Requirements

| Requirement | Notes |
|---|---|
| Linux | mandatory: Firegex works through nftables/NFQUEUE, it will not run on macOS or WSL1 |
| root | needed for nftables and for installing packages |
| network | for `git clone` and the docker image; on an isolated vulnbox see [Offline](#offline-and-isolated-vulnboxes) |

Docker, `git`, `curl` and `python3` are installed by the script if missing. Supported package
managers: apt, dnf, yum, pacman, apk, zypper.

---

## Quick start

Copy the script to the vulnbox and run it:

```bash
scp firegex-setup.sh root@<vulnbox>:/root/
```

```bash
sudo bash /root/firegex-setup.sh
```

A minute or two later the tail of the output looks like this:

```
[+] Firegex is up
    web UI   : http://10.60.3.1:4444
    password : xK3nQ8vTpL2mWd9sZa
    saved to : /opt/firegex/.firegex-credentials
```

The password is also stored in `/opt/firegex/.firegex-credentials` (mode 600) — read it from
there if you lost the output.

**Close off outside access right away.** A Firegex UI open on 4444 is a gift to your
opponents:

```bash
sudo bash /root/firegex-setup.sh --allowed-ips 10.80.5.0/24
```

where the CIDR is your team's network. If the network is unpredictable, the alternative is to
bind to localhost only and reach it over an SSH tunnel:

```bash
sudo bash /root/firegex-setup.sh --host 127.0.0.1
```

```bash
ssh -N -L 4444:127.0.0.1:4444 root@<vulnbox>
```

---

## Actions

```bash
sudo ./firegex-setup.sh [action] [options]
```

| Action | What it does |
|---|---|
| `start` (default) | deps → clone/pull → start, prints the URL and password |
| `status` | service state |
| `logs` | Firegex logs (interactive, Ctrl-C to quit) |
| `restart` | restart with the current config |
| `stop` | stop the service |
| `update` | `git pull` + restart |
| `clean` | stop and **delete the volume with all settings** — asks for confirmation |

`start` is idempotent: running it again on an already-installed machine just updates the repo
and restarts the service. Note, though, that a new `--password` will change the password.

## Options

| Option | Default | Purpose |
|---|---|---|
| `-p, --port N` | `4444` | web interface port |
| `-w, --password PSW` | generated | login password; without it the script generates 20 characters |
| `--host IP` | from Firegex config | address to bind to (`127.0.0.1` for a tunnel) |
| `--allowed-ips CIDR` | — | who may reach the UI; **set this during a game** |
| `--build` | — | build the image from source instead of pulling it (minutes instead of seconds) |
| `--standalone` | — | no-docker mode: rootless environment, or docker unavailable |
| `-d, --dir PATH` | `/opt/firegex` | install location |
| `--no-deps` | — | leave packages and docker alone, everything is already installed |
| `-h, --help` | — | help |

Environment variables instead of flags: `FIREGEX_PORT`, `FIREGEX_PASSWORD`, `FIREGEX_DIR`,
`FIREGEX_REPO`.

### Examples

Own password, own port, access limited to the team:

```bash
sudo ./firegex-setup.sh -w 'CorrectHorseBattery' -p 8080 --allowed-ips 10.80.5.0/24
```

Password from the environment, to keep it out of shell history and out of `ps`:

```bash
sudo FIREGEX_PASSWORD='...' ./firegex-setup.sh
```

Vulnbox without docker (or a rootless container):

```bash
sudo ./firegex-setup.sh --standalone
```

Update to a fresh version mid-game:

```bash
sudo ./firegex-setup.sh update
```

---

## What to do next in the UI

Firegex modules, in the order they tend to matter during a game:

**Firewall Rules** — allow/deny on top of nftables, a ufw equivalent driven from the web UI.
First thing to do: close everything except the game service ports and SSH. It is cheap and it
immediately cuts off scanners and the stray ports the organizers left behind.

**Netfilter Regex (nfregex)** — the main tool. You add a service (port, TCP/UDP, IPv4/IPv6)
and enable it; traffic then goes through NFQUEUE, PCRE2 regexes are matched by a C++ filter in
kernel space, and a match drops the packet. Rules are supplied base64-encoded.

Practices that save you grief:
- Blacklist a **specific exploit signature** (payload, path, magic parameter) rather than
  "anything suspicious". A broad regex will take down your own checker, and SLA costs more
  than a handful of stolen flags.
- Match **inbound** traffic on the exploit and **outbound** traffic on the flag format
  (`[A-Z0-9]{31}=` or whatever your game uses). The latter saves you while you still have no
  idea what the exploit does.
- After every rule, watch the block counter and the service status: if the checker turns the
  service red, disable the rule first and investigate afterwards.

**Hijack Port to Proxy** — redirects a service port to your own proxy on loopback. Useful once
regexes are not enough and you have to parse the protocol yourself.

**Netfilter Proxy (nfproxy)** — Python filters on top of nfqueue, with built-in protocol
parsers (HTTP, for example). You write a filter, test it with the `fgex` CLI tool, then load
it. This is for when "drop it by regex" no longer works and you need stateful logic or a
parsed request.

**TLS Decryption** — terminates TLS, exposes the decrypted traffic on loopback for the other
filters, then re-encrypts it. Only needed when the service runs behind HTTPS.

The **docs** button inside the interface opens per-module documentation with the current rule
syntax.

---

## Offline and isolated vulnboxes

Game vulnboxes are often cut off from the internet. Prepare in advance, not five minutes
before the start:

- Run the script on an identical machine at home, then move `/opt/firegex` over as a whole.
- Export and import the image by hand:

```bash
docker save ghcr.io/pwnzer0tt1/firegex:latest | gzip > firegex.tar.gz
```

```bash
gunzip -c firegex.tar.gz | docker load
```

  after that run `sudo ./firegex-setup.sh --no-deps` and the script will not reach out to the
  network for the image.
- If docker is unavailable altogether, `--standalone` builds and runs Firegex directly on
  Python, with no container.

---

## Troubleshooting

**`Firegex only runs on Linux`** — you ran the script on a Mac. This is not a bug: NFQUEUE and
nftables exist on Linux only. Copy it to the vulnbox.

**`docker is installed but the daemon is not responding`** —

```bash
sudo systemctl start docker
```

then retry. Inside a container or an LXC without systemd docker may not come up at all — use
`--standalone` there.

**`no docker compose`** — install the plugin by hand (`docker-compose-plugin` in apt/dnf) or
switch to `--standalone`.

**A regex is enabled but traffic is not filtered** — check that the kernel modules are loaded:

```bash
lsmod | grep -E 'nfnetlink_queue|nft_queue'
```

The script runs `modprobe` at install time, but after a vulnbox reboot they may be gone. On
custom or stripped-down kernels NFQUEUE is sometimes not compiled in at all — then Firewall
Rules and port hijack are what you are left with.

**The service went down after you enabled a filter** — disable the filter in the UI first,
investigate second. If the UI is unreachable, stop everything:

```bash
sudo ./firegex-setup.sh stop
```

The nftables rules are removed along with the service and traffic flows directly again.

**Forgot the password** —

```bash
sudo cat /opt/firegex/.firegex-credentials
```

Change it without reinstalling:

```bash
cd /opt/firegex && sudo python3 run.py config --password
```

---

## Game-start checklist

1. `sudo ./firegex-setup.sh --allowed-ips <team network>` — before the network goes live.
2. Verify the UI opens and the password login works.
3. Firewall Rules: close everything unnecessary, keep the service ports and SSH.
4. Create an nfregex entry for every game port, **left disabled**, so you can enable one with
   a single click when the first exploit lands.
5. Put the password and the URL in the team chat — you will not be the only one administering
   this.
