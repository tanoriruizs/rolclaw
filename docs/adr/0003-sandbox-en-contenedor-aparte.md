# ADR 0003: Sandbox en un contenedor aparte, por socket Unix

**Estado:** aceptada

## Contexto

Los modelos se equivocan en aritmética, así que el agente calcula ejecutando código
(`calcular`). Ese código no es confiable: puede intentar salir a internet, leer secretos,
agotar memoria o lanzar una bomba de procesos.

## Decisión

El código corre en un contenedor dedicado:

- **Contenedor:** `network_mode: none`, solo lectura, `/tmp` en memoria, `cap_drop: ALL`,
  `no-new-privileges`, `pids_limit`, memoria y CPU limitadas, usuario propio y `tini` como
  PID 1 para recoger procesos zombie.
- **Cada cálculo:** un proceso nuevo `python -I -S` que se impone `setrlimit` (CPU, memoria,
  procesos, archivos) antes de leer el código, sin variables de entorno, en un directorio
  temporal que se borra, con tiempo máximo y salida acotada. Un cálculo a la vez, y al
  terminar se matan los procesos que intenten quedarse.
- **Comunicación:** HTTP sobre un socket Unix en un volumen compartido, porque el sandbox
  no tiene red.

## Alternativas descartadas

- **Subproceso con límites dentro del servidor.** Más simple, pero comparte red, archivos y
  secretos con el servidor (era el "plan B").
- **`eval` restringido.** No hay forma segura de limitar Python en el mismo proceso.

## Consecuencias

- `make sandbox-check` lanza 11 intentos de escape contra el contenedor real, también en CI.
  Gracias a esa prueba se encontró el problema de los zombies.
- Un cálculo a la vez limita el rendimiento; para escalar se agregan réplicas del sandbox.
