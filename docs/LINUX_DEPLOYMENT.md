# Linux deployment at 192.168.1.8

This runbook deploys the proof-of-concept stack to the local Linux host. It
assumes an x86-64 machine, SSH access and Docker Engine with the Compose plugin.
Keep the host on the trusted LAN; the current broker has no TLS or ACL policy.

## 1. Prepare the host

From the development computer:

```bash
ssh <linux-user>@192.168.1.8
uname -m
docker version
docker compose version
git --version
```

The current Godot 4.2.1 image targets `linux/amd64`, so `uname -m` should report
`x86_64`. The Compose platform pin also lets Apple Silicon development machines
validate the same image through Docker emulation. Do not remove it unless the
Godot binary in `Dockerfile.headless` is changed to match another architecture.

Install Docker and Git through the server distribution's supported package
instructions if any command is missing. Add the SSH user to Docker's group only
if that access is acceptable on this host.

## 2. Clone and configure

```bash
git clone https://github.com/yohhalley/terminal-scms.git
cd terminal-scms
cp .env.example .env
```

Edit `.env` before starting. At minimum replace all placeholder secrets and set:

```dotenv
MINITOS_ALLOWED_HOSTS=localhost,127.0.0.1,minitos,192.168.1.8
OES_ALLOWED_HOSTS=localhost,127.0.0.1,terminal-oes,192.168.1.8
GODOT_PLATFORM=linux/amd64
SIMULATOR_LOCATION_ID=1
SIMULATOR_TERMINAL_CODE=terminal-1
SIMULATOR_MINITOS_USERNAME=simulator
SIMULATOR_MINITOS_PASSWORD=<strong-local-password>
```

The terminal code must match MiniTOS's current `terminal-{location_id}` event
mapping. The first headless image build downloads the Godot 4.2.1 Linux binary.

## 3. Start data and application services

```bash
docker compose config --quiet
docker compose up --build --detach --wait \
  hivemq minitos-db minitos minitos-uns-consumer minitos-outbox \
  oes-db terminal-oes oes-uns-consumer oes-outbox
docker compose ps
```

Create the initial administrator accounts:

```bash
docker compose exec minitos python manage.py createsuperuser
docker compose exec terminal-oes python manage.py createsuperuser
```

MiniTOS automatically creates or reconciles the non-staff simulator account
from `SIMULATOR_MINITOS_USERNAME` and `SIMULATOR_MINITOS_PASSWORD` before its
web process becomes healthy. The password in `.env` is authoritative and is
reapplied after a change or container restart. It can also be reconciled
manually after editing `.env`:

```bash
docker compose up --build --detach --force-recreate --wait minitos
make bootstrap-simulator-user
```

The headless simulator uses Django Basic Authentication only for reading
terminal configuration; it sends execution state through MQTT.

## 4. Start Godot

```bash
docker compose up --build --detach godot-simulator
docker compose logs --follow --tail=200 godot-simulator
```

A healthy startup reports MiniTOS authentication, loaded CHE records, Sparkplug
NBIRTH and the OES command subscription. The process retries MiniTOS and MQTT
connections every five seconds after an outage.

## 5. Access from the development computer

- MiniTOS: `http://192.168.1.8:8000/`
- OES equipment console: `http://192.168.1.8:8010/equipment/`
- OES health: `http://192.168.1.8:8010/health/`
- MQTT: `192.168.1.8:1883`

Allow TCP ports `8000`, `8010` and `1883` through the Linux firewall only for
the local subnet. Do not forward these ports from the internet.

## Operations

```bash
docker compose ps
docker compose logs --follow --tail=200
docker compose restart godot-simulator
docker compose pull --ignore-buildable
git pull --ff-only
docker compose up --build --detach --wait
```

## Diagnose a restart loop

Start with the first unhealthy dependency rather than the downstream services:

```bash
sudo make diagnose
sudo docker compose logs --timestamps --tail=200 hivemq
sudo docker inspect terminal-scms-hivemq-1 \
  --format 'exit={{.State.ExitCode}} error={{.State.Error}} oom={{.State.OOMKilled}}'
```

If HiveMQ is restarting, connection errors from the MiniTOS and OES MQTT
workers are consequences rather than separate failures. The workers wait for a
healthy broker during Compose startup and retry if the broker is temporarily
unavailable later.

HiveMQ CE enables remote JMX by default. Terminal SCMS does not use JMX and
sets `HIVEMQ_JMX_ENABLED=false`. This avoids a JVM startup failure seen on
Docker hosts where Java cannot resolve the generated container hostname:

```text
AgentConfigurationError: java.net.MalformedURLException:
Unable to resolve hostname or get valid IP address
```

After updating the repository, recreate the broker and then the dependent
services so the new environment is applied:

```bash
git pull --ff-only
sudo docker compose up --detach --force-recreate --wait hivemq
sudo docker compose up --build --detach --wait
sudo docker compose ps
```

The HiveMQ log should no longer include the `jmxremote` properties in
`JAVA_OPTS`, and the container should become healthy on port `1883`.

HiveMQ CE 2026.5 runs as user ID `10000`. If its log specifically reports a
permission error under `/opt/hivemq/data` or `/opt/hivemq/log`, inspect the
named-volume ownership before changing anything:

```bash
sudo docker compose run --rm --no-deps --entrypoint sh hivemq \
  -c 'id; ls -ldn /opt/hivemq/data /opt/hivemq/log'
```

Only when that output confirms the directories are not writable by user
`10000`, repair their ownership and restart the broker:

```bash
sudo docker compose stop hivemq
sudo docker compose run --rm --no-deps --user root --entrypoint sh hivemq \
  -c 'chown -R 10000:0 /opt/hivemq/data /opt/hivemq/log'
sudo docker compose up --detach --wait hivemq
sudo docker compose up --detach --wait
```

Do not remove the named volumes as a general troubleshooting step. They contain
broker and database state and are not required to be deleted to collect logs.

Named volumes retain both PostgreSQL databases, HiveMQ data and the Godot
Sparkplug birth/death sequence. Back up the database volumes before migrations
or host replacement.

## Current limitation

The OES console's manual override changes its dispatch projection for debugging.
It does not yet issue a state command to Godot, so the next simulator observation
will replace the override. Runtime control should become an explicit,
acknowledged command rather than a hidden bidirectional field synchronization.
