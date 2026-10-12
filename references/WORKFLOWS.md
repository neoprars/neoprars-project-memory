# Propuestas y confirmación — V1.3

## Preparar y aplicar

Los comandos de preparación devuelven `{proposal, approval_sha256, read_only, next}` por stdout. No guardan archivos. El agente puede conservar la propuesta en una ruta temporal autorizada para revisión; no inicializar memoria como efecto de una consulta.

1. Preparar con evidencia comprobada y fecha local explícita.
2. Presentar rutas, contenido exacto, pruebas desconocidas/fallidas, fuentes y consecuencias. Solicitar confirmación solo si no existe autorización específica para esa versión.
3. Aplicar la propuesta confirmada con su huella, `--mode supervised` y un identificador no sensible del supervisor. Nunca fabricar aprobación.

```powershell
python -X utf8 -B $mem --project C:/repos/proyecto close --date 2026-10-11 --done 'Trabajo comprobado' --tests-status no_ejecutadas --json
# Tras conservar y revisar esa salida exacta en work/proposal.json:
python -X utf8 -B $mem --project C:/repos/proyecto apply --proposal work/proposal.json --mode supervised --approval-sha256 <huella-confirmada> --approved-by usuario
```

La huella usa JSON canónico de `proposal`, no los bytes del archivo ni del envelope. Por eso cambiar sangría no la altera; cambiar contenido sí. Una huella no autentica por sí sola al supervisor: registra la afirmación del invocador. El agente debe contar con confirmación real.

Cada propuesta queda ligada a proyecto, ubicación, estado de registros y fuentes usadas. Un cambio entre preparación y aplicación exige nueva propuesta y revisión. No mezclar proyectos. apply usa un bloqueo exclusivo temporal por ubicación para coordinar escritores de esta CLI. Las comprobaciones son optimistas y cooperativas; no garantizan protección ante cambios hostiles durante la escritura ni una transacción de varios archivos. Ante interrupción, inspeccionar estado y cualquier bloqueo pendiente antes de reintentar; nunca borrar un bloqueo automáticamente por fecha.

## Inicialización y fuentes

`initialize` propone INDEX.md, RULES.md, FORMATS.md, sources.json y registros vacíos. No inventa avances. Rechaza directorio de memoria existente.

`register-source --id facts --path docs/status.md --authority confirmada --reason 'Autoridad revisada'` calcula la huella actual. El agente debe comprobar autoridad antes de declararla. Una actualización conserva snapshots anteriores en `sources.json.revisions`; referencias históricas mantienen su huella.

## Decisiones

`add-decision --date FECHA --title TITULO --reason MOTIVO --detail DETALLE --evidence '{"source_id":"facts","lines":[1,5]}'` prepara un evento. Sin evidencia, su vigencia queda desconocida al consultar. `--supersedes ID` agrega una sustitución; no elimina antecedentes.

## Conflictos

`add-conflict` requiere fecha, título, tipo y dos o más `--alternative` JSON: `{"id":"a","claim":"Afirmación","evidence":[{"source_id":"facts"}]}`. Tipos: contenido, autoridad, histórico, evidencia, formato.

La CLI registra clasificación supervisada; audit solo propone conflictos estrechos por casillas o autoridad múltiple. Diferencias históricas pueden coexistir sin contradicción vigente. Revisar alcance, fuente, evidencia y autorización, no solo fechas.

`resolve-conflict --date FECHA --conflict-id ID --choice a --reason MOTIVO --evidence '{"source_id":"facts"}'` prepara una elección. Exige alternativa conocida y evidencia vigente de la elección y la alternativa. `apply` agrega un evento con `resolves`, motivo y aprobación; conserva el original. Una resolución ya registrada requiere un nuevo conflicto para revisar la elección, evitando sobrescrituras.

## Exportaciones

`summary --export` y `roadmap` escriben TXT bajo la misma ubicación de memoria. V1.4: handoff exporta Markdown por defecto, con `--format txt` opcional; `--preview` solo muestra el documento. Incluyen cobertura y contexto cuando corresponde. Filtran patrones reconocibles; inspeccionar antes de compartir con otro asistente. No afirmar que las pruebas están aprobadas por estar listadas. Ver [HANDOFF.md](HANDOFF.md).
