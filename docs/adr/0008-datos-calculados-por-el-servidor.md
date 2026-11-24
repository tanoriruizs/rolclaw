# ADR 0008: Los datos de negocio los calcula el servidor

**Estado:** aceptada

## Contexto

Las reglas de los flujos ya se validaban en el servidor, pero sobre datos que enviaba el
agente: el total de una cotización, los días de unas vacaciones. Un modelo que se equivoca
(o al que convencen) podía mandar `total: 1` o `dias: 2` para un periodo de dos semanas, y
la regla se aplicaba sobre un dato falso.

## Decisión

El agente solo envía lo que dijo la persona (producto, cantidad, fechas). Lo demás son
**campos calculados**, declarados en el YAML del flujo y resueltos por el servidor:

| Cálculo | De dónde sale |
|---|---|
| `precio_producto`, `total_con_descuento` | `catalogo.yaml` |
| `dias_habiles` | Fechas, fines de semana y festivos de `calendario.yaml` |
| `dias_por_antiguedad` | Fecha de ingreso en `empleados.yaml` y la tabla del calendario |
| `saldo_vacaciones` | Lo anterior menos las solicitudes vigentes del mismo año de servicio |

Los campos `producto` y `empleado` deben existir. Si el agente envía un campo calculado, la
solicitud se rechaza (`no envíes total: lo calcula el servidor`).

## Alternativas descartadas

- **Confiar en `calcular` (el sandbox).** Sirve para mostrar el número antes de confirmar,
  pero el resultado vuelve a pasar por el modelo antes de llegar a la solicitud.
- **Fórmulas libres en el YAML.** Un mini lenguaje de expresiones es otra superficie de
  ataque; una lista cerrada de cálculos con tests es más simple de auditar.

## Consecuencias

- Las reglas se aplican sobre datos que el modelo no puede alterar.
- Un cálculo nuevo requiere código (una función y sus tests); los existentes se reutilizan
  desde cualquier flujo sin programar.
- El saldo de vacaciones consulta ServiceNow en cada solicitud de ese tipo.
