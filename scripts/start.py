"""Read-only entry point. No persistence, repair, exports or repository execution."""
import hashlib
import codecs
import json
import math
import re
import time
from pathlib import Path

from audit import EXCLUDED, SENSITIVE, LINK, DOC_EXT, LANGUAGES, safe_label, discover_root, audit_project
from safety import safe_path


class Budget:
    def __init__(self, max_bytes, max_documents, max_document_bytes, chunk_size, deadline):
        self.bytes = max_bytes
        self.documents = max_documents
        self.document_bytes = max_document_bytes
        self.chunk_size = chunk_size
        self.deadline = deadline
        self.spent = self.opened = 0
        self.coverage = []

    def read(self, path, *, digest=False):
        item = {'path': safe_label(path.as_posix()), 'status': 'omitida', 'bytes_read': 0}
        self.coverage.append(item)
        data = bytearray()
        hasher = hashlib.sha256()
        try:
            before = path.stat()
            item['size_bytes'] = before.st_size
            if self.opened >= self.documents:
                item['reason'] = 'presupuesto de documentos agotado'
                return None, item
            if self.spent >= self.bytes or time.monotonic() >= self.deadline:
                item['reason'] = 'presupuesto global de bytes o tiempo agotado'
                return None, item
            self.opened += 1
            with path.open('rb', buffering=0) as stream:
                while item['bytes_read'] < before.st_size:
                    if time.monotonic() >= self.deadline:
                        item['reason'] = 'presupuesto de tiempo agotado'
                        break
                    amount = min(self.chunk_size, self.bytes - self.spent,
                                 self.document_bytes - item['bytes_read'],
                                 before.st_size - item['bytes_read'])
                    if amount <= 0:
                        item['reason'] = 'presupuesto de bytes agotado'
                        break
                    chunk = stream.read(amount)
                    if not chunk:
                        item['reason'] = 'archivo cambiado; no verificable'
                        break
                    self.spent += len(chunk)
                    item['bytes_read'] += len(chunk)
                    if b'\x00' in chunk:
                        item['reason'] = 'binario omitido'
                        return None, item
                    hasher.update(chunk)
                    if not digest:
                        data.extend(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                item['reason'] = 'archivo cambiado; no verificable'
                return None, item
            complete = item['bytes_read'] == before.st_size and 'reason' not in item
            item['status'] = 'completa' if complete else ('parcial' if item['bytes_read'] else 'omitida')
            item['reason'] = item.get('reason', 'fin del archivo')
            if digest:
                return hasher.hexdigest() if complete else None, item
            try:
                decoder = codecs.getincrementaldecoder('utf-8-sig')()
                text = decoder.decode(data, final=complete)
            except UnicodeError:
                item['status'] = 'omitida'
                item['reason'] = 'codificacion desconocida'
                return None, item
            if not complete:
                text = text[:text.rfind('\n') + 1]
            item['lines_analysed'] = len(text.splitlines())
            return text, item
        except OSError:
            item['reason'] = 'archivo inaccesible'
            return None, item


def allowed_path(root, relative, exclusions, max_depth=12):
    """Never follow links, junctions, excluded components or paths outside this root."""
    if isinstance(relative, str) and safe_label(relative) != relative:
        return None
    return safe_path(root, relative, exclusions, SENSITIVE, max_depth)


def label(value):
    if not isinstance(value, str) or len(value) > 250:
        return '[dato omitido]'
    if (SENSITIVE.search(value) or re.search(r'\b(?:ghp_|github_pat_|sk-|eyJ)\S+|\S+@\S+|https?://|\b\w{32,}\b', value)):
        return '[dato omitido por seguridad]'
    return safe_label(value)


def start_project(project=None, *, memory_root=None, max_files=5000, max_bytes=2000000,
                  max_depth=12, exclude=(), max_document_bytes=1000000,
                  max_documents=1000, max_seconds=20, chunk_size=16384, mode='automatic'):
    if (min(max_files, max_bytes, max_document_bytes, max_documents, chunk_size) < 1
            or max_depth < 0 or not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError('invalid limits')
    deadline = time.monotonic() + max_seconds
    root, root_evidence = discover_root(project, deadline=deadline)
    exclusions = EXCLUDED | {x.casefold() for x in exclude}
    location = memory_root or 'docs/memory'
    folder = allowed_path(root, location, exclusions, max_depth)
    if mode not in ('automatic', 'supervised'):
        raise ValueError('invalid mode')
    report = {'version': '1.3', 'command': 'start', 'mode': mode, 'read_only': True,
              'root': safe_label(root.name), 'root_evidence': root_evidence,
              'memory_path': label(location), 'memory_status': 'desconocida',
              'partial': False, 'index': {}, 'sources': [], 'references': [],
              'decisions': [], 'conflicts': [], 'pending': [], 'issues': [],
              'requires_explicit_approval_for_writes': True}
    if folder is None:
        report['issues'].append('Ruta de memoria insegura o excluida; no se inspecciona ni inicializa.')
        report['partial'] = True
        return report
    if not folder.exists():
        report['memory_status'] = 'ausente'
        remaining = deadline - time.monotonic()
        if remaining > 0:
            report['audit'] = audit_project(root, max_files=max_files, max_bytes=max_bytes,
                max_depth=max_depth, exclude=exclude, max_document_bytes=max_document_bytes,
                max_documents=max_documents, max_seconds=remaining, chunk_size=chunk_size)
            report['partial'] = report['audit']['partial']
        else:
            report['partial'] = True
            report['issues'].append('Tiempo agotado antes de audit.')
        report['proposal'] = {'status': 'propuesta; no ejecutada',
            'paths': [location + '/' + name for name in ('INDEX.md', 'sources.json', 'RULES.md',
                       'FORMATS.md', 'decisions.jsonl', 'bugs.jsonl', 'pending.jsonl',
                       'conflicts.jsonl', 'sessions/', 'exports/')],
            'content': 'Indice de fuentes verificadas y formatos; registros solo con evidencia, sin inventar sesiones ni aprobaciones.',
            'approval_question': '¿Aprobás preparar e inicializar estas rutas tras revisar las fuentes y el contenido concreto?',
            'next': 'Revisar propuesta concreta y solicitar aprobacion explicita; start nunca escribe.'}
        return report
    if not folder.is_dir():
        report['issues'].append('La ruta de memoria no es un directorio.')
        report['memory_status'] = 'incompleta'
        report['partial'] = True
        return report
    budget = Budget(max_bytes, max_documents, max_document_bytes, chunk_size, deadline)
    report['memory_status'] = 'existente'
    entries = 0
    def read_file(name, json_value=False):
        nonlocal entries
        if entries >= max_files:
            report['issues'].append('Presupuesto de archivos agotado.')
            return None, None
        entries += 1
        path = allowed_path(root, (folder/name).relative_to(root).as_posix(), exclusions, max_depth)
        if path is None or not path.is_file():
            report['issues'].append('Fuente de memoria omitida o ausente: ' + label(name))
            return None, None
        text, coverage = budget.read(path)
        coverage['path'] = safe_label(path.relative_to(root).as_posix())
        if text is None:
            return None, coverage
        if json_value:
            if coverage['status'] != 'completa':
                report['issues'].append('JSON parcial no se interpreta: ' + label(name))
                return None, coverage
            try:
                return json.loads(text), coverage
            except (ValueError, RecursionError):
                report['issues'].append('JSON invalido: ' + label(name))
                return None, coverage
        return text, coverage
    index, coverage = read_file('INDEX.md')
    report['index'] = {'status': coverage['status'] if coverage else 'ausente'}
    if index is not None:
        for target in LINK.findall(index):
            if entries >= max_files or time.monotonic() >= deadline:
                report['issues'].append('Presupuesto agotado al comprobar enlaces del indice.')
                break
            entries += 1
            target = target.split('#', 1)[0].strip('<>')
            if not target:
                continue
            if Path(target).is_absolute() or target.startswith(('/', '\\')) or re.match(r'\w+:|//|\\\\', target):
                path = None
            else:
                path = allowed_path(root, (folder/target).relative_to(root).as_posix(), exclusions, max_depth)
            report['references'].append({'path': safe_label(target), 'status':
                'omitida por seguridad' if path is None else ('presente' if path.exists() else 'ausente')})
    registry, coverage = read_file('sources.json', json_value=True)
    source_ids = {}
    source_results = {}
    duplicate_sources = set()
    hash_checks = []
    if isinstance(registry, dict) and isinstance(registry.get('sources'), list):
        if registry.get('schema_version', 1) != 1:
            report['issues'].append('Version de registro de fuentes desconocida; revisar manualmente.')
        for source in registry['sources']:
            if entries >= max_files or time.monotonic() >= deadline:
                report['issues'].append('Presupuesto agotado al comprobar fuentes.')
                break
            entries += 1
            if not isinstance(source, dict) or not isinstance(source.get('id'), str):
                report['issues'].append('Fuente con formato invalido.')
                continue
            ident = source['id']
            if ident in source_ids:
                report['issues'].append('ID de fuente duplicado.')
                duplicate_sources.add(ident)
            source_ids[ident] = source
            relative = source.get('path')
            path = allowed_path(root, relative, exclusions, max_depth)
            result = {'id': label(ident), 'path': safe_label(relative) if isinstance(relative, str) else 'desconocida', 'status': 'desconocida'}
            if str(source.get('authority', '')).startswith(('excluida', 'excluded')):
                result['status'] = 'excluida como autoridad; no se valida contenido'
            elif path is None:
                result['status'] = 'omitida por seguridad'
            elif not path.is_file():
                result['status'] = 'ausente'
            elif path.suffix.lower() not in (DOC_EXT | set(LANGUAGES) | {'.csproj', '.fsproj', '.vbproj', '.sln', '.slnx', '.xml', '.ps1', '.sh', '.yml', '.yaml', '.toml'}):
                result['status'] = 'omitida: tipo de archivo no permitido'
            elif not re.fullmatch(r'[0-9a-f]{64}', str(source.get('sha256', ''))):
                result['status'] = 'presente; huella desconocida'
            else:
                hash_checks.append((path, source['sha256'], result))
            report['sources'].append(result)
            source_results[ident] = result
    else:
        report['issues'].append('Registro de fuentes ausente, parcial o invalido; no se repara.')
    event_ids = set()
    duplicate_events = set()
    dependencies = []
    raw_events = {}
    for name, output in (('decisions.jsonl', 'decisions'), ('conflicts.jsonl', 'conflicts'), ('pending.jsonl', 'pending')):
        text, item = read_file(name)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            if time.monotonic() >= deadline:
                report['issues'].append('Tiempo agotado durante registros.')
                break
            if entries >= max_files:
                report['issues'].append('Presupuesto de entradas agotado durante registros.')
                break
            entries += 1
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError()
            except (ValueError, RecursionError):
                report['issues'].append('Registro invalido en ' + name + ':' + str(number))
                continue
            ident = record.get('id')
            known_schema = record.get('schema_version', 1) in (1, 2)
            if not known_schema:
                report['issues'].append('Version de registro desconocida; autoridad y estado no verificables.')
            if ident is not None:
                if not isinstance(ident, str) or ident in event_ids:
                    report['issues'].append('ID de evento invalido o duplicado.')
                    if isinstance(ident, str):
                        duplicate_events.add(ident)
                else:
                    event_ids.add(ident)
            proof_status = []
            evidence = record.get('evidence', [])
            if isinstance(evidence, list):
                for proof in evidence:
                    if isinstance(proof, dict) and isinstance(proof.get('source_id'), str):
                        proof_status.append('referencia conocida' if proof['source_id'] in source_ids else 'referencia desconocida')
                    elif isinstance(proof, dict) and 'source_id' in proof:
                        proof_status.append('referencia invalida')
            result = {'record': number, 'id': label(ident) if ident else 'legacy sin ID',
                'title': label(record.get('title', 'sin titulo')), 'classification':
                record.get('classification') if known_schema and record.get('classification') in ('confirmado','historico','propuesto','desconocido') else 'desconocido',
                'status': record.get('status') if known_schema and record.get('status') in ('pendiente','implementado','validado','cerrado','no_aplica') else 'desconocido',
                'references': proof_status, 'coverage': item['status'],
                'authority': 'declaracion registrada; no certifica aprobacion vigente'}
            if output == 'conflicts':
                result['resolution'] = 'pendiente' if record.get('resolution') is None else 'declarada; requiere comprobar autoridad'
            report[output].append(result)
            dependencies.append((record, result, known_schema))
            if isinstance(ident, str):
                raw_events[ident] = record
    for path, expected, result in hash_checks:
        digest, item = budget.read(path, digest=True)
        item['path'] = safe_label(path.relative_to(root).as_posix())
        result['status'] = 'no verificable por presupuesto o lectura' if digest is None else (
            'verificada por huella' if digest == expected else 'cambiada; requiere revision')
    for ident in duplicate_sources:
        source_results[ident]['status'] = 'ID duplicado; no verificable'
        for result in report['sources']:
            if result['id'] == label(ident):
                result['status'] = 'ID duplicado; no verificable'
    registry_known = isinstance(registry, dict) and registry.get('schema_version', 1) == 1
    for record, result, known_schema in dependencies:
        evidence = record.get('evidence')
        checks = []
        if isinstance(evidence, list):
            for proof in evidence:
                if entries >= max_files or time.monotonic() >= deadline:
                    report['issues'].append('Presupuesto agotado al evaluar dependencias.')
                    checks.append({'source_id': 'desconocido', 'status': 'no verificable por presupuesto'})
                    break
                entries += 1
                sid = proof.get('source_id') if isinstance(proof, dict) else None
                source = source_results.get(sid) if isinstance(sid, str) else None
                lines = proof.get('lines') if isinstance(proof, dict) else None
                anchor_ok = (lines is None or (isinstance(lines, list) and len(lines) == 2
                    and all(type(n) is int and n > 0 for n in lines) and lines[0] <= lines[1]))
                expected = proof.get('sha256') if isinstance(proof, dict) else None
                registered = source_ids.get(sid, {}) if isinstance(sid, str) else {}
                checks.append({'source_id': label(sid), 'status':
                    'referencia invalida' if not isinstance(sid, str) or not anchor_ok else
                    'referencia desconocida' if source is None else
                    'version de fuente distinta' if expected and expected != registered.get('sha256') else source['status']})
        result['evidence_checks'] = checks
        verified = (registry_known and known_schema
                    and (record.get('id') is None or (isinstance(record.get('id'), str) and record['id'] not in duplicate_events))
                    and bool(checks)
                    and all(c['status'] == 'verificada por huella' for c in checks))
        result['evidence_validity'] = 'bytes vigentes; semantica no certificada' if verified else 'requiere revision'
        result['review_required'] = not verified
        # Preserve declared historical status rather than rewriting the event.
        result['declared_status'] = result['status']
        if result in report['conflicts']:
            result['conflict_type'] = label(record.get('conflict_type', 'desconocido'))
            resolution = record.get('resolves')
            result['resolves'] = label(resolution) if resolution else None
        if record.get('supersedes'):
            result['supersedes'] = label(record['supersedes'])
        result['effective_status'] = result['status']
    results_by_id = {record['id']: result for record, result, _ in dependencies
                     if isinstance(record.get('id'), str) and record['id'] not in duplicate_events}
    for record, result, _ in dependencies:
        if record.get('resolves'):
            target = raw_events.get(record['resolves']) if isinstance(record['resolves'], str) else None
            approval = record.get('approval')
            result['resolution'] = ('registrada; evidencia requiere revision' if result['review_required'] else
                'registrada; revisar autoridad') if target and isinstance(approval, dict) else 'resolucion invalida; requiere revision'
        if isinstance(record.get('supersedes'), str):
            previous = results_by_id.get(record['supersedes'])
            if previous is not None:
                previous.setdefault('superseded_by', []).append(label(record.get('id')))
                previous['effective_status'] = 'sustituida; antecedente preservado'
        if isinstance(record.get('resolves'), str) and isinstance(record.get('approval'), dict):
            previous = results_by_id.get(record['resolves'])
            if previous is not None:
                previous.setdefault('resolved_by', []).append(label(record.get('id')))
                previous['effective_status'] = ('resolucion registrada; requiere revision' if result['review_required'] else
                    'resolucion registrada; revisar autoridad')
    report['memory_status'] = 'incompleta' if report['issues'] or index is None or not isinstance(registry, dict) else 'existente'
    report['coverage'] = {'bytes_read': budget.spent, 'documents_read': budget.opened,
        'entries_checked': entries, 'files': budget.coverage,
        'limits': {'bytes': max_bytes, 'documents': max_documents, 'document_bytes': max_document_bytes,
                   'entries': max_files, 'seconds': max_seconds, 'depth': max_depth, 'chunk_size': chunk_size}}
    report['partial'] = (bool(report['issues']) or any(x['status'] != 'completa' for x in budget.coverage)
                         or any(x['status'] != 'verificada por huella' for x in report['sources']))
    report['limitations'] = ['No copia el indice ni detalles arbitrarios; muestra metadatos y titulos filtrados.',
        'Una huella coincidente no verifica decisiones, implementacion ni resultados de pruebas.',
        'No resuelve conflictos por fecha, no repara archivos ni inicializa memoria.',
        'Tiempo cooperativo; informacion sensible oculta puede no ser reconocible.',
        'Ubicacion legacy docs/memory; usar --memory-root para ubicaciones alternativas dentro del repositorio.']
    report['next'] = 'Revisar fuentes cambiadas y conflictos; confirmar tarea y alcance antes de continuar. Toda escritura de memoria requiere aprobacion explicita.'
    return report


def render_start(report):
    return 'NEOPRARS PROJECT MEMORY V1.3 — START (recomendado, solo lectura)\n' + json.dumps(report, ensure_ascii=True, indent=2)
