FROM ubuntu:24.04

ARG ANSIBLE_CORE_VERSION=2.20.7
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git gzip iputils-ping jq openssh-client openssl \
       python3 python3-pip python3-venv tar unzip wget \
    && python3 -m venv /opt/rancher-automation/ansible-venv \
    && /opt/rancher-automation/ansible-venv/bin/pip install --no-cache-dir \
       "ansible-core==${ANSIBLE_CORE_VERSION}" paramiko \
    && (apt-mark hold tzdata >/dev/null 2>&1 || true) \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="Rancher RKE2 automation control container" \
      org.opencontainers.image.source="https://github.com/YangXTao/rancher-rke2-plugin"

CMD ["sleep", "infinity"]
