#!/bin/sh

REMOTE_HOST="debian-dockerserver"
REMOTE_USER="henninb"
REMOTE_DIR="/home/${REMOTE_USER}/ecobee-manager"
IMAGE_NAME="ecobee-manager"
CONTAINER_NAME="ecobee-manager"

log() {
  echo "$(date +"%Y-%m-%d %H:%M:%S") - $*"
}

log_error() {
  echo "$(date +"%Y-%m-%d %H:%M:%S") - ERROR: $*" >&2
}

# Sync project files to remote host
# Exclude ecobee_jwt.json and logs/ to preserve remote state across deploys
log "=== Syncing project files to ${REMOTE_HOST}:${REMOTE_DIR} ==="
ssh "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/logs ${REMOTE_DIR}/config && touch ${REMOTE_DIR}/ecobee_jwt.json"
rsync -av \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='logs/' \
  --exclude='ecobee_jwt.json' \
  ./ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

# Build and run on remote host
log "=== Building and deploying container on ${REMOTE_HOST} ==="
ssh -T "${REMOTE_USER}@${REMOTE_HOST}" REMOTE_DIR="${REMOTE_DIR}" IMAGE_NAME="${IMAGE_NAME}" CONTAINER_NAME="${CONTAINER_NAME}" 'bash -s' << 'ENDSSH'
set -e

cd "${REMOTE_DIR}"

echo "Removing existing container..."
podman rm -f "${CONTAINER_NAME}" 2>/dev/null || true

echo "Removing old image..."
podman rmi "${IMAGE_NAME}" 2>/dev/null || true

echo "Building new image..."
podman build -t "${IMAGE_NAME}" .

echo "Starting container..."
podman run --detach \
  --name="${CONTAINER_NAME}" \
  --hostname="${CONTAINER_NAME}" \
  --env-file="${REMOTE_DIR}/env.secrets" \
  --env CHECK_INTERVAL_MINUTES=40 \
  --env LOG_LEVEL=INFO \
  --env SELENIUM_TIMEOUT=30 \
  --env SELENIUM_REDIRECT_TIMEOUT=60 \
  --volume "${REMOTE_DIR}/ecobee_jwt.json:/app/ecobee_jwt.json" \
  --volume "${REMOTE_DIR}/logs:/app/logs" \
  --volume "${REMOTE_DIR}/config:/app/config:ro" \
  --userns=keep-id \
  --shm-size=2g \
  "${IMAGE_NAME}"

podman ps -a

echo "Writing systemd Quadlet for auto-start on boot..."
mkdir -p ~/.config/containers/systemd
cat > ~/.config/containers/systemd/${CONTAINER_NAME}.container << EOF
[Unit]
Description=Ecobee Manager
After=network-online.target

[Container]
Image=localhost/${IMAGE_NAME}
ContainerName=${CONTAINER_NAME}
HostName=${CONTAINER_NAME}
EnvironmentFile=${REMOTE_DIR}/env.secrets
Environment=CHECK_INTERVAL_MINUTES=40
Environment=LOG_LEVEL=INFO
Environment=SELENIUM_TIMEOUT=30
Environment=SELENIUM_REDIRECT_TIMEOUT=60
Volume=${REMOTE_DIR}/ecobee_jwt.json:/app/ecobee_jwt.json
Volume=${REMOTE_DIR}/logs:/app/logs
Volume=${REMOTE_DIR}/config:/app/config:ro
UserNS=keep-id
ShmSize=2g

[Service]
Restart=always
TimeoutStartSec=120

[Install]
WantedBy=default.target
EOF

# Reload user systemd instance if accessible (not available in non-login SSH sessions)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}
systemctl --user daemon-reload 2>/dev/null || true

echo "Quadlet written to ~/.config/containers/systemd/${CONTAINER_NAME}.container"
echo "NOTE: Run 'sudo loginctl enable-linger ${USER}' on this host to enable auto-start on reboot."
ENDSSH

log "=== Deployment complete ==="
exit 0
