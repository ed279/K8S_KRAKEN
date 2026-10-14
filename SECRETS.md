# K8S_KRAKEN Secrets

## Required Kubernetes Secrets

### kraken-secrets
```bash
kubectl create secret generic kraken-secrets \
  -n default \
  --from-literal=api-key='<kraken-api-key-from-vault>'
```

Source: `secret/kraken/api-key` in Vault

### telegram-secrets
```bash
kubectl create secret generic telegram-secrets \
  -n default \
  --from-literal=bot-token='<telegram-bot-token>'
```

Source: `secret/telegram/bot-token` in Vault

## Installation Steps

1. Create secrets:
   ```bash
   KRAKEN_KEY=$(vault kv get -field=api_key secret/kraken/api-key)
   TG_TOKEN=$(vault kv get -field=bot_token secret/telegram/bot-token)
   
   kubectl create secret generic kraken-secrets \
     -n default \
     --from-literal=api-key="$KRAKEN_KEY"
   
   kubectl create secret generic telegram-secrets \
     -n default \
     --from-literal=bot-token="$TG_TOKEN"
   ```

2. Deploy:
   ```bash
   kubectl apply -f k8s/kraken-deployment.yaml
   ```

3. Verify:
   ```bash
   kubectl get pods -n default | grep kraken
   kubectl logs -n default -l app=kraken-bot
   ```
