# Cómo agregar un flujo

Un flujo es un trámite (pedir un descuento, solicitar vacaciones…). Se define en YAML; el
servidor valida los datos y aplica las reglas. No hay que escribir código.

## 1. Crea el archivo

`empresa/flujos/<rol>/<id>.yaml`. La carpeta solo sirve para ordenar; quién puede usar el
flujo lo dice el campo `roles`.

```yaml
id: reembolso_gastos            # minúsculas y guiones bajos; único
nombre: Reembolso de gastos
roles: [empleado]               # quién puede iniciarlo (debe tener iniciar_solicitud)
descripcion: Solicita el reembolso de un gasto de trabajo con comprobante.
datos_requeridos:
  - { campo: concepto, tipo: texto }
  - { campo: monto, tipo: decimal, min: 0 }
  - { campo: fecha, tipo: fecha }
pasos:                          # guía para el agente; el servidor no los ejecuta
  - Pedir concepto, monto y fecha del gasto
  - Confirmar con el usuario antes de crear la solicitud
reglas:
  - { campo: monto, mayor_que: 20000, accion: rechazar, mensaje: "El máximo por reembolso es $20,000." }
  - { campo: monto, mayor_que: 2000, accion: requiere_aprobacion, aprueba: gerente }
```

## 2. Tipos de datos

| `tipo` | Acepta | Ejemplo |
|---|---|---|
| `texto` | Texto de hasta 500 caracteres | `Comercial Norte` |
| `entero` | Número entero (también `"50"`) | `50` |
| `decimal` | Número con decimales (también `"12.5"`) | `12.5` |
| `fecha` | Fecha ISO | `2026-12-14` |

`min` y `max` solo aplican a `entero` y `decimal`. Faltantes, tipos incorrectos o campos
desconocidos se reportan **todos juntos** para que el agente pregunte lo que falta.

## 3. Reglas

Cada regla usa **una** condición sobre un campo numérico: `mayor_que` o `menor_que`.

| `accion` | Efecto | Requiere |
|---|---|---|
| `rechazar` | La solicitud queda `rechazada` | `mensaje` |
| `requiere_aprobacion` | Queda `pendiente_aprobacion` | `aprueba`: un rol con la herramienta `aprobar` |

- Si ninguna regla aplica, la solicitud queda `aprobada_automaticamente`.
- **Rechazar siempre gana**, aunque esté escrita después de una regla de aprobación.
- Si aplican varias de aprobación, decide la primera de la lista.
- Nadie puede aprobar sus propias solicitudes.

## 4. Aplica y verifica

```bash
make reload      # reinicia el servidor MCP; valida empresa/ al arrancar
```

Si algo está mal, el servidor **no arranca** y el log dice el archivo y el campo:

```
empresa/flujos/empleado/reembolso_gastos.yaml: reglas.0: Value error, regla de 'monto':
rechazar necesita 'mensaje'
```

Después pídele al agente del rol: *"¿Qué trámites puedo hacer?"* y debe aparecer el nuevo.
Para probar las reglas sin Telegram, usa la API en `http://127.0.0.1:8000/docs`
(`POST /api/v1/solicitudes`).

## Otros cambios en `empresa/`

- **Documentos** (`documentos/*.md`): el encabezado `roles` decide quién los puede leer.
  `make reload` re-indexa solo si el contenido o los roles cambiaron.
- **Productos** (`catalogo.yaml`) y **empleados** (`empleados.yaml`): `make reload`.
- **Un rol nuevo** requiere además su token (`ROLE_TOKEN_<ROL>` en `.env.example` y en
  `docker-compose.yml`), un servidor MCP y un agente en `openclaw/openclaw.json5`, su carpeta
  en `openclaw/agentes/` y un bot de Telegram.
