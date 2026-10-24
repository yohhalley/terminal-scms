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
SIMULATOR_RUNNER_TOKEN=<strong-runner-token>
SIMULATOR_OES_URL=http://terminal-oes:8000
RUNNER_IDENTITY=linux-server-01
```

Generate the runner token on the Linux host rather than using the placeholder:

```bash
openssl rand -hex 32
nano .env
```

Paste the generated 64-character value after `SIMULATOR_RUNNER_TOKEN=` without
angle brackets, quotes or spaces. For example, the resulting line has this
shape:

```dotenv
SIMULATOR_RUNNER_TOKEN=8f1c...the-rest-of-the-generated-value
```

This is a shared authentication secret between the OES web container and the
host-side runner. Do not publish it or commit `.env`. If the token is added or
changed after OES is already running, recreate the OES services so they receive
the new environment value:

```bash
docker compose up --detach --force-recreate \
  terminal-oes oes-uns-consumer oes-outbox
```

An existing `.env` is intentionally not replaced by `git pull`. After upgrades,
compare it with `.env.example` for newly introduced settings.

The first headless image build downloads the Godot 4.2.1 Linux binary. There is
no global terminal ID or MiniTOS simulator account: OES supplies the selected
Site UUID and a short-lived session token for each on-demand process.

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

Create a terminal or inland-depot Location and its Site in MiniTOS. Publish a
valid layout after defining routing and CHE configuration. The Site-created and
configuration-published events make the corresponding OES projection ready.
Verify it at `http://192.168.1.8:8010/simulations/`.

## 4. Build Godot and start the restricted runner

```bash
make build-simulator
make test-runner
make run-simulator-runner
```

`make run-simulator-runner` sources `.env` into the host process. It exits with
`SIMULATOR_RUNNER_TOKEN is required` when that file does not contain the token.
The runner sends it in `X-Runner-Token`; OES accepts claims only when the value
matches its own `SIMULATOR_RUNNER_TOKEN` setting.

Run the last command as a supervised host service for unattended use. The
runner accepts only OES-created session identities, a fixed image, and an
environment allowlist. OES never receives the Docker socket.

Start a ready Site from the OES simulation console. A healthy session moves
from pending to running after Godot retrieves its frozen OES bootstrap,
connects to HiveMQ, publishes NBIRTH and the expected CHE DBIRTH messages, and
subscribes to NCMD/DCMD. Other Sites remain inactive. Stopping the session
terminates only that Site's Godot process.

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
make test-runner
make build-simulator
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

Named volumes retain both PostgreSQL databases and HiveMQ data. Each claimed
simulation also receives a session-specific Docker volume containing its
Sparkplug birth/death sequence and command recovery state. Back up database
volumes before migrations or host replacement. Remove a stopped session volume
only after its restart and audit value is no longer needed.

## Current limitation

The OES console's manual override changes its dispatch projection for debugging.
It does not yet issue a state command to Godot, so the next simulator observation
will replace the override. Runtime control should become an explicit,
acknowledged command rather than a hidden bidirectional field synchronization.
