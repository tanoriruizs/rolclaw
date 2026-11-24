#!/bin/sh
# Arranque de OpenClaw: copia la config y los agentes del repo (solo lectura) a su carpeta
# de estado y luego arranca normal. OpenClaw necesita escribir junto a su config, y así
# el repo sigue siendo la fuente de verdad: cada reinicio vuelve a la versión del repo.
set -eu
SRC=/config-src
STATE=/home/node/.openclaw

install -m 600 "$SRC/openclaw.json5" "$STATE/openclaw.json5"
for dir in "$SRC"/agentes/*/; do
  role=$(basename "$dir")
  mkdir -p "$STATE/workspace-$role"
  install -m 644 "$SRC/agentes/AGENTS.md" "$STATE/workspace-$role/AGENTS.md"
  install -m 644 "$dir/SOUL.md" "$STATE/workspace-$role/SOUL.md"
  if [ -f "$dir/USER.md" ]; then
    install -m 644 "$dir/USER.md" "$STATE/workspace-$role/USER.md"
  fi
done
mkdir -p "$STATE/credentials"
chmod 700 "$STATE/credentials"

exec tini -s -- node /app/docker-entrypoint.mjs "$@"
