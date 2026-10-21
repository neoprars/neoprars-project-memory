"""Read-only proposals and explicit, optimistic supervised persistence."""
import datetime as dt
import hashlib
import json
import re
import uuid
import os
import time
from contextlib import contextmanager

from audit import EXCLUDED, SENSITIVE
from safety import ensure_safe
from store import scrub
from rules import RULES_FILE, validate_document

KINDS = ('initialize', 'source', 'rules', 'close', 'decision', 'conflict', 'resolution')
EVENT_FILES = {'decision': 'decisions.jsonl', 'conflict': 'conflicts.jsonl',
               'resolution': 'conflicts.jsonl'}
INIT_FILES = {
    'INDEX.md': '# Memoria del proyecto\n\n[Fuentes](sources.json) · [Reglas](RULES.md) · [Formatos](FORMATS.md)\n',
    'RULES.md': '# Reglas\n\nLa memoria complementa las fuentes oficiales. Registrar evidencia y aprobación.\n'
        'Cambios de fuentes requieren revisión; no resolver por fecha. Conservar antecedentes.\n',
    'FORMATS.md': '# Formatos\n\nsources.json: schema_version=1, sources y revisions.\n'
        'Eventos: schema_version=2, id, date, classification, status, evidence y approval.\n'
        'Decisiones usan supersedes; resoluciones usan resolves, choice y reason.\n'
        'Conflictos conservan alternatives con id, claim y evidence.\n'
        'Sesiones conservan tests_status, decisions y conflicts, además de los campos legacy.\n',
    'sources.json': '{"schema_version": 1, "sources": [], "revisions": []}\n',
    'decisions.jsonl': '', 'conflicts.jsonl': '', 'pending.jsonl': '', 'bugs.jsonl': ''}


class ConcurrentChangeError(ValueError):
    """A proposal no longer matches the dependencies it was prepared from."""

    def __init__(self, changes):
        self.changes = changes
        super().__init__('Cambio concurrente en: ' + ', '.join(changes))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise ValueError('ID invalido.')
    return value


def text(value, *, required=False):
    if not isinstance(value, str) or len(value) > 20000 or '\x00' in value:
        raise ValueError('Texto invalido o demasiado largo.')
    if required and not value.strip():
        raise ValueError('Falta contenido requerido.')
    if scrub(value) != value:
        raise ValueError('Contenido potencialmente sensible; revisar antes de registrar.')
    return value


def evidence(items):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('Evidencia invalida.')
    for item in items:
        if not isinstance(item, dict) or set(item) - {'source_id', 'sha256', 'lines'}:
            raise ValueError('Referencia invalida.')
        ident(item.get('source_id'))
        if 'sha256' in item and not re.fullmatch(r'[0-9a-f]{64}', str(item['sha256'])):
            raise ValueError('Huella invalida.')
        lines = item.get('lines')
        if lines is not None and (not isinstance(lines, list) or len(lines) != 2
            or any(type(n) is not int or n <= 0 for n in lines) or lines[0] > lines[1]):
            raise ValueError('Rango de lineas invalido.')
    return items


def validate(kind, data):
    if kind not in KINDS or not isinstance(data, dict):
        raise ValueError('Propuesta invalida.')
    if kind == 'initialize':
        if data != {'files': INIT_FILES}:
            raise ValueError('Inicializacion requiere los formatos revisados exactos.')
        return
    if kind == 'source':
        if set(data) != {'id', 'path', 'sha256', 'authority', 'reason'}:
            raise ValueError('Formato de fuente invalido.')
        ident(data['id'])
        text(data['path'], required=True)
        text(data['reason'], required=True)
        if data['authority'] not in ('candidata', 'confirmada', 'excluida') or not re.fullmatch(r'[0-9a-f]{64}', str(data['sha256'])):
            raise ValueError('Metadata de fuente invalida.')
        return
    if kind == 'rules':
        if set(data) != {'operation', 'document'} or not isinstance(data['operation'], str):
            raise ValueError('Formato de reglas invalido.')
        validate_document(data['document'])
        return
    common = {'schema_version', 'id', 'date', 'created_at', 'classification', 'status', 'evidence'}
    extra = {'close': {'module','done','pending','tests','files','tests_status','decisions','conflicts'},
        'decision': {'title','reason','detail','supersedes'},
        'conflict': {'title','conflict_type','alternatives','resolution'},
        'resolution': {'title','resolves','choice','reason','resolution'}}[kind]
    if set(data) != common | extra or data['schema_version'] != 2:
        raise ValueError('Formato de evento invalido.')
    ident(data['id'])
    dt.date.fromisoformat(data['date'])
    if dt.datetime.fromisoformat(data['created_at']).tzinfo is None:
        raise ValueError('La fecha de creacion requiere zona horaria.')
    if data['classification'] not in ('confirmado','historico','propuesto','desconocido') or data['status'] not in ('pendiente','implementado','validado','cerrado','no_aplica'):
        raise ValueError('Clasificacion o estado invalido.')
    evidence(data['evidence'])
    if kind == 'close':
        for key in ('module','done','pending','tests','files'):
            text(data[key], required=key in ('module','done'))
        if data['tests_status'] not in ('aprobadas','fallidas','no_ejecutadas','desconocidas'):
            raise ValueError('Resultado de pruebas invalido.')
        if data['tests_status'] in ('aprobadas','fallidas') and not data['tests'].strip():
            raise ValueError('Un resultado de pruebas requiere detalle.')
        for key in ('decisions','conflicts'):
            if not isinstance(data[key], list) or len(data[key]) > 100:
                raise ValueError('Relaciones invalidas.')
            for ref in data[key]:
                ident(ref)
    elif kind == 'decision':
        for key in ('title','reason','detail'):
            text(data[key], required=True)
        if data['supersedes'] is not None:
            ident(data['supersedes'])
    elif kind == 'conflict':
        text(data['title'], required=True)
        if data['conflict_type'] not in ('contenido','autoridad','historico','evidencia','formato') or data['resolution'] is not None:
            raise ValueError('Conflicto invalido.')
        options = data['alternatives']
        if not isinstance(options, list) or not 2 <= len(options) <= 20:
            raise ValueError('Se requieren al menos dos alternativas.')
        ids = set()
        for option in options:
            if not isinstance(option, dict) or set(option) != {'id','claim','evidence'}:
                raise ValueError('Alternativa invalida.')
            oid = ident(option['id'])
            if oid in ids:
                raise ValueError('Alternativa duplicada.')
            ids.add(oid)
            text(option['claim'], required=True)
            evidence(option['evidence'])
    else:
        text(data['title'], required=True)
        text(data['reason'], required=True)
        ident(data['resolves'])
        ident(data['choice'])
        if data['resolution'] != 'supervisada':
            raise ValueError('Resolucion invalida.')


def snapshot(store, relative, *, official=False):
    if store.entries >= store.limits['max_files'] or time.monotonic() >= store.deadline:
        raise ValueError('Presupuesto de entradas o tiempo agotado.')
    store.entries += 1
    path = ensure_safe(store.root, relative, exclusions=store.exclusions, sensitive=SENSITIVE,
        max_depth=store.limits['max_depth']) if official else store.path(relative)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('Destino existente no es archivo.')
    value, coverage = store.budget.read(path, digest=True)
    if coverage['status'] != 'completa' or value is None:
        raise ValueError('Estado no verificable dentro del presupuesto.')
    return value


def existing_events(store, filename):
    path = store.path(filename)
    if not path.exists():
        return []
    records = store.objects(filename, jsonl=True)
    if store.issues:
        raise ValueError('Registros incompletos o invalidos; revisar antes de escribir.')
    ids = [x.get('id') for x in records if x.get('id') is not None]
    if any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('IDs invalidos o duplicados.')
    return records


def validate_relations(store, kind, data):
    if kind in ('initialize', 'source', 'rules'):
        return
    decisions = existing_events(store, 'decisions.jsonl')
    conflicts = existing_events(store, 'conflicts.jsonl')
    known = {x['id']: x for x in decisions + conflicts if isinstance(x.get('id'), str)}
    if data['id'] in known:
        raise ValueError('ID ya registrado.')
    if kind == 'decision' and data['supersedes'] and not any(x.get('id') == data['supersedes'] for x in decisions):
        raise ValueError('Decision sustituida desconocida.')
    if kind == 'close':
        for key, records in (('decisions', decisions), ('conflicts', conflicts)):
            if any(ref not in {x.get('id') for x in records if isinstance(x.get('id'), str)} for ref in data[key]):
                raise ValueError('Relacion desconocida en cierre.')
    if kind == 'resolution':
        conflict = known.get(data['resolves'])
        if not conflict or not isinstance(conflict.get('alternatives'), list):
            raise ValueError('Conflicto desconocido o legacy sin alternativas.')
        options = {x.get('id'): x for x in conflict['alternatives'] if isinstance(x, dict) and isinstance(x.get('id'), str)}
        if data['choice'] not in options:
            raise ValueError('Alternativa desconocida.')
        if any(x.get('resolves') == data['resolves'] for x in conflicts):
            raise ValueError('Conflicto ya resuelto; crear nuevo conflicto para revisar.')
        selected = options[data['choice']].get('evidence', [])
        if not selected or not data['evidence']:
            raise ValueError('Resolucion requiere evidencia de la alternativa y de la eleccion.')


def source_registry(store, *, allow_stream=False):
    if not store.path('sources.json').exists():
        return {'schema_version': 1, 'sources': [], 'revisions': []}
    if allow_stream:
        value = store.stream_sources()
        if value is None:
            raise ValueError('Registro de fuentes no verificable.')
        values = [value]
    else:
        values = store.objects('sources.json')
        if store.issues or len(values) != 1:
            raise ValueError('Registro de fuentes no verificable.')
    value = values[0]
    if value.get('schema_version') != 1 or not isinstance(value.get('sources'), list) or not isinstance(value.get('revisions', []), list):
        raise ValueError('Formato de fuentes desconocido.')
    ids = [x.get('id') for x in value['sources'] if isinstance(x, dict)]
    if len(ids) != len(value['sources']) or any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('Fuentes invalidas o duplicadas.')
    return value


def expected_state(store, kind, data, *, strict_evidence=True):
    if kind == 'initialize':
        memory = INIT_FILES
    elif kind == 'source':
        memory = {'sources.json': ''}
    elif kind == 'rules':
        memory = {RULES_FILE: ''}
    else:
        # Event appenders do not overwrite unrelated records. Dependency-level
        # snapshots below protect only referenced records, avoiding false
        # conflicts when another actor appends an independent event.
        memory = {}
    if kind == 'close':
        memory = {**memory, 'sessions/' + data['id'] + '.json': ''}
    state = {'memory': {name: snapshot(store, name) for name in memory}, 'sources': {}, 'records': {}}
    if kind == 'initialize' and any(value is not None for value in state['memory'].values()):
        raise ValueError('No reinicializar memoria existente.')
    if kind == 'initialize' and store.folder.exists():
        raise ValueError('Directorio de memoria existente; revisar manualmente.')
    if kind == 'close' and state['memory']['sessions/' + data['id'] + '.json'] is not None:
        raise ValueError('Sesion ya registrada.')
    if kind == 'rules':
        return state
    if kind == 'source':
        current = snapshot(store, data['path'], official=True)
        if current != data['sha256']:
            raise ValueError('La huella propuesta no coincide con la fuente actual.')
        from audit import DOC_EXT, LANGUAGES
        if ensure_safe(store.root, data['path']).suffix.lower() not in DOC_EXT | set(LANGUAGES) | {'.json','.xml','.toml','.yml','.yaml','.csproj','.fsproj','.vbproj','.sln','.slnx','.ps1','.sh'}:
            raise ValueError('Tipo de fuente no permitido.')
        state['sources'][data['path']] = current
        source_registry(store)
    elif kind != 'initialize':
        proofs = list(data['evidence'])
        if kind == 'conflict':
            proofs += [proof for option in data['alternatives'] for proof in option['evidence']]
        if kind == 'resolution':
            conflicts = existing_events(store, 'conflicts.jsonl')
            target = next(x for x in conflicts if x.get('id') == data['resolves'])
            proofs += next(x['evidence'] for x in target['alternatives'] if x['id'] == data['choice'])
        registry = source_registry(store, allow_stream=kind in ('close', 'decision', 'conflict', 'resolution')) if proofs else None
        by_id = {x['id']: x for x in registry['sources']} if registry else {}
        if proofs and 'sources.json' not in state['memory']:
            state['memory']['sources.json'] = snapshot(store, 'sources.json')
        dependencies = {}
        if kind == 'close':
            dependencies.update({'decisions.jsonl': data['decisions'], 'conflicts.jsonl': data['conflicts']})
        elif kind == 'decision' and data['supersedes']:
            dependencies['decisions.jsonl'] = [data['supersedes']]
        elif kind == 'resolution':
            dependencies['conflicts.jsonl'] = [data['resolves']]
        for filename, identifiers in dependencies.items():
            records = {record.get('id'): digest(record)
                       for record in existing_events(store, filename)
                       if isinstance(record.get('id'), str)}
            state['records'][filename] = {ident: records.get(ident) for ident in identifiers}
        for proof in proofs:
            src = by_id.get(proof['source_id'])
            if not src or str(src.get('authority', '')).startswith(('excluida','excluded')):
                raise ValueError('Evidencia desconocida o excluida.')
            current = snapshot(store, src.get('path'), official=True)
            if current is not None:
                state['sources'][src['path']] = current
            if strict_evidence and (not current or current != src.get('sha256')
                                    or proof.get('sha256', current) != current):
                raise ValueError('Evidencia desactualizada o no verificable.')
    return state


def state_changes(expected, current):
    """List changed dependency paths without exposing record contents."""
    changes = []
    for group in ('memory', 'sources', 'records'):
        before = expected.get(group, {})
        after = current.get(group, {})
        keys = set(before) | set(after)
        for key in sorted(keys):
            if before.get(key) != after.get(key):
                changes.append(group + '/' + str(key))
    return changes


def prepare(store, kind, data):
    if kind == 'initialize' and not data:
        data = {'files': dict(INIT_FILES)}
    validate(kind, data)
    validate_relations(store, kind, data)
    state = expected_state(store, kind, data)
    # Freeze source revisions in the event so later re-registration cannot make
    # an old decision appear verified against a new version.
    has_evidence = bool(data.get('evidence')) or any(
        proof for option in data.get('alternatives', []) if isinstance(option, dict)
        for proof in option.get('evidence', []))
    if has_evidence:
        registry = source_registry(store, allow_stream=kind in ('close', 'decision', 'conflict', 'resolution'))
        sources = {x['id']: x for x in registry['sources']}
        for proof in data['evidence']:
            proof['sha256'] = sources[proof['source_id']]['sha256']
        if kind == 'conflict':
            for option in data['alternatives']:
                for proof in option['evidence']:
                    proof['sha256'] = sources[proof['source_id']]['sha256']
    proposal = {'schema_version': 1, 'kind': kind, 'project_binding':
        hashlib.sha256(str(store.root).encode('utf-8')).hexdigest(),
        'memory_root': store.location, 'data': data, 'expected': state}
    return {'proposal': proposal, 'approval_sha256': digest(proposal), 'read_only': True,
        'next': 'Revisar propuesta completa; aplicar solo con confirmacion explicita de esta huella.'}


@contextmanager
def writer_lock(store):
    name = '.neoprars-memory-' + hashlib.sha256(os.path.normcase(str(store.folder)).encode('utf-8')).hexdigest()[:16] + '.lock'
    path = ensure_safe(store.root, name)
    try:
        stream = path.open('x', encoding='utf-8')
    except FileExistsError:
        raise ValueError('Otro escritor activo o bloqueo pendiente; revisar antes de reintentar.') from None
    try:
        stream.close()
        yield
    finally:
        ensure_safe(store.root, name).unlink()


def apply(store, envelope, approved_hash, approved_by, mode):
    # Validate authorization before even creating the temporary writer lock.
    if mode != 'supervised' or not approved_by or not isinstance(envelope, dict):
        raise ValueError('Confirmacion supervisada requerida.')
    text(approved_by, required=True)
    if not isinstance(envelope.get('proposal'), dict) or digest(envelope['proposal']) != approved_hash:
        raise ValueError('La propuesta difiere de la huella confirmada.')
    with writer_lock(store):
        return apply_locked(store, envelope, approved_hash, approved_by, mode)


def apply_locked(store, envelope, approved_hash, approved_by, mode):
    if mode != 'supervised':
        raise ValueError('Persistencia requiere modo supervised.')
    text(approved_by, required=True)
    if not re.fullmatch(r'[0-9a-f]{64}', approved_hash or ''):
        raise ValueError('Se requiere la huella confirmada.')
    if not isinstance(envelope, dict) or not isinstance(envelope.get('proposal'), dict):
        raise ValueError('Propuesta invalida.')
    proposal = envelope['proposal']
    if set(proposal) != {'schema_version','kind','project_binding','memory_root','data','expected'} or proposal['schema_version'] != 1:
        raise ValueError('Formato de propuesta desconocido.')
    if digest(proposal) != approved_hash:
        raise ValueError('La propuesta difiere de la huella confirmada.')
    if proposal['project_binding'] != hashlib.sha256(str(store.root).encode('utf-8')).hexdigest() or proposal['memory_root'] != store.location:
        raise ValueError('Propuesta de otro proyecto o ubicacion.')
    kind, data = proposal['kind'], proposal['data']
    validate(kind, data)
    validate_relations(store, kind, data)
    current = expected_state(store, kind, data, strict_evidence=False)
    if current != proposal['expected']:
        raise ConcurrentChangeError(state_changes(proposal['expected'], current) or ['dependencias no identificables'])
    approval = {'proposal_sha256': approved_hash, 'confirmed_by': approved_by,
        'confirmed_at': dt.datetime.now().astimezone().isoformat(), 'mode': 'supervised'}
    if kind == 'initialize':
        for name, contents in data['files'].items():
            store.create(name, contents)
        store.create('initialization.json', json.dumps({'schema_version': 1, 'approval': approval}, ensure_ascii=False, indent=2))
    elif kind == 'source':
        registry = source_registry(store)
        registry.setdefault('revisions', []).append({'sources': registry['sources'], 'approval': approval, 'reason': data['reason']})
        registry['sources'] = [x for x in registry['sources'] if x['id'] != data['id']] + [{**data, 'approval': approval}]
        store.replace_sources(registry)
    elif kind == 'rules':
        payload = data['document']
        temp_name = RULES_FILE + '.tmp'
        store.create(temp_name, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        try:
            os.replace(store.path(temp_name), store.path(RULES_FILE))
        finally:
            temp = store.path(temp_name)
            if temp.exists():
                temp.unlink()
    else:
        event = {**data, 'approval': approval}
        if kind == 'close':
            store.create('sessions/' + data['id'] + '.json', json.dumps(event, ensure_ascii=False, indent=2) + '\n')
        else:
            store.append_event(EVENT_FILES[kind], event)
    return {'saved': True, 'kind': kind, 'id': data.get('id'), 'approval_sha256': approved_hash}


def event_base(args):
    return {'schema_version': 2, 'id': getattr(args, 'id', None) or 'evt-' + str(uuid.uuid4()),
        'date': args.date, 'created_at': dt.datetime.now().astimezone().isoformat(),
        'classification': args.classification, 'status': args.status,
        'evidence': [json.loads(x) for x in args.evidence]}
