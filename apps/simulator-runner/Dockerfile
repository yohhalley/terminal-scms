# syntax=docker/dockerfile:1
# Runner (S2): the simulator runner as a compose service. The runner claims OES
# simulation sessions and runs headless Godot as a child process in this same
# container (RUNNER_LAUNCH_MODE=process), so no Docker socket is mounted
# anywhere. Built by compose with the Godot project as the extra build context
# "godot" (compose.yaml: additional_contexts).
FROM debian:bookworm-slim

ARG GODOT_VERSION=4.2.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip libfontconfig1 python3 \
    && curl -fsSL \
        "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-stable/Godot_v${GODOT_VERSION}-stable_linux.x86_64.zip" \
        -o /tmp/godot.zip \
    && unzip /tmp/godot.zip -d /tmp/godot \
    && install -m 0755 "/tmp/godot/Godot_v${GODOT_VERSION}-stable_linux.x86_64" /usr/local/bin/godot \
    && apt-get purge -y curl unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/godot /tmp/godot.zip

RUN useradd --create-home --uid 10001 godot \
    && mkdir -p /app /data /opt/runner \
    && chown godot:godot /data

WORKDIR /app
COPY --from=godot --chown=godot:godot . /app
COPY runner.py /opt/runner/runner.py

USER godot
ENV HOME=/data \
    PYTHONUNBUFFERED=1 \
    RUNNER_LAUNCH_MODE=process \
    RUNNER_DATA_DIR=/data \
    GODOT_BINARY=/usr/local/bin/godot \
    GODOT_PROJECT_PATH=/app
VOLUME ["/data"]

CMD ["python3", "/opt/runner/runner.py"]
