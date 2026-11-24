# ADR 0009: Evals que juzgan por los efectos en el servidor

**Estado:** aceptada

## Contexto

Los tests no llaman al modelo: prueban el servidor. Faltaba saber si, después de cambiar un
prompt, un flujo o el modelo, **los agentes** siguen haciendo lo correcto. Probarlo a mano
en Telegram es lento y no se repite igual dos veces.

## Decisión

`make eval` conversa con cada agente (`openclaw agent`, sin Telegram) y califica **hechos**,
no texto:

- qué solicitudes existen después de la conversación y en qué estado (API REST);
- qué herramientas se ejecutaron de verdad (auditoría del servidor);
- solo como apoyo, palabras que deben o no deben aparecer en la respuesta.

Por defecto un escenario no puede crear nada: cualquier solicitud de más es un fallo. Un
intento que el servidor deniega no cuenta como "usar" una herramienta prohibida: es el
permiso funcionando.

## Alternativas descartadas

- **Otro modelo como juez.** Añade costo y un segundo componente probabilístico para
  responder preguntas que el servidor ya contesta con certeza.
- **Comparar la respuesta con un texto esperado.** El modelo redacta distinto cada vez.

## Consecuencias

- Un agente que *dice* "listo, creé la solicitud" sin haberla creado reprueba.
- Gasta créditos y depende del modelo, así que no corre en la CI; el arnés sí tiene tests
  (un agente guionado contra el servidor real en memoria).
- Los escenarios deben ser inequívocos: con dos solicitudes pendientes, "aprueba la de
  Pedro" hace que el agente pregunte cuál en vez de aprobar.
- Los escenarios comparten datos; `make eval` empieza borrando solicitudes y auditoría.
