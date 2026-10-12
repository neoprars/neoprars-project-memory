# start — V1.3

Ambos modos son de solo lectura. Sin memoria, ejecutar descubrimiento y presentar propuesta; `initialize` prepara contenido concreto y `apply` registra únicamente tras confirmación. Una carpeta existente vacía/incompleta nunca se reinicializa.

Con memoria, consultar INDEX.md, sources.json, decisions.jsonl, conflicts.jsonl y pending.jsonl. JSON requiere lectura completa; JSONL parcial admite solo líneas completas. Informar registros inválidos, IDs duplicados, versiones desconocidas y cobertura.

Recuperar contexto antes de gastar presupuesto en hashes. Las fuentes cambiadas, ausentes, excluidas, duplicadas, desconocidas o no verificables hacen que sus dependientes requieran revisión. `declared_status` y `classification` se conservan; `evidence_validity` y `review_required` son evaluaciones separadas. Las referencias nuevas congelan SHA-256 para que una revisión de sources.json no actualice retrospectivamente una decisión.

Los rangos de líneas se validan estructuralmente, sin certificar su contenido ni semántica. Una resolución se agrega como evento que referencia `resolves`; revisar metadata de aprobación y autoridad humana. No seleccionar decisiones por fecha ni modificar registros desde start.

Aplicar presupuestos de entradas, documentos, bytes, profundidad, fragmentos y tiempo cooperativo. Omitir enlaces/junctions/reparse points, rutas externas, sensibles y generadas. UTF-8 inválido interior se descarta; un sufijo incompleto queda pendiente, sin usar `errors=ignore`.

`resume`, `decisions` y `handoff` incorporan este contexto y comparten presupuesto con sus demás lecturas. `--memory-root` está disponible en todos los comandos. Sin control de versiones, fijar `--project` explícitamente desde subdirectorios.
