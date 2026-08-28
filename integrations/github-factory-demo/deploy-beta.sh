#!/usr/bin/env bash
# Deploy reviewed GitHub Factory demo source to the beta control plane and build
# its app images in the managed node's loopback registry. Runtime secrets are
# provisioned separately by provision-beta.py; this script never reads them.
set -euo pipefail

CP_HOST=${BARISTA_BETA_CP_HOST:-46.225.59.43}
NODE_HOST=${BARISTA_BETA_NODE_HOST:-88.99.166.242}
SSH_KEY=${BARISTA_BETA_SSH_KEY:-"$HOME/.ssh/barista_hetzner"}
KNOWN_HOSTS=${BARISTA_BETA_KNOWN_HOSTS:-"$HOME/.ssh/known_hosts.barista-deploy"}
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
FACTORY_TAG=${BARISTA_FACTORY_BETA_TAG:-0.5.4}
WORKER_TAG=${BARISTA_GITHUB_WORKER_BETA_TAG:-0.1.0}

# Refusal must precede SSH, rsync, Docker, and every production side effect.
if [ -n "$(git -C "$REPO_DIR" status --porcelain --untracked-files=normal)" ]; then
  echo "ERROR: refusing to deploy a dirty checkout" >&2
  exit 1
fi
DEPLOY_REV=$(git -C "$REPO_DIR" rev-parse HEAD)
ORIGIN=$(git -C "$REPO_DIR" config --get remote.origin.url)
REMOTE_MAIN=$(git ls-remote --exit-code "$ORIGIN" refs/heads/main | awk '{print $1}')
if [ -z "$REMOTE_MAIN" ] || [ "$DEPLOY_REV" != "$REMOTE_MAIN" ]; then
  echo "ERROR: refusing to deploy HEAD=$DEPLOY_REV; remote main=$REMOTE_MAIN" >&2
  exit 1
fi
[ -f "$SSH_KEY" ] || { echo "ERROR: missing SSH key: $SSH_KEY" >&2; exit 2; }
mkdir -p "$(dirname "$KNOWN_HOSTS")"
touch "$KNOWN_HOSTS"
chmod 600 "$KNOWN_HOSTS"
SSHOPT=(-i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN_HOSTS" -o ConnectTimeout=10)

copy_source() {
  local host=$1
  ssh "${SSHOPT[@]}" root@"$host" 'install -d -m 0755 /opt/barista-apps'
  # Additive by design. Operator state and forward-version files are not ours to
  # infer disposable; secret/state paths are excluded even if added locally.
  rsync -az \
    --exclude '.git' --exclude '.venv' --exclude '**/.venv' \
    --exclude '.pytest_cache' --exclude '**/.pytest_cache' \
    --exclude '__pycache__' --exclude '**/__pycache__' --exclude '*.pyc' \
    --exclude '.env' --exclude '**/.env' --exclude '*.sqlite3' \
    --exclude 'github-factory-results' \
    -e "ssh ${SSHOPT[*]}" "$REPO_DIR"/ root@"$host":/opt/barista-apps/
}

echo "== copy reviewed source to beta hosts =="
copy_source "$CP_HOST"
copy_source "$NODE_HOST"

echo "== build and publish digest-pinned app images on managed node =="
ssh "${SSHOPT[@]}" root@"$NODE_HOST" \
  "DEPLOY_REV='$DEPLOY_REV' FACTORY_TAG='$FACTORY_TAG' WORKER_TAG='$WORKER_TAG' bash -s" <<'REMOTE'
set -euo pipefail
cd /opt/barista-apps
REGISTRY=127.0.0.1:5000
curl -fsS "http://$REGISTRY/v2/" >/dev/null

docker build -f apps/factory/Dockerfile -t "$REGISTRY/barista-factory:$FACTORY_TAG" .
docker push "$REGISTRY/barista-factory:$FACTORY_TAG"
docker build -f apps/github-issue-worker/Dockerfile -t "$REGISTRY/barista-github-issue-worker:$WORKER_TAG" .
docker push "$REGISTRY/barista-github-issue-worker:$WORKER_TAG"

registry_digest() {
  local repository=$1 tag=$2 digest
  digest=$(curl -fsSI \
    -H 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    "http://$REGISTRY/v2/$repository/manifests/$tag" \
    | tr -d '\r' | awk 'tolower($1)=="docker-content-digest:" {print $2}' | tail -1)
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: registry returned invalid digest for $repository:$tag" >&2
    exit 1
  }
  printf '%s' "$digest"
}
FACTORY_DIGEST=$(registry_digest barista-factory "$FACTORY_TAG")
WORKER_DIGEST=$(registry_digest barista-github-issue-worker "$WORKER_TAG")
python3 - "$DEPLOY_REV" "$FACTORY_TAG" "$FACTORY_DIGEST" "$WORKER_TAG" "$WORKER_DIGEST" <<'PY'
import json
import os
import sys
from pathlib import Path

revision, factory_tag, factory_digest, worker_tag, worker_digest = sys.argv[1:]
document = {
    "schema_version": "v1alpha1",
    "source_revision": revision,
    "factory": {
        "image": f"127.0.0.1:5000/barista-factory:{factory_tag}",
        "digest": factory_digest,
    },
    "worker": {
        "image": f"127.0.0.1:5000/barista-github-issue-worker:{worker_tag}",
        "digest": worker_digest,
    },
}
path = Path("/opt/barista-apps/.github-factory-images.json")
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
os.replace(temporary, path)
PY
REMOTE

echo "== install hardened controller service on control plane =="
ssh "${SSHOPT[@]}" root@"$CP_HOST" "DEPLOY_REV='$DEPLOY_REV' bash -s" <<'REMOTE'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null
cd /opt/barista-apps/integrations/github-factory-demo
uv sync --frozen
id barista >/dev/null 2>&1 || useradd --system --home-dir /opt/barista-apps --shell /usr/sbin/nologin barista
install -d -o barista -g barista -m 0700 /var/lib/barista-github-factory-demo
install -d -o root -g root -m 0700 /etc/barista
chown -R barista:barista /opt/barista-apps

cat > /etc/systemd/system/barista-github-factory-demo.service <<'SVC'
[Unit]
Description=Barista signed GitHub issue-to-Factory controller
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/barista/github-factory-demo.env

[Service]
User=barista
Group=barista
Type=simple
WorkingDirectory=/var/lib/barista-github-factory-demo
EnvironmentFile=/etc/barista/github-factory-demo.env
Environment=BARISTA_GITHUB_DEMO_DB=/var/lib/barista-github-factory-demo/deliveries.sqlite3
Environment=BARISTA_GITHUB_DEMO_RESULTS=/var/lib/barista-github-factory-demo/results
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/barista-apps/integrations/github-factory-demo/.venv/bin/barista-github-demo serve --host 127.0.0.1 --port 8098
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
ReadWritePaths=/var/lib/barista-github-factory-demo

[Install]
WantedBy=multi-user.target
SVC
systemctl daemon-reload
systemctl enable barista-github-factory-demo.service >/dev/null
if [ -f /etc/barista/github-factory-demo.env ]; then
  chmod 600 /etc/barista/github-factory-demo.env
  chown root:root /etc/barista/github-factory-demo.env
  systemctl restart barista-github-factory-demo.service
  for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:8098/healthz >/dev/null && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:8098/healthz >/dev/null
else
  echo "controller environment not provisioned; unit installed but not started"
fi
printf '%s\n' "$DEPLOY_REV" > /opt/barista-apps/.deployed-revision.tmp
chown barista:barista /opt/barista-apps/.deployed-revision.tmp
mv /opt/barista-apps/.deployed-revision.tmp /opt/barista-apps/.deployed-revision
REMOTE

echo "== deployed image identities =="
ssh "${SSHOPT[@]}" root@"$NODE_HOST" cat /opt/barista-apps/.github-factory-images.json
echo "source_revision=$DEPLOY_REV"
echo "controller=https://github-factory.beta.barista.sh/healthz"
