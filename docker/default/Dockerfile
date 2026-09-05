# Default development container image (§9.1).
#
# Deliberately does not bake in a specific uid — the container user is
# created at container *start* by entrypoint.sh, to match the checkout
# owner's uid/gid (§5.3), which varies by host and is only known when the
# daemon actually launches the container.
#
# G2R-003 (`docs/GAP2_SPEC.md`): the privileged root phase must not start
# under Bash before Bash startup controls have been stripped. The Python
# wrapper sanitizes that Bash-specific state before execing entrypoint.sh.
#
# G3R-001 / G3AT-001 (`docs/GAP3_SPEC.md`): Python itself has an earlier startup
# boundary. In normal mode `PYTHONPATH` can make `sitecustomize.py` execute
# before the wrapper reaches its first line, while the container is still
# root. The real ENTRYPOINT therefore invokes the absolute interpreter with
# `-I`: isolated mode ignores `PYTHON*` environment state and unsafe import
# search paths. This flag is load-bearing; do not replace this array with a
# direct invocation of the wrapper script or a PATH-resolved interpreter.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    git \
    curl \
    wget \
    ca-certificates \
    build-essential \
    pkg-config \
    unzip \
    zip \
    jq \
    python3 \
    tar \
    gzip \
    sudo \
    openssh-client \
    openssh-server \
    passwd \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/mcp-entrypoint-real.sh
COPY entrypoint-wrapper.py /usr/local/bin/mcp-entrypoint-wrapper.py
RUN chmod 0755 /usr/local/bin/mcp-entrypoint-real.sh /usr/local/bin/mcp-entrypoint-wrapper.py

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/python3", "-I", "/usr/local/bin/mcp-entrypoint-wrapper.py"]
CMD ["sleep", "infinity"]
