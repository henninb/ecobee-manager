#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# run-k8.sh — Build and deploy ecobee-manager to Kubernetes
# -----------------------------------------------------------------------------
# Prerequisites:
#   - kubectl configured and pointing at your cluster
#   - Docker or Podman available for building the image
#   - ./env.secrets file with ECOBEE_EMAIL and ECOBEE_PASSWORD
#   - config/schedule.json present
# -----------------------------------------------------------------------------

APP_NAME="ecobee-manager"
NAMESPACE="ecobee-manager"
IMAGE="ecobee-manager:latest"
SECRETS_FILE="./env.secrets"
SCHEDULE_FILE="./config/schedule.json"

# --- helpers -----------------------------------------------------------------

info()  { echo "[INFO]  $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found in PATH"; }

# --- preflight ---------------------------------------------------------------

require_cmd kubectl
require_cmd docker || require_cmd podman

[[ -f "$SECRETS_FILE"  ]] || die "Missing $SECRETS_FILE — copy .env.example and fill in credentials"
[[ -f "$SCHEDULE_FILE" ]] || die "Missing $SCHEDULE_FILE"

# --- build image -------------------------------------------------------------

info "Building Docker image: $IMAGE"
if command -v docker &>/dev/null; then
    docker build -t "$IMAGE" .
else
    podman build -t "$IMAGE" .
fi

# Load image into the cluster nodes (works for single-node or kubeadm clusters
# that use containerd — adjust if you use a registry instead).
info "Importing image into cluster nodes via containerd"
if command -v docker &>/dev/null; then
    docker save "$IMAGE" | ssh debian-k8s-cp-01 "sudo ctr -n k8s.io images import -"
    docker save "$IMAGE" | ssh debian-k8s-worker-01 "sudo ctr -n k8s.io images import -" 2>/dev/null || true
else
    podman save "$IMAGE" | ssh debian-k8s-cp-01 "sudo ctr -n k8s.io images import -"
    podman save "$IMAGE" | ssh debian-k8s-worker-01 "sudo ctr -n k8s.io images import -" 2>/dev/null || true
fi

# --- parse secrets -----------------------------------------------------------

info "Reading credentials from $SECRETS_FILE"
ECOBEE_EMAIL=""
ECOBEE_PASSWORD=""

while IFS='=' read -r key value || [[ -n "$key" ]]; do
    # strip leading/trailing whitespace and ignore comments/blanks
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
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${APP_NAME}-data
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi

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
              value: "45"
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
            - name: data
              mountPath: /app/ecobee_jwt.json
              subPath: ecobee_jwt.json
            - name: data
              mountPath: /app/logs
              subPath: logs
            - name: schedule
              mountPath: /app/config
              readOnly: true
          securityContext:
            allowPrivilegeEscalation: false
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: ${APP_NAME}-data
        - name: schedule
          configMap:
            name: ${APP_NAME}-schedule

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

# --- wait for rollout --------------------------------------------------------

info "Waiting for rollout to complete..."
kubectl rollout status deployment/"$APP_NAME" -n "$NAMESPACE" --timeout=120s

info "Deployment complete. Pod status:"
kubectl get pods -n "$NAMESPACE" -o wide

info ""
info "To view logs:   kubectl logs -n $NAMESPACE -l app=$APP_NAME -f"
info "To check health: kubectl port-forward -n $NAMESPACE svc/$APP_NAME 8080:8080 then curl http://localhost:8080/health"
