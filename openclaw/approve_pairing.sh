#!/bin/sh
# Aprueba las solicitudes de pairing de Telegram SOLO del dueño (su id de Telegram).
# Si alguien más le escribe a los bots, su solicitud se ignora. Uso: make pairing
set -eu
OWNER="${1:-}"
if [ -z "$OWNER" ]; then
  echo "Falta TELEGRAM_OWNER_ID en .env (tu id de Telegram; aparece en el mensaje de pairing)."
  exit 1
fi

for dir in /config-src/agentes/*/; do
  account=$(basename "$dir")
  codes=$(openclaw pairing list telegram --account "$account" --json 2>/dev/null | node -e '
    let raw = "";
    process.stdin.on("data", (d) => (raw += d)).on("end", () => {
      const data = JSON.parse(raw.slice(raw.indexOf("{")));
      console.log(data.requests.filter((r) => r.id === process.argv[1]).map((r) => r.code).join(" "));
    });' "$OWNER")
  if [ -z "$codes" ]; then
    echo "[$account] sin solicitudes de $OWNER"
    continue
  fi
  for code in $codes; do
    if openclaw pairing approve telegram "$code" --account "$account" --notify >/dev/null 2>&1; then
      echo "[$account] aprobado"
    else
      echo "[$account] no se pudo aprobar"
    fi
  done
done
