#!/bin/sh
# Alt dizin (APP_BASE_PATH): / -> kok; aksi halde /REL/ altinda PHP on yuz.
set -e
BP="${1:-/}"
REL=$(echo "$BP" | sed 's|^/||;s|/$||')
mkdir -p /etc/nginx/snippets
DEST=/etc/nginx/snippets/php-app-prefix.conf
if [ -z "$REL" ] || [ "$BP" = "/" ] || [ "$BP" = "./" ]; then
  printf '%s\n' '# PHP: kok dizin (APP_BASE_PATH=/)' >"$DEST"
else
  cat >"$DEST" <<EOF
location = / {
  return 302 /${REL}/;
}
location /${REL}/ {
  root /usr/share/nginx/html;
  try_files \$uri \$uri/ /${REL}/index.php?\$query_string;
}
EOF
fi
