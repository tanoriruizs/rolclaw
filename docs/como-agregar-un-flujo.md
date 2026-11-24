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
| `producto` | Un id que exista en `catalogo.yaml` | `LAP-001` |
| `empleado` | Un id que exista en `empleados.yaml` | `E-301` |

`min` y `max` solo aplican a `entero` y `decimal`. Faltantes, tipos incorrectos o campos
desconocidos se reportan **todos juntos** para que el agente pregunte lo que falta.

## 3. Valores que calcula el servidor

El agente no envía totales ni días: los declara el flujo en `calculados` y los pone el
servidor. Si el agente los manda, la solicitud se rechaza.

```yaml
calculados:
  - { campo: precio_unitario, calculo: precio_producto, con: { producto: producto_id } }
  - campo: total
    calculo: total_con_descuento
    con: { producto: producto_id, cantidad: cantidad, descuento_pct: descuento_pct }
```

| `calculo` | Argumentos (`con`) | Resultado |
|---|---|---|
| `precio_producto` | `producto` | Precio del catálogo |
| `total_con_descuento` | `producto`, `cantidad`, `descuento_pct` | Total con 2 decimales |
| `dias_habiles` | `inicio`, `fin` | Días sin fines de semana ni festivos (`calendario.yaml`) |
| `dias_por_antiguedad` | `inicio` | Días de vacaciones que le tocan a quien solicita |
| `saldo_vacaciones` | `inicio`, `dias` | Días que le quedarían en su año de servicio |

Cada argumento apunta a un campo definido **antes** (un dato o un calculado anterior).

## 4. Reglas

Cada regla usa **una** condición sobre un campo numérico (un dato o un calculado):
`mayor_que` o `menor_que`.

| `accion` | Efecto | Requiere |
|---|---|---|
| `rechazar` | La solicitud queda `rechazada` | `mensaje` |
| `requiere_aprobacion` | Queda `pendiente_aprobacion` | `aprueba`: un rol con la herramienta `aprobar` |

- Si ninguna regla aplica, la solicitud queda `aprobada_automaticamente`.
- **Rechazar siempre gana**, aunque esté escrita después de una regla de aprobación.
- Si aplican varias de aprobación, decide la primera de la lista.
- Nadie puede aprobar sus propias solicitudes.

## 5. Aplica y verifica

```bash
make validate    # revisa empresa/ sin levantar nada
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
- **Productos** (`catalogo.yaml`), **empleados** (`empleados.yaml`) y **festivos**
  (`calendario.yaml`): `make reload`. En modo directorio, además `make directorio`.
- **Un rol nuevo:** `make new-role ROL=compras NOMBRE="Compras" PERSONA="Carla Ríos"`.
  Crea el rol, su persona demo, sus variables, su agente y regenera la config de OpenClaw;
  al terminar te dice qué falta (describir el rol, darle documentos y crear su bot).
- **La config de OpenClaw** (`openclaw/openclaw.json5`) se genera: edita
  `openclaw.plantilla.json5` o `empresa/` y ejecuta `make openclaw-config`.
