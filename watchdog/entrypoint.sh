#!/bin/bash
# Ensure kubectl is available
if ! command -v kubectl &> /dev/null; then
  echo "Installing kubectl..."
  curl -sLO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
  chmod +x kubectl
  mv kubectl /usr/local/bin/
fi

exec python3 /app/trading-report.py "$@"
