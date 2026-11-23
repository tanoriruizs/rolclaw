# Seguridad

Este proyecto es una demostración, pero está diseñado como si fuera a producción: cada
control se puede verificar con un test, con `make sandbox-check` o con
`openclaw security audit`.

## Principio

**El modelo nunca decide permisos.** Un modelo de lenguaje es probabilístico y se le puede
convencer. Por eso los permisos no viven en el prompt sino en el servidor:

1. Cada agente se conecta al servidor MCP con el token de **su** rol.
2. El servidor resuelve `token → rol` comparando en tiempo constante (`hmac.compare_digest`).
3. El rol decide qué herramientas existen para ese agente (`empresa/roles.yaml`).
4. Cada herramienta vuelve a validar el permiso en cada llamada.
5. Los datos se filtran **antes** de llegar al modelo: lo que no recibe, no lo puede filtrar.

## Modelo de amenazas

| Riesgo | Mitigación | Cómo se verifica |
|---|---|---|
| Un rol ve información de otro | El RAG filtra por rol **antes** de buscar; el tabulador nunca se carga para ventas | Tests con el texto literal del tabulador como pregunta |
| El modelo se salta una regla de negocio | El motor de flujos valida datos y reglas en el servidor; "rechazar" gana aunque el YAML tenga otro orden | Tests de fronteras (10 %, 10.01 %, 25 %, 25.01 %) |
| Autoaprobación | Solo el rol `aprueba` del flujo decide y nunca sobre sus propias solicitudes; una decisión no se repite | Tests de rol equivocado, autoaprobación y doble decisión |
| *Prompt injection* en documentos | Permisos independientes del modelo; `AGENTS.md` pide ignorar instrucciones dentro de datos | Documento con injection incluido a propósito + tests |
| Herramientas que el rol no tiene | Tres capas en MCP: 401 sin token, `tools/list` filtrado por rol y permiso revalidado en cada llamada | Tests con el cliente MCP oficial |
| Inyección en consultas a ServiceNow | Los valores que entran a `sysparm_query` se validan (`REQ0000000`, estados permitidos) | Test con `REQ1^u_aprueba=rh` |
| Código malicioso en cálculos | Sandbox sin red, solo lectura, `cap_drop: ALL`, `pids_limit`, `setrlimit` (CPU, memoria, procesos, archivos), tiempo máximo, entorno vacío y `tini` como PID 1 | `make sandbox-check`: 11 intentos de escape, también en CI |
| Robo de secretos | `.env` fuera de git; gitleaks en pre-commit y CI; `SecretStr`; logs sin tokens; cada contenedor recibe solo sus variables | Tests de logs y del entorno del sandbox |
| Exposición de servicios | Puertos solo en `127.0.0.1`; Redis, ServiceNow y el sandbox sin puerto publicado; protección contra DNS rebinding en `/mcp` | Test con `Host` malicioso (421) |
| Acceso a los bots | Telegram con `dmPolicy: pairing`; `make pairing` solo aprueba el id del dueño | Manual |
| Superficie de OpenClaw | Sin herramientas propias (shell, archivos, web, navegador), sin herramientas elevadas, sin automatizaciones, solo 5 plugins, sesiones aisladas entre agentes | `openclaw security audit`: 0 críticos, 0 advertencias |
| Fuerza bruta al gateway | Límite de intentos de login y token de 256 bits | `openclaw security audit` |
| Trazabilidad | Cada uso de herramienta queda en un Redis Stream (sin contenido sensible) | Tests de auditoría |

## Lo que se encontró al probar

Algunas mitigaciones existen porque las pruebas encontraron el problema:

- **Zombies en el sandbox.** Tras una bomba de procesos quedaban 30 procesos zombie, porque
  `uvicorn` era el PID 1 y no los recogía, y bloqueaban los cálculos siguientes. Se corrigió
  con `init: true` (`tini`). Lo detectó `make sandbox-check`.
- **Agentes leyendo sesiones de otros.** Por defecto, OpenClaw deja que un agente consulte
  conversaciones de otros agentes. Se cerró con `tools.sessions.visibility: "self"` y
  `agentToAgent.enabled: false`. Lo detectó `openclaw security audit`.

## Limitaciones conocidas

- **Un token por rol, no por persona.** Quien tenga acceso a un bot actúa como su rol. En
  producción el rol debe venir de la identidad del usuario (SSO con Entra ID).
- **Auditoría "fail-open".** Si Redis falla al registrar un evento, la acción no se bloquea
  (queda en el log del servidor). En un entorno regulado convendría lo contrario.
- **Decisiones concurrentes.** Dos aprobaciones simultáneas de la misma solicitud no están
  protegidas con una actualización condicional; el ServiceNow simulado no la ofrece.
- **Modelo de confianza de OpenClaw.** Un gateway está pensado para un solo operador de
  confianza. Para organizaciones que no confían entre sí, usa gateways separados.

## Reportar un problema

Si encuentras una vulnerabilidad, abre un *issue* sin detalles explotables o escribe al
mantenedor desde su perfil de GitHub.
