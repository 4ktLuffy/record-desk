#!/bin/sh
set -eu
cd "$(dirname "$0")"
STATE="${RECEIPT_DESK_STATE:-$HOME/.local/share/record-desk}"
mkdir -p "$STATE"
if [ "$(uname -s)" = Darwin ] && command -v swiftc >/dev/null 2>&1; then
  if [ ! -x "$STATE/ocr" ] || [ ocr.swift -nt "$STATE/ocr" ]; then
    swiftc ocr.swift -o "$STATE/ocr"
  fi
fi
exec python3 server.py "$@"
