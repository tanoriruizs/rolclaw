# ADR 0007: Identidad por persona (modo directorio)

**Estado:** aceptada (convive con el modo demo del [ADR 0005](0005-un-bot-por-rol-en-la-demo.md))

## Contexto

En la demo el rol pertenece al bot: quien entra al bot de Ventas *es* Pedro. Dos vendedores
reales compartirían identidad, solicitudes y saldo de vacaciones, y la auditoría no sabría
quién hizo qué.

## Decisión

Un segundo modo, `AUTH_MODE=directorio`:

- **En el servidor**, cada empleado tiene su propio token y su rol sale de
  `empresa/empleados.yaml`. El token es `HMAC-SHA256(DIRECTORY_TOKEN_SECRET, id del empleado)`:
  no hay que guardar una tabla de tokens y el secreto maestro nunca sale del servidor.
- **En OpenClaw**, un solo bot. `make directorio` genera un agente y un servidor MCP por
  persona, y un *binding* que une cada chat de Telegram con su agente. Solo pueden escribir
  los ids de `TELEGRAM_USERS` (`dmPolicy: allowlist`).
- OpenClaw recibe únicamente los tokens ya derivados de quienes usan el bot, nunca el
  secreto con el que se podría fabricar el de cualquier empleado.

## Alternativas descartadas

- **Pasar el id de Telegram al servidor en cada llamada.** El dato vendría del gateway y el
  modelo podría influir en él; el token por agente no depende del modelo.
- **Guardar un token aleatorio por empleado.** Más piezas (alta, baja, almacenamiento) para
  la misma garantía en una plantilla.
- **Un agente "sin acceso" por defecto.** OpenClaw lo rechaza con `ownership: "explicit"`, y
  no hace falta: una conversación sin *binding* no se atiende.

## Consecuencias

- Dos personas con el mismo rol ya no se ven entre sí, y la auditoría nombra a la persona.
- La config de OpenClaw de este modo se genera (`openclaw/local/`, fuera del repo) y hay que
  regenerarla cuando cambian `TELEGRAM_USERS` o `empresa/`; `make up` avisa si quedó vieja.
- Rotar `DIRECTORY_TOKEN_SECRET` cambia todos los tokens a la vez; no hay revocación
  individual (para dar de baja a alguien se le quita de `TELEGRAM_USERS` y de
  `empleados.yaml`).
- **Lo que no se probó:** el camino completo con varias cuentas reales de Telegram. Sí se
  probó con tests la cadena chat → agente → token → persona, y la config generada se validó
  con `openclaw config validate`.
- Producción (hoja de ruta): que el directorio sea el de la empresa (SSO con Entra ID).
