# syntax=docker/dockerfile:1
# Runner (S2): the simulator runner as a compose service. The runner claims OES
# simulation sessions and runs headless Godot as a child process in this same
# container (RUNNER_LAUNCH_MODE=process), so no Docker socket is mounted
# anywhere. Built by compose with the Godot project as the extra build context
# "godot" and .github/scripts as "ci-scripts" (compose.yaml: additional_contexts).
FROM debian:bookworm-slim

ARG GODOT_VERSION=4.2.1
# SHA-512 of Godot_v4.2.1-stable_linux.x86_64.zip, the same pinned value as
# .github/actions/setup-godot (from the release's SHA512-SUMS.txt). Change both
# together with GODOT_VERSION.
ARG GODOT_SHA512=e835e7a3bcae2033019293beea3bef6d6b2dcaf4b7a266d55c9327bcc84e808b9a0e0a6966379350a6cf3c7c28ec6e4e6a86344eae8ad869f80bae76da3fc855

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip libfontconfig1 python3 \
    && curl -fsSL \
        "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-stable/Godot_v${GODOT_VERSION}-stable_linux.x86_64.zip" \
        -o /tmp/godot.zip \
    && echo "${GODOT_SHA512}  /tmp/godot.zip" | sha512sum --check --strict - \
    && unzip /tmp/godot.zip -d /tmp/godot \
    && install -m 0755 "/tmp/godot/Godot_v${GODOT_VERSION}-stable_linux.x86_64" /usr/local/bin/godot \
    && apt-get purge -y curl unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/godot /tmp/godot.zip

# /app is owned by godot: the import below runs as godot, and Godot writes
# nothing (and still exits 0) into a project dir it can't write.
RUN useradd --create-home --uid 10001 godot \
    && mkdir -p /app /data /opt/runner \
    && chown godot:godot /app /data

WORKDIR /app
COPY --from=godot --chown=godot:godot . /app
COPY --from=ci-scripts godot-import.sh /usr/local/bin/godot-import
COPY runner.py /opt/runner/runner.py

USER godot

# Import the project at build time, so every session starts from the same
# .godot cache. The same script as CI (.github/scripts/godot-import.sh): it
# loops headless editor passes until every *.import target exists and fails
# the build with the missing list otherwise (method from TCL-007, #32).
RUN GODOT_BIN=/usr/local/bin/godot godot-import /app \
    && test "$(stat -c %U /app/.godot)" = godot
ENV HOME=/data \
    PYTHONUNBUFFERED=1 \
    RUNNER_LAUNCH_MODE=process \
    RUNNER_DATA_DIR=/data \
    GODOT_BINARY=/usr/local/bin/godot \
    GODOT_PROJECT_PATH=/app
VOLUME ["/data"]

CMD ["python3", "/opt/runner/runner.py"]
