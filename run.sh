#!/usr/bin/env bash
set -euo pipefail

IMAGE="ecobee-temperature-manager"
CONTAINER="ecobee-temperature-manager"

ECOBEE_EMAIL=$(gopass show ecobee/email)
ECOBEE_PASSWORD=$(gopass show ecobee/password)

# Ensure host-side files and directories exist
touch ecobee_jwt.json
mkdir -p logs config

# Stop and remove existing container if running
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "Stopping and removing existing container: ${CONTAINER}"
  docker rm -f "${CONTAINER}"
fi

# Build the image
echo "Building image: ${IMAGE}"
docker build --network=host -t "${IMAGE}" .

# Run the container
echo "Starting container: ${CONTAINER}"
docker run -d \
  --name "${CONTAINER}" \
  --restart unless-stopped \
  --user "$(id -u):$(id -g)" \
  -p 8080:8080 \
  --env ECOBEE_EMAIL="${ECOBEE_EMAIL}" \
  --env ECOBEE_PASSWORD="${ECOBEE_PASSWORD}" \
  --env CHECK_INTERVAL_MINUTES="${CHECK_INTERVAL_MINUTES:-40}" \
  --env LOG_LEVEL="${LOG_LEVEL:-INFO}" \
  --env SELENIUM_TIMEOUT="${SELENIUM_TIMEOUT:-30}" \
  --env SELENIUM_REDIRECT_TIMEOUT="${SELENIUM_REDIRECT_TIMEOUT:-60}" \
  -v "$(pwd)/ecobee_jwt.json:/app/ecobee_jwt.json" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/config:/app/config:ro" \
  "${IMAGE}"

echo "Container started. Logs:"
docker logs -f "${CONTAINER}"
