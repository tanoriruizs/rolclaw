# ADR 0006: La config de OpenClaw se copia al arrancar

**Estado:** aceptada

## Contexto

La configuración de OpenClaw (`openclaw/openclaw.json5`) debe estar versionada y sin
secretos. El primer intento la montaba en solo lectura, pero OpenClaw guarda datos en la
carpeta de su config (media, migraciones de `doctor`, *pairing*) y fallaba al aprobar un
*pairing*. Además, montar archivos sueltos los "congela" al crear el contenedor: si un
editor reemplaza el archivo, el contenedor sigue viendo la versión anterior.

## Decisión

Se monta la carpeta `openclaw/` en solo lectura y `openclaw/start.sh` copia la config
(permisos 600) y los `AGENTS.md`/`SOUL.md` de cada agente a la carpeta de estado de
OpenClaw antes de arrancar la imagen oficial. Los secretos entran por variables de entorno
(SecretRef y `${VAR}`).

La config ya incluye las migraciones que `openclaw doctor` aplica en la versión fijada, y se
verificó con `openclaw config schema`, `openclaw doctor --lint` y `openclaw security audit`.

## Alternativas descartadas

- **Config de solo lectura en su propia carpeta.** Choca con el diseño de OpenClaw.
- **Config editable dentro del volumen.** Se desviaría de lo versionado sin que nadie lo note.

## Consecuencias

- El repo es la fuente de verdad: cada arranque vuelve a su versión.
- Lo que OpenClaw escribe en tiempo de ejecución vive en el volumen de estado.
- `make up` recrea el contenedor de OpenClaw para aplicar cambios de config o de agentes.
