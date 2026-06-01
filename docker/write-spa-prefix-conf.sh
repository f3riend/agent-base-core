#!/bin/sh
# VITE_BASE_PATH alt dizininde SPA + / kokunden yonlendirme (Docker nginx).
# NOT: conf.d/*.conf ana nginx.conf icinde http{} altinda include edilir; location{}
# yalnizca server{} icinde gecerli. Bu yuzden snippet conf.d disinda /etc/nginx/snippets/.
set -e
BP="${1:-/}"
REL=$(echo "$BP" | sed 's|^/||;s|/$||')
mkdir -p /etc/nginx/snippets
DEST=/etc/nginx/snippets/spa-prefix.conf
if [ -z "$REL" ] || [ "$BP" = "/" ] || [ "$BP" = "./" ]; then
  printf '%s\n' '# SPA site kokunde (VITE_BASE_PATH=/)' >"$DEST"
else
  cat >"$DEST" <<EOF
location = / {
  return 302 /${REL}/;
}
location /${REL}/ {
  root /usr/share/nginx/html;
  try_files \$uri \$uri/ /${REL}/index.html;
}
EOF
fi
