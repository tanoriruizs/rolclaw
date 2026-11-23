# ADR 0002: MCP y REST sobre los mismos servicios

**Estado:** aceptada

## Contexto

Los agentes de OpenClaw consumen herramientas por MCP (Model Context Protocol). Las
personas, las pruebas y otros sistemas prefieren una API REST con documentación
interactiva. Mantener dos implementaciones duplicaría la lógica y, peor, los permisos.

## Decisión

La lógica vive una sola vez en `tools/`, una función por herramienta con el decorador
`@tool` (permiso + auditoría). MCP (`/mcp`) y REST (`/api/v1`) son adaptadores delgados:
autentican, llaman a la misma función y traducen errores.

En MCP hay tres capas: 401 sin token válido, `tools/list` filtrado por rol y permiso
revalidado con el header de cada llamada. El servidor MCP es *stateless*: cada petición trae
su token.

## Alternativas descartadas

- **Solo MCP.** Más difícil de probar y de integrar con otros sistemas.
- **Lógica duplicada por transporte.** Tarde o temprano un transporte se salta un permiso.

## Consecuencias

- Un permiso o una regla se cambian en un solo lugar y aplican a los dos transportes.
- Los tests de REST y de MCP ejercitan el mismo código.
- REST sirve para depurar sin Telegram (`http://127.0.0.1:8000/docs`).
