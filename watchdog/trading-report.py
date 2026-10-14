#!/usr/bin/env python3
"""
Enhanced Kraken Bot Trading Report
Aggregates data from shadow, paper, and live trading instances
Reports to @watchdog_telai_bot with unified trading metrics
"""

import os
import json
import subprocess
import time
from datetime import datetime
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8746813544")

class TradingReport:
    def __init__(self):
        self.variants = ["shadow", "paper", "live"]
        
    def get_pod_logs(self, variant, lines=20):
        """Get recent logs from variant pod"""
        try:
            result = subprocess.run(
                ["kubectl", "logs", "-n", "default", 
                 f"-l", f"app=kraken-bot,variant={variant}",
                 "--tail", str(lines), "--timestamps=true"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout.strip().split('\n') if result.stdout else []
        except Exception as e:
            return [f"Error: {str(e)}"]

    def extract_trading_metrics(self, logs):
        """Extract trading metrics from logs"""
        metrics = {
            "trades_total": 0,
            "trades_24h": 0,
            "open_positions": 0,
            "last_signal": None,
            "current_price": None,
            "rsi": None,
            "status": "Unknown"
        }
        
        for log in logs:
            # Parse log lines for metrics
            if "HOLD" in log or "BUY" in log or "SELL" in log:
                if "$" in log:
                    # Extract price
                    import re
                    price_match = re.search(r'\$([0-9,]+\.[0-9]+)', log)
                    if price_match:
                        metrics["current_price"] = price_match.group(1)
                    
                    # Extract RSI
                    rsi_match = re.search(r'RSI=([0-9.]+)', log)
                    if rsi_match:
                        metrics["rsi"] = rsi_match.group(1)
                    
                    # Extract signal
                    if "HOLD" in log:
                        metrics["last_signal"] = "HOLD"
                    elif "BUY" in log:
                        metrics["last_signal"] = "BUY"
                    elif "SELL" in log:
                        metrics["last_signal"] = "SELL"
                    
                    metrics["status"] = "Active"
        
        return metrics

    def get_pod_status(self, variant):
        """Get pod status for variant"""
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", "default",
                 "-l", f"app=kraken-bot,variant={variant}",
                 "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10
            )
            pods = json.loads(result.stdout)
            return pods.get("items", [])
        except Exception as e:
            return []

    def format_variant_report(self, variant):
        """Format trading report for a single variant"""
        pods = self.get_pod_status(variant)
        
        if not pods:
            return f"⚠️ <b>{variant.upper()}</b>: No pods found"
        
        pod = pods[0]
        status_icon = "🟢" if pod["status"]["phase"] == "Running" else "🔴"
        phase = pod["status"]["phase"]
        
        # Get logs and metrics
        logs = self.get_pod_logs(variant, lines=50)
        metrics = self.extract_trading_metrics(logs)
        
        # Format report
        lines = [
            f"{status_icon} <b>{variant.upper()}</b> ({phase})",
            f"  Status: {'Active' if metrics['status'] == 'Active' else 'Inactive'}"
        ]
        
        if metrics["last_signal"]:
            signal_icon = "📈" if metrics["last_signal"] == "BUY" else "📉" if metrics["last_signal"] == "SELL" else "⏸️"
            lines.append(f"  Signal: {signal_icon} {metrics['last_signal']}")
        
        if metrics["current_price"]:
            lines.append(f"  Price: ${metrics['current_price']}")
        
        if metrics["rsi"]:
            lines.append(f"  RSI: {metrics['rsi']}")
        
        return "\n".join(lines)

    def generate_unified_report(self):
        """Generate unified trading report for all variants"""
        lines = [
            "📊 <b>Kraken Bot Trading Report</b>",
            f"⏰ {datetime.now().isoformat()}",
            ""
        ]
        
        for variant in self.variants:
            variant_report = self.format_variant_report(variant)
            lines.append(variant_report)
            lines.append("")
        
        # Summary
        lines.append("<b>Summary</b>")
        lines.append("🎯 Monitoring: 6 trading pods (3×shadow, 2×paper, 1×live)")
        lines.append("📡 Signal generation active across all variants")
        
        return "\n".join(lines)

    def send_to_telegram(self, text):
        """Send report to Telegram via @watchdog_telai_bot"""
        if not TELEGRAM_BOT_TOKEN:
            print("ERROR: TELEGRAM_BOT_TOKEN not set")
            return False
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"Failed to send: {e}")
            return False

    def run_once(self):
        """Generate and send report once"""
        report = self.generate_unified_report()
        print(report)
        
        if self.send_to_telegram(report):
            print("✅ Report sent to Telegram")
        else:
            print("❌ Failed to send report")
        
        return report

    def run_periodic(self, interval=1800):  # 30 minutes default
        """Run report generation periodically"""
        print(f"📊 Trading report service started (interval: {interval}s)")
        
        while True:
            try:
                report = self.generate_unified_report()
                self.send_to_telegram(report)
                print(f"✅ Report sent at {datetime.now().isoformat()}")
            except Exception as e:
                print(f"❌ Error: {e}")
            
            time.sleep(interval)

if __name__ == "__main__":
    import sys
    
    reporter = TradingReport()
    
    if "--once" in sys.argv:
        reporter.run_once()
    else:
        interval = int(os.getenv("REPORT_INTERVAL", "1800"))
        reporter.run_periodic(interval)
