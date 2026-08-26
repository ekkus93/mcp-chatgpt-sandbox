# Default development container image (§9.1).
#
# Deliberately does not bake in a specific uid — the container user is
# created at container *start* by entrypoint.sh, to match the checkout
# owner's uid/gid (§5.3), which varies by host and is only known when the
# daemon actually launches the container.

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

COPY entrypoint.sh /usr/local/bin/mcp-entrypoint.sh
RUN chmod 0755 /usr/local/bin/mcp-entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/mcp-entrypoint.sh"]
CMD ["sleep", "infinity"]
