# Esquemas y preservación — V1.3

## sources.json

`schema_version: 1`, `sources: []`, `revisions: []`.
Fuente: `id`, `path` relativa al proyecto, `sha256`, `authority` (`candidata|confirmada|excluida`), `reason`, `approval`.
Revisión: snapshot anterior de `sources`, motivo y aprobación. ID duplicado invalida verificabilidad; versiones desconocidas requieren revisión.

## Eventos JSONL y sesiones JSON

Nuevos eventos `schema_version: 2`: `id`, `date` ISO local explícita, `created_at` con zona, `classification` (`confirmado|historico|propuesto|desconocido`), `status` (`pendiente|implementado|validado|cerrado|no_aplica`), `evidence` y `approval`.

Referencia: `source_id`, `sha256` congelada durante preparación y `lines: [inicio, fin]` opcional, positivas e inclusivas. Un rango estructural válido no acredita contenido ni semántica.

- Decisión en decisions.jsonl: `title`, `reason`, `detail`, `supersedes` nulo o ID anterior.
- Conflicto en conflicts.jsonl: `title`, `conflict_type`, `alternatives` (id, claim, evidence), `resolution: null`.
- Resolución en conflicts.jsonl: `title`, `resolves`, `choice`, `reason`, `resolution: supervisada`.
- Sesión en sessions/ID.json: `module`, `done`, `pending`, `tests`, `files`, `tests_status`, `decisions` y `conflicts` (IDs).
- pending.jsonl y bugs.jsonl: formatos legibles de eventos con título, clasificación, estado y evidencia. V1.3 no ofrece comandos específicos de escritura para esos registros; no inventarlos automáticamente.

`approval`: `proposal_sha256`, `confirmed_by` (identificador no sensible), `confirmed_at` con zona y `mode: supervised`. Es constancia del invocador, no firma ni autenticación externa.

## Evaluación en consultas

`classification`, `status` y `declared_status` describen lo registrado. `evidence_validity`, `evidence_checks` y `review_required` evalúan vigencia actual. No reescribir el estado histórico por un cambio de fuente.

Una sustitución o resolución se representa mediante nuevo evento. Nunca borrar el antecedente. Registros legacy siguen legibles, con evidencia/autoridad desconocida si no incluyen metadata. No inferir confirmación a partir de fechas, commits o clasificación declarada.

## Propuesta

`schema_version: 1`, `kind`, `project_binding`, `memory_root`, `data`, `expected` (huellas de memoria y fuentes). Tipos: initialize, source, close, decision, conflict, resolution.
La CLI valida campos permitidos, relaciones y evidencia; no admite destinos arbitrarios. `approval_sha256` es SHA-256 del JSON canónico de la propuesta.
