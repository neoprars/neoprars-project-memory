"""Provider-neutral project rules and their persisted project exceptions."""
import copy
import json
import re

RULES_FILE = 'PROJECT_RULES.json'

TASK_TRIGGERS = {
    'protect-main': ('main', 'trunk'),
    'database-changes': ('database', 'base de datos', 'migration', 'migracion', 'schema', 'esquema', 'sql'),
    'git-operations': ('commit', 'push', 'merge', 'rebase', 'tag', 'force push'),
    'data-protection': ('secret', 'secreto', 'password', 'contraseña', 'token', 'credential', 'credencial', 'personal data', 'datos personales'),
    'tests': ('skip test', 'sin pruebas', 'no tests', 'no ejecutar pruebas'),
    'scope-control': ('fuera del alcance', 'out of scope', 'tocar neoprarsposventa', 'touch neoprarsposventa'),
    'minimal-changes': ('rewrite', 'reescribir todo', 'refactor completo', 'rebuild'),
}

DEFAULT_RULES = (
    ('protect-main', 'Proteger main', 'No trabajar directamente sobre main; usar una rama y revisar el estado antes de cambiarlo.'),
    ('minimal-changes', 'Cambios mínimos', 'Modificar sólo lo necesario para el objetivo acordado y conservar el comportamiento existente.'),
    ('reuse-first', 'Reutilización', 'Buscar y reutilizar código, comandos, formatos y capacidades existentes antes de crear duplicados.'),
    ('database-changes', 'Cambios de base de datos', 'Tratar cambios de esquema, migraciones y datos como de alto impacto; revisar respaldo, reversibilidad y pruebas antes de aplicarlos.'),
    ('scope-control', 'Alcance', 'Respetar el alcance solicitado; no ampliar la tarea ni tocar componentes excluidos sin autorización explícita.'),
    ('stability', 'Estabilidad', 'Preservar compatibilidad y estabilidad; preferir cambios reversibles y declarar los riesgos conocidos.'),
    ('tests', 'Pruebas', 'Ejecutar sólo pruebas dirigidas y declarar qué se ejecutó, qué no y el resultado.'),
    ('git-operations', 'Operaciones Git', 'No hacer commit, merge, rebase, tag, push ni cambiar de rama sin autorización explícita.'),
    ('data-protection', 'Protección de datos', 'No exponer secretos, credenciales, datos personales ni contenido sensible en salidas, registros o propuestas.'),
    ('temporaries', 'Temporales', 'Usar archivos temporales acotados y seguros; limpiarlos al terminar sin borrar datos del usuario.'),
    ('decision-log', 'Registro de decisiones', 'Registrar decisiones, motivos, alternativas y pruebas sólo cuando estén confirmados y con antecedentes preservados.'),
)


def _rule(rule_id, title, text, *, enabled=True, default=False, custom=False):
    return {'id': rule_id, 'title': title, 'text': text, 'enabled': bool(enabled),
            'default': bool(default), 'custom': bool(custom)}


def defaults():
    return [_rule(rule_id, title, text, default=True) for rule_id, title, text in DEFAULT_RULES]


def empty_document():
    return {'schema_version': 1, 'overrides': {}, 'custom': [], 'deleted_custom': []}


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) is not None


def _valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 20000 and '\x00' not in value


def validate_document(document):
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('Formato de PROJECT_RULES.json desconocido.')
    overrides = document.get('overrides', {})
    custom = document.get('custom', [])
    deleted_custom = document.get('deleted_custom', [])
    if (not isinstance(overrides, dict) or not isinstance(custom, list) or
            not isinstance(deleted_custom, list) or len(custom) + len(deleted_custom) > 500):
        raise ValueError('Excepciones de reglas invalidas.')
    known = {item['id'] for item in defaults()}
    custom_ids = set()
    for rule_id, override in overrides.items():
        if not _valid_id(rule_id) or not isinstance(override, dict) or set(override) - {'title', 'text', 'enabled', 'deleted'}:
            raise ValueError('Excepcion de regla invalida.')
        if rule_id not in known and rule_id not in custom_ids:
            # Unknown overrides are retained only if they describe an existing custom rule.
            raise ValueError('Excepcion de regla desconocida.')
        if 'title' in override and not _valid_text(override['title']):
            raise ValueError('Titulo de regla invalido.')
        if 'text' in override and not _valid_text(override['text']):
            raise ValueError('Texto de regla invalido.')
        if 'enabled' in override and not isinstance(override['enabled'], bool):
            raise ValueError('Estado de regla invalido.')
        if 'deleted' in override and not isinstance(override['deleted'], bool):
            raise ValueError('Eliminacion de regla invalida.')
    for item in custom + deleted_custom:
        if not isinstance(item, dict) or set(item) != {'id', 'title', 'text', 'enabled'}:
            raise ValueError('Regla personalizada invalida.')
        if not _valid_id(item['id']) or item['id'] in known or item['id'] in custom_ids:
            raise ValueError('ID de regla personalizado invalido o duplicado.')
        if not _valid_text(item['title']) or not _valid_text(item['text']) or not isinstance(item['enabled'], bool):
            raise ValueError('Contenido de regla personalizado invalido.')
        custom_ids.add(item['id'])
    for rule_id in overrides:
        if rule_id not in known and rule_id not in custom_ids:
            raise ValueError('Excepcion de regla desconocida.')


def normalize(document):
    validate_document(document)
    return copy.deepcopy(document)


def effective(document=None):
    document = normalize(document or empty_document())
    result = {item['id']: item for item in defaults()}
    for item in document['custom']:
        result[item['id']] = _rule(item['id'], item['title'], item['text'], enabled=item['enabled'], custom=True)
    for rule_id, override in document['overrides'].items():
        if rule_id not in result:
            continue
        if override.get('deleted'):
            result.pop(rule_id, None)
            continue
        result[rule_id] = {**result[rule_id], **{key: value for key, value in override.items() if key != 'deleted'}}
    return sorted(result.values(), key=lambda item: item['id'])


def load_text(text):
    if text is None or not text.strip():
        return empty_document()
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        raise ValueError('PROJECT_RULES.json no contiene JSON valido.')
    return normalize(value)


def operation(document, action, rule_id, *, title=None, text=None, enabled=None):
    document = normalize(document or empty_document())
    defaults_by_id = {item['id']: item for item in defaults()}
    custom_by_id = {item['id']: item for item in document['custom']}
    if not _valid_id(rule_id):
        raise ValueError('ID de regla invalido.')
    if action == 'add':
        if rule_id in defaults_by_id or rule_id in custom_by_id or any(item['id'] == rule_id for item in document['deleted_custom']):
            raise ValueError('La regla ya existe; usar modify.')
        if not _valid_text(title) or not _valid_text(text):
            raise ValueError('Agregar requiere titulo y texto.')
        document['custom'].append({'id': rule_id, 'title': title, 'text': text, 'enabled': True})
    elif action == 'modify':
        if rule_id not in defaults_by_id and rule_id not in custom_by_id:
            raise ValueError('Regla desconocida.')
        if rule_id in custom_by_id:
            item = next(item for item in document['custom'] if item['id'] == rule_id)
            if title is not None: item['title'] = title
            if text is not None: item['text'] = text
            if title is None and text is None: raise ValueError('Modificar requiere titulo o texto.')
        else:
            override = document['overrides'].setdefault(rule_id, {})
            if title is not None: override['title'] = title
            if text is not None: override['text'] = text
            if title is None and text is None: raise ValueError('Modificar requiere titulo o texto.')
    elif action in ('deactivate', 'activate'):
        if rule_id not in defaults_by_id and rule_id not in custom_by_id:
            raise ValueError('Regla desconocida.')
        if rule_id in custom_by_id:
            next(item for item in document['custom'] if item['id'] == rule_id)['enabled'] = action == 'activate'
        else:
            document['overrides'].setdefault(rule_id, {})['enabled'] = action == 'activate'
    elif action == 'delete':
        if rule_id in custom_by_id:
            document['custom'] = [item for item in document['custom'] if item['id'] != rule_id]
            document['deleted_custom'].append(custom_by_id[rule_id])
        elif rule_id in defaults_by_id:
            document['overrides'][rule_id] = {'deleted': True}
        else:
            raise ValueError('Regla desconocida.')
    elif action == 'restore':
        if rule_id in defaults_by_id:
            document['overrides'].pop(rule_id, None)
        elif rule_id in custom_by_id:
            for item in document['custom']:
                if item['id'] == rule_id: item['enabled'] = True
        elif any(item['id'] == rule_id for item in document['deleted_custom']):
            restored = next(item for item in document['deleted_custom'] if item['id'] == rule_id)
            document['deleted_custom'] = [item for item in document['deleted_custom'] if item['id'] != rule_id]
            document['custom'].append(restored)
        else:
            raise ValueError('Regla desconocida.')
    else:
        raise ValueError('Operacion de reglas desconocida.')
    validate_document(document)
    return document


def public(document=None):
    document = normalize(document or empty_document())
    return {'schema_version': 1, 'rules': effective(document),
            'project_exceptions': document}


def check_task(project_context, task, scopes=()):
    """Check a task against loaded rules and currently verifiable decisions.

    This is intentionally a conservative, read-only gate. It reports possible
    conflicts and asks the calling agent for authorization; it never authorizes
    or executes the task itself.
    """
    if not isinstance(task, str) or not task.strip() or len(task) > 20000:
        raise ValueError('La tarea requerida es invalida.')
    context = project_context if isinstance(project_context, dict) else {}
    rules_data = context.get('project_rules') or public(empty_document())
    rules = rules_data.get('rules', []) if isinstance(rules_data, dict) else []
    normalized = task.casefold()
    checks = []
    conflicts = []
    for rule in rules:
        rule_id = rule.get('id')
        enabled = rule.get('enabled') is True
        terms = TASK_TRIGGERS.get(rule_id, ())
        triggered = enabled and any(term in normalized for term in terms)
        check = {'rule_id': rule_id, 'title': rule.get('title'),
                 'status': 'review_required' if triggered else ('disabled' if not enabled else 'not_triggered')}
        if triggered:
            reason = 'La tarea activa la regla "{}".'.format(rule.get('title', rule_id))
            if rule_id == 'protect-main':
                reason += ' Confirmar que no se trabajará directamente sobre main/trunk.'
            elif rule_id == 'git-operations':
                reason += ' Las operaciones Git sensibles requieren autorización explícita.'
            elif rule_id == 'database-changes':
                reason += ' Confirmar respaldo, reversibilidad y pruebas dirigidas.'
            elif rule_id == 'data-protection':
                reason += ' Confirmar que no se expondrán datos sensibles.'
            elif rule_id == 'scope-control':
                reason += ' Confirmar que el alcance y las exclusiones fueron autorizados.'
            conflicts.append({'type': 'rule', 'rule_id': rule_id, 'reason': reason})
        checks.append(check)
    for decision in context.get('decisions', []):
        if decision.get('effective_status') not in ('sustituida; antecedente preservado', 'no_aplica'):
            status = 'review_required' if decision.get('review_required') else 'vigente'
            checks.append({'decision_id': decision.get('id'), 'title': decision.get('title'), 'status': status})
            if decision.get('review_required'):
                conflicts.append({'type': 'decision', 'decision_id': decision.get('id'),
                                  'reason': 'La decisión vigente requiere revisión de evidencia o autoridad.'})
    for conflict in context.get('conflicts', []):
        if conflict.get('resolution') == 'pendiente' and conflict.get('resolves') is None:
            conflicts.append({'type': 'project_conflict', 'conflict_id': conflict.get('id'),
                              'reason': 'Existe un conflicto del proyecto sin resolución registrada.'})
    if scopes:
        checks.append({'scope': list(scopes), 'status': 'provided_for_review'})
    return {'command': 'preflight', 'read_only': True, 'task': task,
            'status': 'authorization_required' if conflicts else 'ready',
            'rules': rules_data, 'checks': checks, 'conflicts': conflicts,
            'authorization_question': ('¿Autorizás continuar pese a estos conflictos, confirmando el alcance y las excepciones?'
                                        if conflicts else None),
            'next': ('Solicitar autorización explícita antes de continuar.' if conflicts
                     else 'Continuar la tarea respetando las reglas activas y decisiones vigentes.')}


def render_preflight(report):
    return 'NEOPRARS PROJECT MEMORY — PREFLIGHT (solo lectura)\n' + json.dumps(
        report, ensure_ascii=False, indent=2)
