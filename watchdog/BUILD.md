# Kraken Watchdog Reporter

## Build
```bash
docker build -t kraken-watchdog:v2-reporter watchdog/
```

## Deploy
```bash
# Ensure telegram-secrets exists
kubectl create secret generic telegram-secrets \
  -n default \
  --from-literal=bot-token='<TELEGRAM_BOT_TOKEN>'

# Deploy watchdog
kubectl apply -f k8s/kraken-watchdog-deployment.yaml
```

## Features
- **Unified Reporting**: Monitors all three Kraken instances (shadow, paper, live)
- **Telegram Bot Integration**: Reports to @watchdog_telai_bot
- **Pod Health Monitoring**: Tracks status, restarts, and readiness
- **Configurable Intervals**: Default 5 minutes (configurable via ConfigMap)
- **RBAC Secured**: Minimal permissions for pod listing

## Report Format
```
🤖 Kraken Bot Watchdog Report
⏰ <timestamp>
⌚ Uptime: <duration>

✅ SHADOW: 3/3 running
  🟢 kraken-bot-shadow-abc123: Running
  🟢 kraken-bot-shadow-def456: Running
  🟢 kraken-bot-shadow-ghi789: Running

✅ PAPER: 2/2 running
  🟢 kraken-bot-paper-jkl012: Running
  🟢 kraken-bot-paper-mno345: Running

✅ LIVE: 1/1 running
  🟢 kraken-bot-live-pqr678: Running

Overall: ✅ HEALTHY (6/6 pods)
```

## Telegram Bot Target
Reports sent to: **@watchdog_telai_bot**
Chat ID: **8746813544** (ED's personal chat)
