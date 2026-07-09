#!/usr/bin/env bash
set -euo pipefail

VERSION="${NEXPLANE_VERSION}"   # injected by Packer as env var
GHCR_TOKEN="${NEXPLANE_GHCR_TOKEN}"  # injected by Packer as env var

# Install Docker CE
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Enable Docker
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# Log in to GHCR
echo "${GHCR_TOKEN}" | docker login ghcr.io -u youbetyourballs --password-stdin

# Pull backend image
docker pull "ghcr.io/youbetyourballs/nexplane-backend:${VERSION}"

# Build production webserver image from source (already checked out by Packer)
cd /tmp/nexplane-src
docker build \
  -f frontend/Dockerfile.prod \
  --build-arg VITE_API_URL=/api \
  -t "nexplane-webserver:${VERSION}" \
  ./frontend

# Log out of GHCR (don't leave credentials on AMI)
docker logout ghcr.io

# Write production compose with real version
mkdir -p /opt/nexplane
sed "s/NEXPLANE_VERSION/${VERSION}/g" /tmp/nexplane-src/packer/docker-compose.ami.yml \
  > /opt/nexplane/docker-compose.ami.yml

# Install systemd unit
cp /tmp/nexplane-src/packer/scripts/nexplane.service /etc/systemd/system/nexplane.service
systemctl daemon-reload
systemctl enable nexplane.service

# Write MOTD
cat > /etc/motd <<'MOTD'

  ███╗   ██╗███████╗██╗  ██╗██████╗ ██╗      █████╗ ███╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██╔══██╗██║     ██╔══██╗████╗  ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██████╔╝██║     ███████║██╔██╗ ██║█████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██╔═══╝ ██║     ██╔══██║██║╚██╗██║██╔══╝
  ██║ ╚████║███████╗██╔╝ ██╗██║     ███████╗██║  ██║██║ ╚████║███████╗
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝

  Platform URL:  http://<this-instance-public-or-private-ip>/
  Default email: admin@nexplane.local
  Default pass:  changeme

  Change your password immediately after first login.
  Run: sudo docker compose -f /opt/nexplane/docker-compose.ami.yml ps
  to check platform status.

MOTD

echo "Nexplane AMI setup complete — version ${VERSION}"
