# Default development container image (§9.1).
#
# Deliberately does not bake in a specific uid — the container user is
# created at container *start* by entrypoint.sh, to match the checkout
# owner's uid/gid (§5.3), which varies by host and is only known when the
# daemon actually launches the container.
#
# G2R-003 (`docs/GAP2_SPEC.md`): the actual ENTRYPOINT is
# `entrypoint-wrapper.py`, not `entrypoint.sh` — the wrapper strips
# interpreter-control environment state (`BASH_ENV`/`ENV`/an exported
# shell function) before `entrypoint.sh`'s root phase can run at all,
# since bash imports that state at its own process startup, before even
# `entrypoint.sh`'s first line executes. See the wrapper's own docstring
# for why this step is Python, not another layer of shell.

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
    passwd \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/mcp-entrypoint-real.sh
COPY entrypoint-wrapper.py /usr/local/bin/mcp-entrypoint-wrapper.py
RUN chmod 0755 /usr/local/bin/mcp-entrypoint-real.sh /usr/local/bin/mcp-entrypoint-wrapper.py

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/mcp-entrypoint-wrapper.py"]
CMD ["sleep", "infinity"]
