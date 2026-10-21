"""Read-only entry point. No persistence, repair, exports or repository execution."""
import hashlib
import codecs
import json
import math
import re
import time
from pathlib import Path

from audit import EXCLUDED, SENSITIVE, LINK, DOC_EXT, LANGUAGES, safe_label, discover_root, audit_project, git_read
from safety import safe_path
from rules import RULES_FILE, empty_document, load_text, public


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
        item = {'path': safe_label(path.as_posix()), 'status': 'omitida', 'bytes_read': 0,
                'coverage_class': 'omitida', 'integrity': 'no verificable'}
        self.coverage.append(item)
        data = bytearray()
        hasher = hashlib.sha256()
        try:
            before = path.stat()
            item['size_bytes'] = before.st_size
            if self.opened >= self.documents:
                item['reason'] = 'presupuesto de documentos agotado'
                item['coverage_class'] = 'limitada'
                return None, item
            if self.spent >= self.bytes or time.monotonic() >= self.deadline:
                item['reason'] = 'presupuesto global de bytes o tiempo agotado'
                item['coverage_class'] = 'limitada'
                return None, item
            self.opened += 1
            with path.open('rb', buffering=0) as stream:
                while item['bytes_read'] < before.st_size:
                    if time.monotonic() >= self.deadline:
                        item['reason'] = 'presupuesto de tiempo agotado'
                        item['coverage_class'] = 'limitada'
                        break
                    amount = min(self.chunk_size, self.bytes - self.spent,
                                 self.document_bytes - item['bytes_read'],
                                 before.st_size - item['bytes_read'])
                    if amount <= 0:
                        item['reason'] = 'presupuesto de bytes agotado'
                        item['coverage_class'] = 'limitada'
                        break
                    chunk = stream.read(amount)
                    if not chunk:
                        item['reason'] = 'archivo cambiado; no verificable'
                        item['coverage_class'] = 'incompleta'
                        break
                    self.spent += len(chunk)
                    item['bytes_read'] += len(chunk)
                    if b'\x00' in chunk:
                        item['reason'] = 'binario omitido'
                        item['coverage_class'] = 'incompleta'
                        return None, item
                    hasher.update(chunk)
                    if not digest:
                        data.extend(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                item['reason'] = 'archivo cambiado; no verificable'
                item['coverage_class'] = 'incompleta'
                return None, item
            complete = item['bytes_read'] == before.st_size and 'reason' not in item
            item['status'] = 'completa' if complete else ('parcial' if item['bytes_read'] else 'omitida')
            item['reason'] = item.get('reason', 'fin del archivo')
            if complete:
                item['coverage_class'] = 'completa'
                item['integrity'] = 'verificada por lectura estable'
            elif item.get('coverage_class') not in ('limitada', 'incompleta'):
                item['coverage_class'] = 'incompleta'
            if digest:
                return hasher.hexdigest() if complete else None, item
            try:
                decoder = codecs.getincrementaldecoder('utf-8-sig')()
                text = decoder.decode(data, final=complete)
            except UnicodeError:
                item['status'] = 'omitida'
                item['coverage_class'] = 'incompleta'
                item['integrity'] = 'no verificable'
                item['reason'] = 'codificacion desconocida'
                return None, item
            if not complete:
                text = text[:text.rfind('\n') + 1]
            item['lines_analysed'] = len(text.splitlines())
            return text, item
        except OSError:
            item['reason'] = 'archivo inaccesible'
            item['coverage_class'] = 'incompleta'
            return None, item

    def stream_sources(self, path):
        """Parse sources.json incrementally while hashing and checking stability.

        Only source entries are materialized. Other top-level values, including
        large revisions arrays, are skipped structurally. The complete byte
        stream is still bounded, UTF-8 validated, hashed, and checked against
        pre/post stat metadata.
        """
        item = {'path': safe_label(path.as_posix()), 'status': 'omitida',
                'bytes_read': 0, 'coverage_class': 'omitida',
                'integrity': 'no verificable', 'sources_read': 0}
        self.coverage.append(item)
        decoder = codecs.getincrementaldecoder('utf-8-sig')()
        decoder_buffer = ''
        source_values = []
        hasher = hashlib.sha256()
        try:
            before = path.stat()
            item['size_bytes'] = before.st_size
            if self.opened >= self.documents:
                item['reason'] = 'presupuesto de documentos agotado'
                item['coverage_class'] = 'limitada'
                return None, item
            if self.spent >= self.bytes or time.monotonic() >= self.deadline:
                item['reason'] = 'presupuesto global de bytes o tiempo agotado'
                item['coverage_class'] = 'limitada'
                return None, item
            self.opened += 1
            with path.open('rb', buffering=0) as stream:
                def chunks():
                    while item['bytes_read'] < before.st_size:
                        if time.monotonic() >= self.deadline:
                            item['reason'] = 'presupuesto de tiempo agotado'
                            item['coverage_class'] = 'limitada'
                            return
                        # sources.json is parsed incrementally; its per-file
                        # cap is the global byte budget, while the stream
                        # parser keeps memory bounded to current tokens.
                        amount = min(self.chunk_size, self.bytes - self.spent,
                                     before.st_size - item['bytes_read'])
                        if amount <= 0:
                            item['reason'] = 'presupuesto de bytes agotado'
                            item['coverage_class'] = 'limitada'
                            return
                        chunk = stream.read(amount)
                        if not chunk:
                            item['reason'] = 'archivo cambiado; no verificable'
                            item['coverage_class'] = 'incompleta'
                            return
                        self.spent += len(chunk)
                        item['bytes_read'] += len(chunk)
                        hasher.update(chunk)
                        try:
                            text = decoder.decode(chunk, final=False)
                        except UnicodeError:
                            item['reason'] = 'codificacion desconocida'
                            item['coverage_class'] = 'incompleta'
                            return
                        yield text
                    try:
                        tail = decoder.decode(b'', final=True)
                    except UnicodeError:
                        item['reason'] = 'codificacion desconocida'
                        item['coverage_class'] = 'incompleta'
                        return
                    if tail:
                        yield tail

                class Cursor:
                    def __init__(self):
                        self.buffer = ''
                        self.position = 0
                        self.iterator = chunks()
                        self.finished = False

                    def fill(self, minimum=1):
                        while len(self.buffer) - self.position < minimum and not self.finished:
                            try:
                                part = next(self.iterator)
                            except StopIteration:
                                self.finished = True
                                break
                            self.buffer = self.buffer[self.position:] + part
                            self.position = 0
                        return len(self.buffer) - self.position >= minimum

                    def peek(self):
                        return self.buffer[self.position] if self.fill() else ''

                    def take(self):
                        if not self.fill():
                            return ''
                        char = self.buffer[self.position]
                        self.position += 1
                        return char

                    def whitespace(self):
                        while self.peek() and self.peek().isspace():
                            self.take()

                    def raw_value(self):
                        self.whitespace()
                        chars = []
                        depth = 0
                        quoted = False
                        escaped = False
                        begun = False
                        while True:
                            char = self.peek()
                            if not char:
                                if self.finished:
                                    break
                                continue
                            if quoted:
                                chars.append(self.take())
                                if escaped:
                                    escaped = False
                                elif char == '\\':
                                    escaped = True
                                elif char == '"':
                                    quoted = False
                                    if depth == 0:
                                        break
                                continue
                            if char == '"':
                                quoted = True
                                begun = True
                                chars.append(self.take())
                                continue
                            if char in '[{':
                                depth += 1
                                begun = True
                                chars.append(self.take())
                                continue
                            if char in ']}':
                                depth -= 1
                                chars.append(self.take())
                                if begun and depth == 0:
                                    break
                                continue
                            if depth == 0 and begun and char in ',}]':
                                break
                            if depth == 0 and not begun and char in ',}]':
                                raise ValueError('JSON value missing')
                            begun = True
                            chars.append(self.take())
                        value = ''.join(chars).strip()
                        if not value:
                            raise ValueError('JSON value missing')
                        return value

                cursor = Cursor()
                cursor.whitespace()
                if cursor.take() != '{':
                    raise ValueError('sources.json no es un objeto')
                decoder_json = json.JSONDecoder()
                registry_schema = None
                while True:
                    cursor.whitespace()
                    if cursor.peek() == '}':
                        cursor.take()
                        break
                    key = decoder_json.decode(cursor.raw_value())
                    cursor.whitespace()
                    if cursor.take() != ':':
                        raise ValueError('JSON invalido')
                    cursor.whitespace()
                    if key == 'sources':
                        if cursor.take() != '[':
                            raise ValueError('sources no es una lista')
                        while True:
                            cursor.whitespace()
                            if cursor.peek() == ']':
                                cursor.take()
                                break
                            value = decoder_json.decode(cursor.raw_value())
                            if not isinstance(value, dict):
                                raise ValueError('fuente invalida')
                            source_values.append(value)
                            item['sources_read'] += 1
                            cursor.whitespace()
                            separator = cursor.take()
                            if separator == ']':
                                break
                            if separator != ',':
                                raise ValueError('JSON invalido')
                    else:
                        value = decoder_json.decode(cursor.raw_value())
                        if key == 'schema_version':
                            registry_schema = value
                    cursor.whitespace()
                    separator = cursor.take()
                    if separator == '}':
                        break
                    if separator != ',':
                        raise ValueError('JSON invalido')
                cursor.whitespace()
                if cursor.peek():
                    raise ValueError('contenido posterior a sources.json')
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    item['reason'] = 'archivo cambiado; no verificable'
                    item['coverage_class'] = 'incompleta'
                    return None, item
            item['status'] = 'completa'
            item['coverage_class'] = 'completa'
            item['integrity'] = 'verificada por lectura estable'
            item['reason'] = 'fin del archivo'
            item['sha256'] = hasher.hexdigest()
            return {'schema_version': registry_schema, 'sources': source_values}, item
        except (OSError, UnicodeError, ValueError, RecursionError):
            item.setdefault('reason', 'JSON invalido o lectura incompleta')
            item['coverage_class'] = item.get('coverage_class') or 'incompleta'
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
    report = {'version': '1.5.0', 'command': 'start', 'mode': mode, 'read_only': True,
              'root': safe_label(root.name), 'root_evidence': root_evidence,
              'memory_path': label(location), 'memory_status': 'desconocida',
              'partial': False, 'index': {}, 'sources': [], 'references': [],
              'decisions': [], 'conflicts': [], 'pending': [], 'issues': [],
              'git': {'status': 'no verificable'},
              'requires_explicit_approval_for_writes': True,
              'project_rules': public(empty_document())}
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
    rules_path = allowed_path(root, (folder / RULES_FILE).relative_to(root).as_posix(), exclusions, max_depth)
    if rules_path is not None and rules_path.is_file():
        rules_text, rules_coverage = budget.read(rules_path)
        if rules_text is None or rules_coverage.get('status') != 'completa':
            report['issues'].append('PROJECT_RULES.json incompleto; se usan reglas predeterminadas.')
        else:
            try:
                report['project_rules'] = public(load_text(rules_text))
            except ValueError as exc:
                report['issues'].append(str(exc) + ' Se usan reglas predeterminadas.')
    index, coverage = read_file('INDEX.md')
    report['index'] = {'status': coverage['status'] if coverage else 'ausente'}
    reference_targets = []
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
            reference = {'path': safe_label(target), 'status':
                'omitida por seguridad' if path is None else ('presente' if path.exists() else 'ausente')}
            report['references'].append(reference)
            reference_targets.append((reference, path))
    registry_path = allowed_path(root, (folder/'sources.json').relative_to(root).as_posix(), exclusions, max_depth)
    if registry_path is not None and registry_path.is_file():
        registry, coverage = budget.stream_sources(registry_path)
        if coverage:
            coverage['path'] = safe_label(registry_path.relative_to(root).as_posix())
        if registry is None and coverage and coverage.get('coverage_class') == 'limitada':
            report['issues'].append('JSON parcial: sources.json limitado por presupuesto; no se certifican fuentes omitidas.')
        elif registry is None:
            report['issues'].append('sources.json incompleto o invalido; no se repara.')
    else:
        registry, coverage = None, None
        report['issues'].append('Fuente de memoria omitida o ausente: sources.json')
    source_ids = {}
    source_results = {}
    source_paths = {}
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
            if isinstance(relative, str):
                source_paths[relative.replace('\\', '/')] = result
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
    remaining = deadline - time.monotonic()
    git_output = git_read(root, 'status', '--short', '--untracked-files=normal',
                          timeout=min(5, remaining)) if remaining > 0 else None
    changed_paths = set()
    if git_output is None:
        report['git'] = {'status': 'no verificable'}
    else:
        for line in git_output.splitlines():
            value = line[3:].strip() if len(line) >= 3 else line.strip()
            if ' -> ' in value:
                value = value.split(' -> ', 1)[-1]
            changed_paths.add(value.replace('\\', '/'))
        report['git'] = {'status': 'verificable', 'changed_paths': [safe_label(x) for x in sorted(changed_paths)]}
    for relative, result in source_paths.items():
        status = result.get('status', '')
        result['freshness'] = ('confirmada' if status == 'verificada por huella' and relative not in changed_paths else
                               'posiblemente obsoleta' if 'cambiada' in status or relative in changed_paths else
                               'no verificable')
    for reference, path in reference_targets:
        relative = None
        if path is not None:
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                pass
        source = source_paths.get(relative) if relative else None
        reference['freshness'] = source.get('freshness') if source else 'no verificable'
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
    partial_reasons = []
    if report['issues']:
        partial_reasons.append('issues de lectura o formato')
    if any(x['status'] != 'completa' for x in budget.coverage):
        partial_reasons.append('documentos limitados o incompletos')
    unverified_sources = sum(x['status'] != 'verificada por huella' for x in report['sources'])
    stale_sources = sum(x.get('freshness') != 'confirmada' for x in report['sources'])
    if unverified_sources:
        partial_reasons.append('fuentes no verificadas: {}'.format(unverified_sources))
    if stale_sources:
        partial_reasons.append('frescura no confirmada: {}'.format(stale_sources))
    report['partial_reasons'] = partial_reasons
    report['partial'] = bool(partial_reasons)
    report['limitations'] = ['No copia el indice ni detalles arbitrarios; muestra metadatos y titulos filtrados.',
        'Una huella coincidente no verifica decisiones, implementacion ni resultados de pruebas.',
        'No resuelve conflictos por fecha, no repara archivos ni inicializa memoria.',
        'Tiempo cooperativo; informacion sensible oculta puede no ser reconocible.',
        'Ubicacion legacy docs/memory; usar --memory-root para ubicaciones alternativas dentro del repositorio.']
    report['next'] = 'Revisar fuentes cambiadas y conflictos; confirmar tarea y alcance antes de continuar. Toda escritura de memoria requiere aprobacion explicita.'
    return report


def render_start(report):
    return 'NEOPRARS PROJECT MEMORY V1.5.0 — START (recomendado, solo lectura)\n' + json.dumps(report, ensure_ascii=True, indent=2)
