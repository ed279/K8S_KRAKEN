# K8S_KRAKEN

Kraken trading bot for CephaloBot, running on Kubernetes with multi-variant support (live, paper, shadow).

## Contents

- `k8s-manifests/` — Kraken bot deployments, RBAC, service definitions
- `kraken-bot/` — Bot source code (modules, strategies, trading logic)
- `scripts/` — Deployment helpers, monitoring scripts

## Key Features

- **Multi-Variant Support:** Live trading (real Kraken API), paper trading (simulation), shadow trading (validation)
- **RBAC Least-Privilege:** Each variant has restricted ServiceAccount + Role
- **Resilience:** Circuit breaker for provider failures, retry logic, health probes
- **Observability:** Prometheus metrics, detailed logging

## Quick Start

```bash
# Deploy all three variants
kubectl apply -f k8s-manifests/

# Check deployment status
kubectl get pods -l app=kraken-bot -o wide

# View trading logs
kubectl logs -n default kraken-live-0 -f
```

## Variants

| Variant | Mode | Risk | Purpose |
|---------|------|------|---------|
| **live** | Real Kraken API | High | Actual trading with real funds |
| **paper** | Simulated trading | None | Test strategies without risk |
| **shadow** | Validation mode | None | Monitor signals vs live (no execute) |

## Configuration

See `k8s-manifests/configmap.yaml` for trading parameters:
- Confidence thresholds
- Stop-loss percentages
- Position sizing
- Max hold times

## RBAC

Each variant runs under its own ServiceAccount with minimal permissions:
- Read own pod identity
- Access trading credentials (Vault token)
- Read strategy configuration (ConfigMap)
- Write trade logs (read/write access to shared volume)

## Related

- Primary repo: [cephalobot-workspace](https://github.com/ed279/cephalobot-workspace)
- Vault config: [vault-config](https://github.com/ed279/vault-config)
- DNS config: [K8S_DNS](https://github.com/ed279/K8S_DNS)
