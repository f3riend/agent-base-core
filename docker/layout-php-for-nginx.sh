#!/bin/sh
# php-ui/public ciktisini nginx root altina koyar (APP_BASE_PATH=/posting/agent-base/ ise alt klasore).
set -e
SRC="${1:-/app/php-ui/public}"
OUT="${2:-/app/nginx-html}"
BP="${APP_BASE_PATH:-${VITE_BASE_PATH:-/}}"
REL=$(echo "$BP" | sed 's|^/||;s|/$||')
APP_ROOT=$(dirname "$SRC")
mkdir -p "$OUT"
if [ -z "$REL" ] || [ "$BP" = "/" ] || [ "$BP" = "./" ]; then
  cp -r "$SRC"/* "$OUT/"
  DEPLOY_PUBLIC="$OUT"
else
  mkdir -p "$OUT/$REL"
  cp -r "$SRC"/* "$OUT/$REL/"
  DEPLOY_PUBLIC="$OUT/$REL"
fi

# index.php, ../includes ve ../locale'yi bekliyor; bu nedenle public'in ebeveynine
# php-ui'nin kardes klasorlerini de kopyala.
DEPLOY_PARENT=$(dirname "$DEPLOY_PUBLIC")
for d in includes views locale; do
  if [ -d "$APP_ROOT/$d" ]; then
    mkdir -p "$DEPLOY_PARENT/$d"
    cp -r "$APP_ROOT/$d"/. "$DEPLOY_PARENT/$d/"
  fi
done
