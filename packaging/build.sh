#!/usr/bin/env bash
# Builds a single-file claudeaibridge executable for the current platform.
#
# PyInstaller doesn't cross-compile -- this only produces a binary for
# whatever OS/architecture it's run on. Building for Linux, macOS, and
# Windows requires running this on each of those platforms separately.
#
# Requires the 'build' extra: pip install -e '.[build]'
set -euo pipefail

cd "$(dirname "$0")/.."

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)
output_name="claudeaibridge-${os}-${arch}"

pyinstaller --onefile --name "$output_name" \
  --distpath dist --workpath build/work --specpath build \
  --paths . \
  --copy-metadata fastmcp --copy-metadata fastmcp-slim \
  --collect-data pyfiglet \
  packaging/entrypoint.py

echo
echo "Built: dist/$output_name"
