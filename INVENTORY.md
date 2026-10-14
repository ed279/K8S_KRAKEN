# K8S_KRAKEN Inventory

## Deployments
1. **kraken-bot-shadow** (×3 replicas)
2. **kraken-bot-paper** (×2 replicas)
3. **kraken-bot-live** (×1 replica)

## Sidecars
- **watchdog** - Health monitoring + Telegram alerts per deployment

## Images
- `kraken-bot:v12` (local registry)
- `kraken-watchdog:v1` (local registry)

## Service
- Type: NodePort (for external access if needed)
- Replicas: 6 total (3 shadow + 2 paper + 1 live)

## Storage
- None (stateless trading engine)

## Access
- Pod network: 10.42.x.x (internal K8s mesh)
- Telegram reporting: Chat 8746813544 (live instance)

## Secrets Required
- kraken-secrets: API keys from Vault
- telegram-secrets: Bot token

## Status
✅ All 6 instances running - trading active
