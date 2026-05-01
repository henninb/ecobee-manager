#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
PROXMOX_HOST="proxmox.bhenning.com"
LXC_ID=501
LXC_HOSTNAME="ecobee-manager"
LXC_CORES=1
LXC_MEMORY=512
LXC_DISK=8
LXC_STORAGE="local"
LXC_BRIDGE="vmbr0"
APP_DIR="/opt/ecobee-manager"
STAGE_DIR="/tmp/ecobee-stage-${LXC_ID}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="ecobee-manager"
IMAGE_NAME="ecobee-manager"
PROXMOX_SSH="root@${PROXMOX_HOST}"

log() { echo "[$(date '+%H:%M:%S')] ==> $*"; }

# --- Preflight ---
if [[ ! -f "${LOCAL_DIR}/env.secrets" ]]; then
  echo "ERROR: env.secrets not found at ${LOCAL_DIR}/env.secrets"
  exit 1
fi

# --- Find or download a Debian template ---
log "Looking for Debian template on Proxmox..."
TEMPLATE=$(ssh "${PROXMOX_SSH}" "pveam list local 2>/dev/null | grep -E 'debian-13|debian-12' | sort -rV | head -1 | awk '{print \$1}'" || true)

if [[ -z "$TEMPLATE" ]]; then
  log "No Debian template cached locally. Updating template list..."
  ssh "${PROXMOX_SSH}" "pveam update"
  TMPL_NAME=$(ssh "${PROXMOX_SSH}" "pveam available --section system 2>/dev/null | awk '{print \$2}' | grep -E 'debian-13|debian-12' | sort -rV | head -1" || true)
  if [[ -z "$TMPL_NAME" ]]; then
    echo "ERROR: No Debian 12 or 13 template available for download."
    exit 1
  fi
  log "Downloading template: ${TMPL_NAME}..."
  ssh "${PROXMOX_SSH}" "pveam download local ${TMPL_NAME}"
  TEMPLATE=$(ssh "${PROXMOX_SSH}" "pveam list local | grep -E 'debian-13|debian-12' | sort -rV | head -1 | awk '{print \$1}'")
fi
log "Using template: ${TEMPLATE}"

# --- Create LXC 501 if it does not exist ---
if ! ssh "${PROXMOX_SSH}" "pct list | awk '{print \$1}' | grep -qw ${LXC_ID}"; then
  log "Creating LXC ${LXC_ID} (${LXC_HOSTNAME})..."
  ssh "${PROXMOX_SSH}" "pct create ${LXC_ID} ${TEMPLATE} \
    --hostname ${LXC_HOSTNAME} \
    --cores ${LXC_CORES} \
    --memory ${LXC_MEMORY} \
    --rootfs ${LXC_STORAGE}:${LXC_DISK} \
    --net0 name=eth0,bridge=${LXC_BRIDGE},ip=dhcp \
    --unprivileged 0 \
    --features nesting=1 \
    --onboot 1"
else
  log "LXC ${LXC_ID} already exists, skipping creation."
fi

# --- Ensure LXC is running ---
LXC_STATUS=$(ssh "${PROXMOX_SSH}" "pct status ${LXC_ID} | awk '{print \$2}'")
if [[ "$LXC_STATUS" != "running" ]]; then
  log "Starting LXC ${LXC_ID}..."
  ssh "${PROXMOX_SSH}" "pct start ${LXC_ID}"
  log "Waiting for LXC to boot..."
  sleep 8
fi

# --- Install Podman inside LXC ---
log "Installing Podman inside LXC (this may take a minute)..."
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- bash -c 'apt-get update -qq && apt-get install -y -qq podman 2>&1 | tail -3'"

# --- Stage files on Proxmox host, then push into LXC ---
log "Syncing project files to Proxmox staging area..."
ssh "${PROXMOX_SSH}" "mkdir -p ${STAGE_DIR}/config"
rsync -az --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='logs/' \
  --exclude='ecobee_jwt.json' \
  --exclude='data/' \
  "${LOCAL_DIR}/" \
  "${PROXMOX_SSH}:${STAGE_DIR}/"

log "Preparing app directories inside LXC..."
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- bash -c 'mkdir -p ${APP_DIR}/logs ${APP_DIR}/config && touch ${APP_DIR}/ecobee_jwt.json'"

log "Pushing source files into LXC..."
for f in Dockerfile requirements.txt ecobee_auth_jwt.py ecobee_service.py \
          health_server.py schedule_engine.py secrets_loader.py \
          temperature_controller.py env.secrets; do
  ssh "${PROXMOX_SSH}" "test -f ${STAGE_DIR}/${f} && pct push ${LXC_ID} ${STAGE_DIR}/${f} ${APP_DIR}/${f} || true"
done

log "Pushing config files into LXC..."
ssh "${PROXMOX_SSH}" bash << EOF
for f in ${STAGE_DIR}/config/*; do
  [ -f "\$f" ] && pct push ${LXC_ID} "\$f" ${APP_DIR}/config/\$(basename "\$f")
done
EOF

# Preserve JWT token if it already has content locally
if [[ -s "${LOCAL_DIR}/ecobee_jwt.json" ]]; then
  log "Copying existing JWT token to LXC..."
  rsync -az "${LOCAL_DIR}/ecobee_jwt.json" "${PROXMOX_SSH}:${STAGE_DIR}/ecobee_jwt.json"
  ssh "${PROXMOX_SSH}" "pct push ${LXC_ID} ${STAGE_DIR}/ecobee_jwt.json ${APP_DIR}/ecobee_jwt.json"
fi

# --- Build Podman image inside LXC ---
log "Building Podman image inside LXC..."
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- bash -c 'cd ${APP_DIR} && podman build --network=host -t ${IMAGE_NAME} .'"

# --- Stop existing container if any ---
log "Removing existing container if present..."
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- bash -c 'podman rm -f ${CONTAINER_NAME} 2>/dev/null || true'"

# --- Run container ---
log "Starting Podman container..."
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- bash -c 'podman run --detach \
  --name ${CONTAINER_NAME} \
  --hostname ${CONTAINER_NAME} \
  --env-file ${APP_DIR}/env.secrets \
  --env CHECK_INTERVAL_MINUTES=40 \
  --env LOG_LEVEL=INFO \
  --env SELENIUM_TIMEOUT=30 \
  --env SELENIUM_REDIRECT_TIMEOUT=60 \
  --volume ${APP_DIR}/ecobee_jwt.json:/app/ecobee_jwt.json \
  --volume ${APP_DIR}/logs:/app/logs \
  --volume ${APP_DIR}/config:/app/config:ro \
  --shm-size=2g \
  -p 8080:8080 \
  ${IMAGE_NAME}'"

# --- Write systemd Quadlet for auto-start on boot ---
log "Writing systemd Quadlet for auto-start on boot..."
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- mkdir -p /etc/containers/systemd"
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- bash -c 'cat > /etc/containers/systemd/${CONTAINER_NAME}.container'" << EOF
[Unit]
Description=Ecobee Manager
After=network-online.target

[Container]
Image=localhost/${IMAGE_NAME}
ContainerName=${CONTAINER_NAME}
HostName=${CONTAINER_NAME}
EnvironmentFile=${APP_DIR}/env.secrets
Environment=CHECK_INTERVAL_MINUTES=40
Environment=LOG_LEVEL=INFO
Environment=SELENIUM_TIMEOUT=30
Environment=SELENIUM_REDIRECT_TIMEOUT=60
Volume=${APP_DIR}/ecobee_jwt.json:/app/ecobee_jwt.json
Volume=${APP_DIR}/logs:/app/logs
Volume=${APP_DIR}/config:/app/config:ro
ShmSize=2g
PublishPort=8080:8080

[Service]
Restart=on-failure
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- systemctl daemon-reload"

# --- Cleanup staging area ---
log "Cleaning up Proxmox staging area..."
ssh "${PROXMOX_SSH}" "rm -rf ${STAGE_DIR}"

# --- Final status ---
log "Deployment complete!"
ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- podman ps"
LXC_IP=$(ssh "${PROXMOX_SSH}" "pct exec ${LXC_ID} -- hostname -I | awk '{print \$1}'" 2>/dev/null || echo "unknown")
log "Health endpoint: http://${LXC_IP}:8080/health"
