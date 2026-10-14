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

## Trading Report Service (Multi-Variant Monitoring)

**Purpose:** Continuous monitoring and reporting of all three Kraken bot variants (shadow, paper, live) to Telegram

**Components:**
- `watchdog/trading-report.py` - Unified report generator
- `k8s/trading-report-deployment.yaml` - Continuous reporting service (30min intervals)
- `k8s/trading-report-cronjob.yaml` - Hourly cron-based reporting alternative

**Metrics Reported:**
- Trading signals (BUY/SELL/HOLD) for all variants
- Current price from order books
- RSI indicator values
- Pod health status (Running/Failed)
- Unified summary across all 6 pods (3×shadow + 2×paper + 1×live)

**Deployment:**
- 1 replica trading-report pod (stable)
- RBAC: ServiceAccount + ClusterRole for pod introspection
- ConfigMap: `trading-report-config` (report_interval: 1800s default)
- Secret: `telegram-trading-secret` (bot_token, chat_id)

**Docker Image:**
- Registry: Local K3s containerd
- Name: `kraken-trading-report:v1`
- Base: python:3.11-slim + requests==2.31.0
- Size: ~75MB

**K8s Resources:**
- CPU: 100m request, 500m limit
- Memory: 256Mi request, 512Mi limit

**Output Format:**
```
📊 Kraken Bot Trading Report
⏰ 2026-10-14T05:56:16.295700+00:00

🟢 SHADOW (Running) [3 replicas]
  Status: Active
  Signal: ⏸️ HOLD
  Price: $83,375.00
  RSI: 55.85

🟢 PAPER (Running) [2 replicas]
  Status: Active
  Signal: 📈 BUY
  Price: $83,375.00
  RSI: 55.85

🟢 LIVE (Running) [1 replica]
  Status: Active
  Signal: ⏸️ HOLD
  Price: $83,375.00
  RSI: 55.85

Summary
🎯 Monitoring: 6 trading pods (3×shadow, 2×paper, 1×live)
📡 Signal generation active across all variants
```

**Telegram Configuration:**
- Target: @watchdog_telai_bot
- Chat ID: 8746813544 (ED's direct message)
- Delivery: HTML formatted with emoji status indicators

**Related Services:**
- Kraken Bot Health Watchdog (watchdog-reporter.py) - Pod lifecycle monitoring
- Kraken Bot Deployment (kraken-deployment.yaml) - Core trading services

