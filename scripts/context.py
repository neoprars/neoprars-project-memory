"""Shared, provider-neutral context consolidation for resume and handoff."""


def _latest(sessions):
    if not sessions:
        return None
    return max(sessions, key=lambda item: (str(item.get('date', '')), str(item.get('created_at', ''))))


def _effective(items, *, exclude_status=()):
    excluded = set(exclude_status)
    return [item for item in items
            if item.get('effective_status') != 'sustituida; antecedente preservado'
            and item.get('status') not in excluded]


def _compact(item, fields):
    return {key: item[key] for key in fields if key in item}


def _unique(items, max_items, fields):
    result = []
    seen = set()
    for item in items:
        key = item.get('id') or (item.get('title'), item.get('date'), item.get('kind'))
        if key in seen:
            continue
        seen.add(key)
        result.append(_compact(item, fields))
        if len(result) >= max_items:
            break
    return result, max(0, len(items) - len(result))


def consolidate(context, sessions, *, max_items=100):
    """Synthesize already-read context without inventing or reading new data."""
    if max_items < 1:
        raise ValueError('Limite de contexto invalido.')
    decisions = _effective(context.get('decisions', []), exclude_status=('no_aplica',))
    pending = [item for item in context.get('pending', [])
               if item.get('status') not in ('cerrado', 'no_aplica')]
    conflicts = context.get('conflicts', [])
    unresolved = [item for item in conflicts
                  if item.get('resolution') == 'pendiente' and item.get('resolves') is None]
    risks = [{'kind': 'conflict', 'id': item.get('id'), 'title': item.get('title', 'sin titulo'),
              'reason': 'Conflicto sin resolución registrada.'} for item in unresolved]
    for collection, kind in ((decisions, 'decision'), (pending, 'pending')):
        risks.extend({'kind': kind, 'id': item.get('id'), 'title': item.get('title', 'sin titulo'),
                      'reason': 'Evidencia o vigencia requiere revisión.'}
                     for item in collection if item.get('review_required'))
    risks.extend({'kind': 'coverage', 'title': 'Cobertura', 'reason': issue}
                 for issue in context.get('issues', []))
    risks.extend({'kind': 'limitation', 'title': 'Limitación', 'reason': issue}
                 for issue in context.get('limitations', []))
    progress = []
    for session in sessions:
        for field in ('done', 'tests', 'files'):
            if session.get(field):
                progress.append({'date': session.get('date'), 'module': session.get('module'),
                                 'kind': field, 'value': session[field],
                                 'tests_status': session.get('tests_status')})
    decision_fields = ('id', 'title', 'status', 'declared_status', 'effective_status',
                       'evidence_validity', 'review_required', 'superseded_by', 'reason')
    pending_fields = ('id', 'title', 'status', 'declared_status', 'evidence_validity',
                      'review_required', 'scope', 'acceptance', 'dependencies')
    decisions, decisions_omitted = _unique(decisions, max_items, decision_fields)
    pending, pending_omitted = _unique(pending, max_items, pending_fields)
    progress, progress_omitted = _unique(progress, max_items, ('date', 'module', 'kind', 'value', 'tests_status'))
    risks, risks_omitted = _unique(risks, max_items, ('kind', 'id', 'title', 'reason'))
    last = _latest(sessions)
    last = _compact(last, ('id', 'date', 'created_at', 'module', 'done', 'pending', 'tests',
                           'tests_status', 'files', 'decisions', 'conflicts')) if last else None
    state = context.get('state') or {}
    state_coverage = state.get('coverage') or {}
    coverage_issues = context.get('issues', [])
    if not isinstance(coverage_issues, list):
        coverage_issues = []
    coverage_issues = coverage_issues[:max_items]
    return {'decisions': decisions, 'progress': progress, 'pending': pending, 'risks': risks,
            'last_session': last,
            'coverage': {'partial': bool(context.get('partial')), 'issues': coverage_issues,
                         'omitted': context.get('omitted', []),
                         'partial_reasons': list(context.get('partial_reasons', []))[:max_items],
                         'state': {'path': state.get('path'), 'status': state.get('status'),
                                   'coverage_class': state_coverage.get('coverage_class'),
                                   'integrity': state_coverage.get('integrity'),
                                   'budget_scope': state_coverage.get('budget_scope')}},
            'limits': {'max_items': max_items, 'omitted': {
                'decisions': decisions_omitted, 'progress': progress_omitted,
                'pending': pending_omitted, 'risks': risks_omitted}}}


def overview(summary):
    """Render only non-duplicating aggregate information for portable text."""
    last = summary.get('last_session') or {}
    last_text = 'No registrada.'
    if last:
        last_text = '{} / {}: {}'.format(last.get('date', 'fecha desconocida'),
                                         last.get('module', 'modulo no indicado'),
                                         last.get('done', 'avance no registrado'))
    coverage = summary.get('coverage', {})
    omitted = summary.get('limits', {}).get('omitted', {})
    omitted_total = sum(value for value in omitted.values() if isinstance(value, int))
    return ('Decisiones efectivas: {}\nAvances: {}\nPendientes abiertos: {}\nRiesgos: {}\n'
            'Ultima sesion: {}\nCobertura: {}\nElementos omitidos por limite: {}'.format(
                len(summary.get('decisions', [])), len(summary.get('progress', [])),
                len(summary.get('pending', [])), len(summary.get('risks', [])), last_text,
                'parcial' if coverage.get('partial') else 'completa dentro de lo leido',
                omitted_total))
