#!/bin/sh
# Vite ciktisini nginx root altinda dogru dizine koyar (VITE_BASE_PATH=/posting/agent-base/ ise).
set -e
SRC="${1:-/app/dist}"
OUT="${2:-/app/nginx-html}"
BP="${VITE_BASE_PATH:-/}"
REL=$(echo "$BP" | sed 's|^/||;s|/$||')
mkdir -p "$OUT"
if [ -z "$REL" ] || [ "$BP" = "/" ] || [ "$BP" = "./" ]; then
  cp -r "$SRC"/* "$OUT/"
else
  mkdir -p "$OUT/$REL"
  cp -r "$SRC"/* "$OUT/$REL/"
fi
