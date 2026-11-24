#!/bin/sh
# Arranque de OpenClaw: copia la config y los agentes del repo (solo lectura) a su carpeta
# de estado y luego arranca normal. OpenClaw necesita escribir junto a su config, y así
# el repo sigue siendo la fuente de verdad: cada reinicio vuelve a la versión del repo.
set -eu
SRC="${OPENCLAW_SRC:-/config-src}"
STATE="${OPENCLAW_STATE:-/home/node/.openclaw}"

# demo: un agente por rol (config del repo). directorio: uno por persona (make directorio).
if [ "${AUTH_MODE:-demo}" = "directorio" ]; then
  CONFIG="$SRC/local/openclaw.json5"
  AGENTS="$SRC/local/agentes"
  if [ ! -f "$CONFIG" ]; then
    echo "Falta openclaw/local/openclaw.json5: ejecuta make directorio." >&2
    exit 1
  fi
else
  CONFIG="$SRC/openclaw.json5"
  AGENTS="$SRC/agentes"
fi

install -m 600 "$CONFIG" "$STATE/openclaw.json5"
for dir in "$AGENTS"/*/; do
  agent=$(basename "$dir")
  mkdir -p "$STATE/workspace-$agent"
  install -m 644 "$SRC/agentes/AGENTS.md" "$STATE/workspace-$agent/AGENTS.md"
  install -m 644 "$dir/SOUL.md" "$STATE/workspace-$agent/SOUL.md"
  if [ -f "$dir/USER.md" ]; then
    install -m 644 "$dir/USER.md" "$STATE/workspace-$agent/USER.md"
  fi
done
mkdir -p "$STATE/credentials"
chmod 700 "$STATE/credentials"

exec tini -s -- node /app/docker-entrypoint.mjs "$@"
