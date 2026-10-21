# Neoprars Project Memory

Memoria local de proyectos y transferencia de contexto para asistentes de IA.

**Estado: beta.** Version del codigo: 1.5.0. Python 3.10 o posterior; sin paquetes externos. Compatible con flujos Codex y Claude Code mediante CLI y handoff portable para cualquier asistente que pueda leer Markdown. La compatibilidad es por contrato de archivos y comandos, sin depender de AGENTS.md, ECC ni Graphify.

## Capacidades

- Consultar sesiones, decisiones, pendientes y conflictos.
- Detectar cambios en fuentes registradas mediante SHA-256 y conservar estados historicos.
- Preparar cierres y decisiones sin escribir; guardar solo propuestas confirmadas en modo supervisado.
- Resolver conflictos mediante nuevos eventos, preservando alternativas y antecedentes.
- Exportar handoff compacto con motivos, posiciones historicas, criterios de aceptacion, pruebas declaradas y fuentes.
- Funcionar sin ECC ni Graphify. Git es opcional, salvo consultas de historial.
- Reanudar con contexto consolidado, carga selectiva y limites configurables (M1/M2).
- Detectar cambios concurrentes y frescura documental sin sobrescrituras silenciosas (M3).
- Integrar fuentes grandes con lectura incremental, cobertura diferenciada y salida UTF-8 portable (M4).
- Cargar reglas globales predeterminadas y excepciones persistentes por proyecto, sin depender de `AGENTS.md`.

## Instalacion

Copiar `SKILL.md`, `LICENSE`, `scripts/` y `references/` a:

```text
TU_PROYECTO/.agents/skills/neoprars-project-memory/
```

Opcionalmente copiar `tests/` para comprobar la instalacion. Conservar una copia de una instalacion anterior y revisar cambios propios antes de reemplazarla. **No reemplazar `docs/memory/` ni `AGENTS.md`.** Abrir una sesion nueva del asistente para que descubra la skill.

La CLI tambien puede ejecutarse directamente desde este repositorio:

```powershell
python -X utf8 -B scripts/memory.py --project C:/repos/mi-proyecto start --json
python -X utf8 -B scripts/memory.py --project C:/repos/mi-proyecto handoff --preview --sessions 5
```

En Linux, reemplazar las rutas por las del proyecto y usar `python3` si corresponde. `--project` va antes del comando; las demas opciones despues.

## Comandos

| Consulta | Preparacion | Persistencia y exportacion |
|---|---|---|
| start, audit, recall, resume, preflight, check-task, rules, decisions, history, help | close, add-decision, initialize, register-source, add-conflict, resolve-conflict | apply, summary --export, roadmap, handoff |

`summary` sin `--export` es una consulta; `handoff --preview` no escribe. Ver [ayuda](references/HELP.md) y la ayuda individual: `python -X utf8 -B scripts/memory.py handoff --help`.

`rules list` muestra las reglas efectivas. `rules add|modify|deactivate|delete|restore` prepara una propuesta para persistir en `PROJECT_RULES.json`; revisar y aplicar con el flujo supervisado de `apply`. Ver [PROJECT_RULES](references/PROJECT_RULES.md).

Antes de cada tarea, ejecutar `preflight --task "Objetivo" --json` (o `check-task`). Si devuelve `authorization_required`, informar los conflictos y solicitar autorización. Codex y Claude Code pueden ejecutarlo por shell; no se afirma automatización por prompt sin un hook del agente o del entorno.

## Handoff a otra IA

```powershell
python -X utf8 -B scripts/memory.py --project C:/repos/mi-proyecto handoff --preview --goal 'Revisar continuidad' --next-step 'Confirmar contexto y solicitar fuentes'
```

Revisar primero la vista previa; ejecutar sin `--preview` para crear un Markdown nuevo en `docs/memory/exports/`. Opciones: `--module`, `--sessions`, `--state-path`, `--roadmap-path`, `--format txt`, `--technical`, `--full-documents` y `--memory-root`.

Estado y roadmap se citan por ruta de forma predeterminada. `--full-documents` incluye sus cuerpos. El documento no otorga acceso al repositorio ni autorizacion para desarrollar. [Procedimiento y criterios de prueba](references/HANDOFF.md).

## Cierre supervisado

`close` y `add-decision` preparan propuestas en stdout; no guardan directamente.

1. Preparar el cierre con hechos, pruebas y fecha local explicita.
2. Conservar la salida en una ruta temporal autorizada y revisar contenido/rutas.
3. Confirmar esa version concreta y aplicar con su huella:

```powershell
python -X utf8 -B scripts/memory.py --project C:/repos/mi-proyecto apply --proposal work/proposal.json --mode supervised --approval-sha256 HUELLA_CONFIRMADA --approved-by usuario
```

La huella identifica contenido; no autentica por si misma al supervisor. Un cambio de memoria/fuentes exige revisar otra propuesta. [Flujos](references/WORKFLOWS.md) y [formatos](references/FORMATS.md).

## Pruebas

```text
python -X utf8 -B -m unittest discover -s tests -v
```

Los fixtures son sinteticos y temporales. `NEOPRARS_TEST_TMP` permite seleccionar una carpeta temporal autorizada. Validacion local previa: 85 pruebas, 83 aprobadas y 2 omitidas por restricciones para crear enlaces reales. La automatizacion publica comprueba Windows/Linux con Python 3.10/3.13; consultar sus resultados actuales.

## Limites de la beta

- Coincidir por huella verifica bytes, no semantica, implementacion, autoridad ni resultados de pruebas.
- Las lecturas y controles temporales son cooperativos; se declara cobertura parcial.
- El bloqueo coordina escritores de esta CLI. La inicializacion de varios archivos no es una transaccion; ante interrupcion, inspeccionar estado y bloqueos antes de reintentar.
- Excluir/filtrar patrones sensibles no garantiza ausencia de informacion confidencial. Revisar exportaciones antes de compartir.
- Registros antiguos siguen legibles con metadata desconocida cuando falta. No se migran automaticamente.
- Se recomienda probar primero con un proyecto de ensayo. No se ha comprobado funcionamiento en macOS.

## Contribuciones

Informar el comportamiento esperado, version de Python/SO y un ejemplo sintetico reproducible. No adjuntar credenciales, memoria privada, handoffs reales ni datos personales. Ejecutar las pruebas antes de proponer cambios.

## Licencia

[MIT](LICENSE). Copyright (c) 2026 NeoPrars INC.
