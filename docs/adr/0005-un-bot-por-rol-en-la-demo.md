# ADR 0005: Un bot por rol en la demo

**Estado:** aceptada (para la demo; el modo directorio del
[ADR 0007](0007-identidad-por-persona.md) cubre el caso de un solo bot)

## Contexto

La demo muestra cuatro roles. En producción, cada persona usaría un solo bot y su rol
saldría del directorio de la empresa. Pero eso requiere un proveedor de identidad y varias
cuentas de Telegram para grabar la demo.

## Decisión

Cuatro bots de Telegram, uno por rol. Cada cuenta se enlaza en OpenClaw (`bindings`) con un
agente, y cada agente se conecta al servidor MCP con el token de su rol (cuatro servidores
MCP con la misma URL y distinto token; cada agente solo puede usar el suyo). El acceso a
los bots se protege con *pairing*.

## Alternativas descartadas

- **Un solo bot con comandos para cambiar de rol.** Cualquiera podría elegir el rol: rompe
  el principio de permisos.
- **Un bot con rol por usuario de Telegram.** Es el camino de producción, pero requiere un
  directorio de identidad.

## Consecuencias

- Con una sola cuenta de Telegram se pueden mostrar los cuatro roles.
- El rol pertenece al bot, no a la persona: quien tenga acceso a un bot actúa como su rol.
  Se mitiga con *pairing* y es una limitación documentada en `SECURITY.md`.
- Un solo bot con ruteo por usuario ya existe como modo directorio; lo que queda en la
  hoja de ruta es que el rol venga de Entra ID (SSO).
