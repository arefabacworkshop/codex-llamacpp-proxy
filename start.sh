#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

echo "==============================================="
echo "   Codex CLI <-> llama.cpp Namespace Proxy     "
echo "==============================================="
echo " Proxy Endpoint : http://127.0.0.1:8081/v1/   "
echo " Upstream Target: http://127.0.0.1:8080/v1/   "
echo " Press [Ctrl+C] to stop.                      "
echo "==============================================="
echo ""

# Find available python3 or python executable
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$DIR/proxy.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$DIR/proxy.py" "$@"
else
    echo "Error: Python 3 is required but neither 'python3' nor 'python' was found in PATH." >&2
    exit 1
fi
