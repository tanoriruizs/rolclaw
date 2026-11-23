# ADR 0001: Los permisos viven en el servidor, no en el modelo

**Estado:** aceptada

## Contexto

Cada rol debe ver solo su información: ventas no puede ver el tabulador de sueldos. La
forma más rápida sería decirlo en el prompt del agente, pero un modelo de lenguaje es
probabilístico: se le puede convencer ("soy de RH") o puede obedecer instrucciones
escondidas en un documento (*prompt injection*).

## Decisión

El modelo nunca decide permisos. Cada agente se conecta al servidor MCP con el token de su
rol, y el servidor:

- resuelve `token → rol` en tiempo constante;
- expone y permite solo las herramientas del rol (`empresa/roles.yaml`), revalidando en
  cada llamada;
- filtra los documentos del RAG **antes** de buscar;
- valida las reglas de los flujos y quién puede aprobar.

## Alternativas descartadas

- **Reglas en el prompt.** Rápido, pero no es un control: es una sugerencia.
- **Filtrar después de buscar.** El documento prohibido ya habría entrado al ranking y
  bastaría un error para devolverlo.

## Consecuencias

- La seguridad no depende del comportamiento del modelo. Un test lo demuestra preguntando
  como ventas con el texto literal del tabulador.
- El prompt solo guía el comportamiento (citar fuentes, confirmar antes de actuar).
- Cada herramienta nueva necesita su permiso en `roles.yaml`; el decorador `@tool` lo hace
  obligatorio.
