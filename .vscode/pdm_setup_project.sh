#!/usr/bin/env bash
set -euo pipefail

REPO_FOLDER="$(cd "$(dirname "$0")/.." && pwd)"
echo "Repository directory: ${REPO_FOLDER}"
cd "${REPO_FOLDER}"

if ! command -v pdm >/dev/null 2>&1; then
    echo "'pdm' not found. Installing via official installer..."
    curl -sSL https://pdm-project.org/install-pdm.py | python3 -
    export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v pdm >/dev/null 2>&1; then
    echo "'pdm' still not on PATH. Please install it manually: https://pdm-project.org/"
    exit 1
fi

echo "Installing dev dependencies..."
pdm install --group dev

echo "Vendoring Qt into the plugin..."
pdm run python dev.py vendor

echo "Installing pre-commit hooks..."
pdm run pre-commit install

echo
echo "Setup complete. Next:"
echo "  1. Select the .venv interpreter in VS Code (Python: Select Interpreter)."
echo "  2. Use 'Register Plugin (junction)' or 'Register Python Path' to point a UE project at this repo."
