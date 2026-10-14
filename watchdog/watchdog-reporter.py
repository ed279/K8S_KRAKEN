#!/usr/bin/env python3
"""
Enhanced Kraken Bot Watchdog Reporter
Monitors shadow, paper, and live instances
Reports to @watchdog_telai_bot Telegram bot
"""

import os
import json
import subprocess
import time
from datetime import datetime, timedelta
import requests

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8746813544")
TELEGRAM_BOT_USERNAME = "@watchdog_telai_bot"

INSTANCES = {
    "shadow": {"replicas": 3, "mode": "shadow_trading"},
    "paper": {"replicas": 2, "mode": "paper_trading"},
    "live": {"replicas": 1, "mode": "live_trading"}
}

class KrakenWatchdog:
    def __init__(self):
        self.start_time = datetime.now()
        self.reports = []

    def get_pod_status(self):
        """Get all Kraken pod status from K8s"""
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", "default", "-l", "app=kraken-bot", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=10
            )
            pods = json.loads(result.stdout)
            return pods.get("items", [])
        except Exception as e:
            return {"error": str(e)}

    def check_instance_health(self, variant, pods):
        """Check health of instance variant"""
        variant_pods = [p for p in pods if p["metadata"]["labels"].get("variant") == variant]
        expected = INSTANCES[variant]["replicas"]
        running = sum(1 for p in variant_pods if p["status"]["phase"] == "Running")
        
        return {
            "variant": variant,
            "expected": expected,
            "running": running,
            "pods": [
                {
                    "name": p["metadata"]["name"],
                    "status": p["status"]["phase"],
                    "restarts": p["status"]["containerStatuses"][0]["restartCount"] if p["status"]["containerStatuses"] else 0,
                    "ready": p["status"]["conditions"][-1]["status"] == "True" if p["status"]["conditions"] else False
                }
                for p in variant_pods
            ]
        }

    def generate_report(self):
        """Generate unified health report"""
        pods = self.get_pod_status()
        
        if isinstance(pods, dict) and "error" in pods:
            return self.format_error_report(pods)
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "uptime": str(datetime.now() - self.start_time).split('.')[0],
            "instances": {}
        }
        
        for variant in INSTANCES.keys():
            report["instances"][variant] = self.check_instance_health(variant, pods)
        
        return report

    def format_telegram_report(self, report):
        """Format report for Telegram @watchdog_telai_bot"""
        if "error" in report:
            return f"⚠️ Watchdog Error: {report['error']}"
        
        lines = [
            f"🤖 <b>Kraken Bot Watchdog Report</b>",
            f"⏰ {report['timestamp']}",
            f"⌚ Uptime: {report['uptime']}",
            ""
        ]
        
        total_running = 0
        total_expected = 0
        
        for variant, data in report["instances"].items():
            running = data["running"]
            expected = data["expected"]
            total_running += running
            total_expected += expected
            
            status = "✅" if running == expected else "⚠️"
            lines.append(f"{status} <b>{variant.upper()}</b>: {running}/{expected} running")
            
            for pod in data["pods"]:
                pod_status = "🟢" if pod["status"] == "Running" else "🔴"
                lines.append(f"  {pod_status} {pod['name']}: {pod['status']}")
                if pod["restarts"] > 0:
                    lines.append(f"     ⚠️ Restarts: {pod['restarts']}")
        
        lines.append("")
        health = "✅ HEALTHY" if total_running == total_expected else "⚠️ DEGRADED"
        lines.append(f"<b>Overall: {health}</b> ({total_running}/{total_expected} pods)")
        
        return "\n".join(lines)

    def format_error_report(self, report):
        """Format error report"""
        return f"❌ <b>Watchdog Error</b>\n{report.get('error', 'Unknown error')}"

    def send_telegram_report(self, text):
        """Send report to Telegram via bot"""
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
            print(f"Failed to send Telegram message: {e}")
            return False

    def run(self, interval_seconds=300):
        """Run watchdog continuously"""
        print(f"🤖 Kraken Watchdog started - reporting every {interval_seconds}s to {TELEGRAM_BOT_USERNAME}")
        
        while True:
            try:
                report = self.generate_report()
                message = self.format_telegram_report(report)
                
                if self.send_telegram_report(message):
                    print(f"✅ Report sent at {datetime.now().isoformat()}")
                else:
                    print(f"❌ Failed to send report")
                
                # Print to stdout for pod logs
                print(message)
                
            except Exception as e:
                print(f"❌ Watchdog error: {e}")
                error_msg = f"❌ <b>Watchdog Error</b>\n{str(e)}"
                self.send_telegram_report(error_msg)
            
            time.sleep(interval_seconds)

if __name__ == "__main__":
    watchdog = KrakenWatchdog()
    interval = int(os.getenv("REPORT_INTERVAL", "300"))  # 5 minutes default
    watchdog.run(interval_seconds=interval)
