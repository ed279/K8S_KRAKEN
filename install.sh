#!/bin/bash
set -e

echo "=== K8S_KRAKEN Installation ==="

# Create secrets from Vault
echo "[1/3] Creating Kraken API secrets..."
KRAKEN_KEY=$(vault kv get -field=api_key secret/kraken/api-key)
TG_TOKEN=$(vault kv get -field=bot_token secret/telegram/bot-token)

kubectl create secret generic kraken-secrets \
  -n default \
  --from-literal=api-key="$KRAKEN_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic telegram-secrets \
  -n default \
  --from-literal=bot-token="$TG_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy
echo "[2/3] Deploying Kraken bot instances..."
kubectl apply -f k8s/kraken-deployment.yaml

# Wait for all to be ready
echo "[3/3] Waiting for all Kraken instances..."
kubectl wait --for=condition=ready pod -l app=kraken-bot -n default --timeout=180s

echo "✅ Kraken deployment complete"
kubectl get pods -n default -l app=kraken-bot
