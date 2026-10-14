# Enhanced Trading Report

## Features

### Unified Multi-Variant Reporting
- **Shadow Trading**: 3 instances (backtesting strategies, no real money)
- **Paper Trading**: 2 instances (simulated trading, real market data)
- **Live Trading**: 1 instance (real money trading)

### Metrics Tracked
- Trading signals (BUY/SELL/HOLD)
- Current price (from order books)
- RSI indicator values
- Pod health status
- Active position count
- Recent activity timestamps

### Telegram Integration
- Reports to **@watchdog_telai_bot**
- HTML formatting with emoji status indicators
- Configurable report intervals (default: 30 minutes)
- Automatic error alerts

## Report Format

```
📊 Kraken Bot Trading Report
⏰ 2026-10-14T05:56:16.295700+00:00

🟢 SHADOW (Running)
  Status: Active
  Signal: ⏸️ HOLD
  Price: $83,375.00
  RSI: 55.85

🟢 PAPER (Running)
  Status: Active
  Signal: 📈 BUY
  Price: $83,375.00
  RSI: 55.85

🟢 LIVE (Running)
  Status: Active
  Signal: ⏸️ HOLD
  Price: $83,375.00
  RSI: 55.85

Summary
🎯 Monitoring: 6 trading pods (3×shadow, 2×paper, 1×live)
📡 Signal generation active across all variants
```

## Deployment

### Build
```bash
docker build -t kraken-trading-report:v1 watchdog/
```

### Deploy as K8s CronJob
```bash
kubectl apply -f k8s/trading-report-cronjob.yaml
```

Or continuous reporting:
```bash
kubectl apply -f k8s/trading-report-deployment.yaml
```

## Environment Variables

- `TELEGRAM_BOT_TOKEN`: Bot token for @watchdog_telai_bot
- `TELEGRAM_CHAT_ID`: Target chat (default: 8746813544)
- `REPORT_INTERVAL`: Report frequency in seconds (default: 1800 = 30 min)

## Testing

```bash
# Single report
python3 watchdog/trading-report.py --once

# Continuous reporting (for 5 minutes)
timeout 300 python3 watchdog/trading-report.py
```
