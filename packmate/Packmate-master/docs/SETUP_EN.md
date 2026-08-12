## Setup

Packmate uses properties from the `.env` file (in the same directory as `docker-compose.yml`)

### Primary settings
```dotenv
# Local IP of a server on which the traffic in directed. Used to tell incoming packets from outgoing.
PACKMATE_LOCAL_IP=10.20.1.1
# Username for the web interface
PACKMATE_WEB_LOGIN=SomeUser
# Password for the web interface
PACKMATE_WEB_PASSWORD=SomeSecurePassword
```

### Modes of operation
Packmate supports 3 modes of operation: `LIVE`, `FILE` и `VIEW`.
1. `LIVE` - the usual mode during a CTF. Packmate processes live traffic and instantly displays the results.
2. `FILE` - processes traffic from pcap files. Useful to analyze traffic from past CTFs where Packmate wasn't launched, or CTFs where it's impossible to use it on the vulnbox.
3. `VIEW` - Packmate does not process any traffic, but simply shows already processed streams. Useful for post-game analyses.

<details>
  <summary>LIVE setup</summary>

Set the interface through which the game traffic passes.
IP address from `PACKMATE_LOCAL_IP` should be bound to the same interface.

```dotenv
# Mode: capturing
PACKMATE_MODE=LIVE
# Interface to capture on
PACKMATE_INTERFACE=game
```

</details>

<details>
  <summary>FILE setup</summary>

Set the name of the pcap file in the `pcaps` directory.
After the startup, in the web interface, you will see the button that activates the file processing.
It's important that by this moment all services and patterns are already created (see Usage).

```dotenv
# Mode: pcap file anysis
PACKMATE_MODE=FILE
# Path to pcap file in the pcaps directory
PACKMATE_PCAP_FILE=dump.pcap
```

</details>

<details>
  <summary>VIEW setup</summary>

In that mode, Packmate simply shows already existing data.

```dotenv
# Mode: viewing the data
PACKMATE_MODE=VIEW
```

</details>

### Database cleanup
On large CTFsб after some time a lot of traffic will pile up. This can slow Packmate down and take a lot of drive space.

To optimize the workflow, it is recommended to enable periodical database cleanup of old streams. It will only work in the `LIVE` mode.
```dotenv
PACKMATE_OLD_STREAMS_CLEANUP_ENABLED=true
# Old streams removal interval (in minutes).
# It's better to use small numbers so the streams are removed in small chunks and don't overload the server.
PACKMATE_OLD_STREAMS_CLEANUP_INTERVAL=1
# How old the stream must be to be removed (in minutes before current time)
PACKMATE_OLD_STREAMS_CLEANUP_THRESHOLD=240
```

### Additional settings

```dotenv
# Database password. Considering it only listens on localhost, it's not mandatory to change it, but you can do it for additional security.
PACKMATE_DB_PASSWORD=K604YnL3G1hp2RDkCZNjGpxbyNpNHTRb
# Packmate version. Change it if you want to use a different version from the docker registry.
BUILD_TAG=latest
```

To use the TLS decryption, you have to put the matching private key in the `rsa_keys` directory.

Database files are being saved in `./data`, so to reset the database, you need to delete this directory.