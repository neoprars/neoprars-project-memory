# Neoprars Project Memory V1.4.1 — ayuda

Python 3.10+, sin paquetes externos. Copiar el paquete a `.agents/skills/neoprars-project-memory/`; no sobrescribir una instalación existente sin revisar cambios. No modificar AGENTS.md automáticamente.

```powershell
$mem = '.agents/skills/neoprars-project-memory/scripts/memory.py'
python -X utf8 -B $mem --project C:/repos/proyecto start --json
python -X utf8 -B $mem --project C:/repos/proyecto resume --memory-root notes/memory --state-path docs/status.md
python -X utf8 -B $mem --project C:/repos/proyecto handoff --preview --memory-root notes/memory --state-path docs/status.md --roadmap-path docs/plan.md
python -X utf8 -B $mem help
```

| Comando | Función | Ejemplo |
|---|---|---|
| **start** | Contexto y vigencia | `start --json` |
| audit | Descubrimiento sin escritura | `audit --json` |
| recall | Sesiones | `recall --from 2026-10-01 --to 2026-10-11` |
| resume | Estado y sesiones con memoria | `resume` |
| summary | Resumen | `summary --export` |
| roadmap | Exportación | `roadmap --mode pending` |
| decisions | Decisiones y evidencia | `decisions` |
| handoff | Transferencia Markdown/TXT a cualquier IA | `handoff --preview` |
| close | Propuesta de cierre | `close --help` |
| history | Hashes/fechas Git | `history` |
| help | Todos los comandos | `help` |
| add-decision | Propuesta de decisión | `add-decision --help` |
| initialize | Propuesta de estructura | `initialize --json` |
| register-source | Propuesta de fuente | `register-source --help` |
| add-conflict | Propuesta de conflicto | `add-conflict --help` |
| resolve-conflict | Propuesta de resolución | `resolve-conflict --help` |
| apply | Persistencia supervisada | `apply --help` |

Opciones compartidas: `--memory-root`, `--json`, `--max-files 5000`, `--max-bytes 2000000`, `--max-depth 12`, `--max-document-bytes 1000000`, `--max-documents 1000`, `--max-seconds 20`, `--chunk-size 16384`, `--exclude NOMBRE`.

`--project` va antes del comando; las demás opciones después. `--mode automatic|supervised` selecciona el flujo; solo `apply --mode supervised` persiste memoria. En `roadmap`, `--mode` conserva `full|pending|handoff` por compatibilidad.

`close` admite `--tests-status aprobadas|fallidas|no_ejecutadas|desconocidas`, `--decision-id`, `--conflict-id` y referencias JSON repetidas mediante `--evidence`. `add-decision --supersedes ID` conserva la decisión anterior. Ver [WORKFLOWS.md](WORKFLOWS.md) y [FORMATS.md](FORMATS.md).

V1.3 mantiene lectura de registros anteriores, pero `close/add-decision` ahora preparan propuestas en stdout. `history` muestra hashes/fechas con timeout, sin asuntos de commits. No hay lectura de conversaciones externas.

Pruebas: `python -X utf8 -B -m unittest discover -s .agents/skills/neoprars-project-memory/tests -v`. `NEOPRARS_TEST_TMP` permite elegir una carpeta temporal autorizada para fixtures. Las pruebas solo generan proyectos sintéticos temporales.

V1.4: `handoff --preview` no escribe; `handoff` exporta Markdown por defecto. `--format txt` conserva TXT. Opciones: `--module`, `--sessions 10` (1 a 50), `--goal`, `--next-step`, `--technical` (anexo opcional). La salida normal muestra el documento; `--json` devuelve el envelope para automatizaciones. Ver [HANDOFF.md](HANDOFF.md).

V1.4.1: estado y roadmap se citan por referencia de forma predeterminada; `--full-documents` incluye el contenido completo. Handoff conserva posiciones historicas de conflictos y alcance, criterios, dependencias y limites de pendientes. No modifica formatos persistidos.
