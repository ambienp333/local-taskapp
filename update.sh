#!/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

echo "[update] pulling latest code..."
git pull

echo "[update] installing any new dependencies..."
pip install -q -r server/requirements.txt

echo "[update] done. restart the server to apply changes."
