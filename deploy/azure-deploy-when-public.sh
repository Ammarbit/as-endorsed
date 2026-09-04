#!/usr/bin/env sh
# Wait until ghcr.io/ammarbit/as-endorsed:latest is publicly pullable, then create (or update)
# the Azure Container App from it. Safe to re-run.
set -u
export MSYS_NO_PATHCONV=1   # Git Bash on Windows would otherwise rewrite /data into C:/Program Files/Git/data
IMAGE_PATH="ammarbit/as-endorsed"
IMAGE="ghcr.io/$IMAGE_PATH:latest"
RG="as-endorsed-rg"; APP="as-endorsed"; ENV="as-endorsed-env"; LOC="germanywestcentral"
LOG="${1:-/dev/stdout}"

is_public() {
  tok=$(curl -s "https://ghcr.io/token?scope=repository:$IMAGE_PATH:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  [ -n "$tok" ] || return 1
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $tok" \
    -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json" \
    "https://ghcr.io/v2/$IMAGE_PATH/manifests/latest")
  [ "$code" = "200" ]
}

i=0
until is_public; do
  i=$((i+1)); [ $i -gt 240 ] && { echo "gave up after 2 hours; package still private" >> "$LOG"; exit 2; }
  sleep 30
done
echo "package is public; deploying $IMAGE" >> "$LOG"

if az containerapp show -n "$APP" -g "$RG" -o none 2>/dev/null; then
  az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" --cpu 1.0 --memory 2.0Gi --min-replicas 0 --max-replicas 1 >> "$LOG" 2>&1
else
  az containerapp create -n "$APP" -g "$RG" --environment "$ENV" --image "$IMAGE" \
    --ingress external --target-port 8000 --cpu 1.0 --memory 2.0Gi --min-replicas 0 --max-replicas 1 \
    --env-vars PORT=8000 AS_ENDORSED_DATA_DIR=/data FASTEMBED_CACHE_PATH=/data/models >> "$LOG" 2>&1
fi
FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo "url=https://$FQDN" >> "$LOG"
for _ in $(seq 1 40); do
  if curl -sf "https://$FQDN/api/health" >/dev/null 2>&1; then echo "healthy" >> "$LOG"; curl -s "https://$FQDN/api/health" >> "$LOG"; echo >> "$LOG"; exit 0; fi
  sleep 10
done
echo "app created but health check did not pass within 400s; check: az containerapp logs show -n $APP -g $RG" >> "$LOG"
exit 3
