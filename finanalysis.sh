#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.conda/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "[ERRORE] Ambiente conda non trovato: $PYTHON"
    echo "Esegui prima: conda create -p .conda python=3.12"
    exit 1
fi

"$PYTHON" "$SCRIPT_DIR/screener.py" "$@"
