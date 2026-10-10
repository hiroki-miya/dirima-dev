#!/usr/bin/env bash
# Run only inside the dedicated DirimaDev WSL development distribution.
set -euo pipefail
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if ! docker info >/dev/null 2>&1; then
  nohup dockerd > /var/log/dirima-docker.log 2>&1 < /dev/null &
  for attempt in {1..30}; do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi
docker version
docker compose version
