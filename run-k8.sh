#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# run-k8.sh — Build and deploy ecobee-manager to Kubernetes
# -----------------------------------------------------------------------------
# Prerequisites:
#   - kubectl configured and pointing at your cluster
#   - SSH access to the worker node (debian-k8s-worker-01)
#   - Docker or Podman available for building the image
#   - ./env.secrets file with ECOBEE_EMAIL and ECOBEE_PASSWORD
#   - config/schedule.json present
#
# Storage strategy: hostPath volumes on debian-k8s-worker-01
#   /opt/ecobee-manager/ecobee_jwt.json  — JWT token (persists across restarts)
#   /opt/ecobee-manager/logs/            — rotating logs
#
# Note: subPath file mounts (PVC) are broken on this cluster's runc version;
#       hostPath is used instead and the pod is pinned to the worker.
# -----------------------------------------------------------------------------

APP_NAME="ecobee-manager"
NAMESPACE="default"
IMAGE_TAG=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
IMAGE="${APP_NAME}:${IMAGE_TAG}"
WORKER_NODE="debian-k8s-worker-01"
HOST_DATA_DIR="/opt/ecobee-manager"
SECRETS_FILE="./env.secrets"
SCHEDULE_FILE="./config/schedule.json"

# --- helpers -----------------------------------------------------------------

info()  { echo "[INFO]  $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found in PATH"; }

# --- preflight ---------------------------------------------------------------

require_cmd kubectl
command -v docker &>/dev/null || command -v podman &>/dev/null || die "docker or podman is required"

[[ -f "$SECRETS_FILE"  ]] || die "Missing $SECRETS_FILE — copy .env.example and fill in credentials"
[[ -f "$SCHEDULE_FILE" ]] || die "Missing $SCHEDULE_FILE"

# --- build image -------------------------------------------------------------

info "Building Docker image: $IMAGE (tag: $IMAGE_TAG)"
if command -v docker &>/dev/null; then
    docker build -t "$IMAGE" .
else
    podman build -t "$IMAGE" .
fi

# Load image into the cluster nodes via containerd (no registry needed).
info "Importing image into cluster nodes via containerd"
if command -v docker &>/dev/null; then
    docker save "$IMAGE" | ssh debian-k8s-cp-01  "sudo ctr -n k8s.io images import -"
    docker save "$IMAGE" | ssh "$WORKER_NODE"    "sudo ctr -n k8s.io images import -"
else
    podman save "$IMAGE" | ssh debian-k8s-cp-01  "sudo ctr -n k8s.io images import -"
    podman save "$IMAGE" | ssh "$WORKER_NODE"    "sudo ctr -n k8s.io images import -"
fi

# --- parse secrets -----------------------------------------------------------

info "Reading credentials from $SECRETS_FILE"
ECOBEE_EMAIL=""
ECOBEE_PASSWORD=""

while IFS='=' read -r key value || [[ -n "$key" ]]; do
    key="${key#"${key%%[! ]*}"}"
    key="${key%"${key##*[! ]}"}"
    [[ -z "$key" || "$key" == \#* ]] && continue
    value="${value#"${value%%[! ]*}"}"
    value="${value%"${value##*[! ]}"}"
    case "$key" in
        ECOBEE_EMAIL)    ECOBEE_EMAIL="$value"    ;;
        ECOBEE_PASSWORD) ECOBEE_PASSWORD="$value" ;;
    esac
done < "$SECRETS_FILE"

[[ -n "$ECOBEE_EMAIL"    ]] || die "ECOBEE_EMAIL not found in $SECRETS_FILE"
[[ -n "$ECOBEE_PASSWORD" ]] || die "ECOBEE_PASSWORD not found in $SECRETS_FILE"

SCHEDULE_JSON=$(cat "$SCHEDULE_FILE")

# --- storage class (needed only if PVCs are used elsewhere) ------------------

if ! kubectl get storageclass local-path &>/dev/null; then
    info "Installing local-path-provisioner (default storage class)..."
    kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.30/deploy/local-path-storage.yaml
    kubectl patch storageclass local-path -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
    kubectl rollout status deployment/local-path-provisioner -n local-path-storage --timeout=60s
fi

# --- prepare hostPath dirs on the worker ------------------------------------
# FileOrCreate/DirectoryOrCreate only work if the parent dir already exists.

info "Creating hostPath directories on $WORKER_NODE..."
ssh "$WORKER_NODE" "
    sudo mkdir -p ${HOST_DATA_DIR}/logs &&
    sudo touch ${HOST_DATA_DIR}/ecobee_jwt.json &&
    sudo chown -R 1000:1000 ${HOST_DATA_DIR}
"

# --- apply manifests ---------------------------------------------------------

info "Applying Kubernetes manifests to namespace: $NAMESPACE"

kubectl apply -f - <<EOF
---
apiVersion: v1
kind: Namespace
metadata:
  name: $NAMESPACE

---
apiVersion: v1
kind: Secret
metadata:
  name: ${APP_NAME}-credentials
  namespace: $NAMESPACE
type: Opaque
stringData:
  ECOBEE_EMAIL: "$ECOBEE_EMAIL"
  ECOBEE_PASSWORD: "$ECOBEE_PASSWORD"

---
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${APP_NAME}-schedule
  namespace: $NAMESPACE
data:
  schedule.json: |
$(echo "$SCHEDULE_JSON" | sed 's/^/    /')

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $APP_NAME
  namespace: $NAMESPACE
  labels:
    app: $APP_NAME
spec:
  replicas: 1
  selector:
    matchLabels:
      app: $APP_NAME
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: $APP_NAME
    spec:
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      nodeSelector:
        kubernetes.io/hostname: $WORKER_NODE
      containers:
        - name: $APP_NAME
          image: $IMAGE
          imagePullPolicy: Never
          env:
            - name: ECOBEE_EMAIL
              valueFrom:
                secretKeyRef:
                  name: ${APP_NAME}-credentials
                  key: ECOBEE_EMAIL
            - name: ECOBEE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ${APP_NAME}-credentials
                  key: ECOBEE_PASSWORD
            - name: TZ
              value: "America/Chicago"
            - name: CHECK_INTERVAL_MINUTES
              value: "40"
            - name: LOG_LEVEL
              value: "INFO"
            - name: SELENIUM_TIMEOUT
              value: "30"
            - name: SELENIUM_REDIRECT_TIMEOUT
              value: "60"
          ports:
            - containerPort: 8080
              name: health
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 60
            timeoutSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 30
            timeoutSeconds: 10
          resources:
            requests:
              cpu: "100m"
              memory: "512Mi"
            limits:
              cpu: "500m"
              memory: "1Gi"
          volumeMounts:
            - name: jwt
              mountPath: /app/ecobee_jwt.json
            - name: logs
              mountPath: /app/logs
            - name: schedule
              mountPath: /app/config
              readOnly: true
            - name: dshm
              mountPath: /dev/shm
          securityContext:
            allowPrivilegeEscalation: false
      volumes:
        - name: jwt
          hostPath:
            path: ${HOST_DATA_DIR}/ecobee_jwt.json
            type: FileOrCreate
        - name: logs
          hostPath:
            path: ${HOST_DATA_DIR}/logs
            type: DirectoryOrCreate
        - name: schedule
          configMap:
            name: ${APP_NAME}-schedule
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 2Gi

---
apiVersion: v1
kind: Service
metadata:
  name: $APP_NAME
  namespace: $NAMESPACE
  labels:
    app: $APP_NAME
spec:
  selector:
    app: $APP_NAME
  ports:
    - name: health
      port: 8080
      targetPort: 8080
  type: ClusterIP
EOF

# --- force rollout -----------------------------------------------------------

info "Restarting deployment to pick up new image (${IMAGE})..."
kubectl rollout restart deployment/"$APP_NAME" -n "$NAMESPACE"

# --- wait for rollout --------------------------------------------------------

info "Waiting for rollout to complete..."
kubectl rollout status deployment/"$APP_NAME" -n "$NAMESPACE" --timeout=120s

info "Deployment complete. Pod status:"
kubectl get pods -n "$NAMESPACE" -o wide

info ""
info "To view logs:    kubectl logs -n $NAMESPACE -l app=$APP_NAME -f"
info "To check health: kubectl port-forward -n $NAMESPACE svc/$APP_NAME 8080:8080"
info "                 then: curl http://localhost:8080/health"
info "Data on worker:  ssh $WORKER_NODE ls -la $HOST_DATA_DIR"
