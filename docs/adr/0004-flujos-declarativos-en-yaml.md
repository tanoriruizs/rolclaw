# ADR 0004: Flujos declarativos en YAML

**Estado:** aceptada

## Contexto

Cada empresa tiene sus trámites y sus límites (descuentos, créditos, vacaciones). Si cada
trámite fuera código, el proyecto no serviría como plantilla y cada cambio requeriría un
desarrollador.

## Decisión

Los flujos se definen en `empresa/flujos/*.yaml`: datos requeridos con tipo y límites,
pasos (guía para el agente) y reglas (`mayor_que` / `menor_que` → `rechazar` o
`requiere_aprobacion`). Un motor genérico y puro (`flows/engine.py`) valida los datos y
aplica las reglas en el servidor. Pydantic valida los archivos al arrancar.

Detalles de diseño:

- **Rechazar gana** sin importar el orden, para que un error de orden en el YAML no deje
  pasar algo prohibido.
- Un solo nombre por condición (`mayor_que` en lugar de `max` y `mayor_que`).
- Todos los errores de datos se reportan juntos, para que el agente pregunte lo que falta.

## Alternativas descartadas

- **Reglas en código Python por flujo.** Más flexibles, pero no editables por la empresa.
- **Un motor de reglas completo (por ejemplo, DMN).** Demasiado para el alcance.

## Consecuencias

- Agregar un trámite es editar un YAML; el servidor no arranca si el YAML es inválido.
- Las reglas son simples a propósito: comparaciones numéricas sobre un campo. Reglas entre
  campos (fecha final posterior a la inicial) no están soportadas.
