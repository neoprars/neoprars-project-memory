"""Bounded, offline, read-only discovery. Never emit document contents or Git messages."""
import os
import codecs
import math
import heapq
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from safety import is_link, safe_path

EXCLUDED = frozenset(''' .git .hg .svn node_modules vendor packages bin obj build dist
target out outputs artifacts coverage .coverage .next .nuxt .cache .gradle .idea
.vs .venv venv env __pycache__ .pytest_cache .mypy_cache .tox site-packages
graphify-out .aws .azure .ssh .gnupg .terraform logs backups exports datasets
.agents .codex
'''.split())
SENSITIVE = re.compile(r'(?i)(secret|credential|password|passwd|token|private|confidential|'
                       r'sensible|sensitive|secreto|credencial|contrase|customer|cliente|'
                       r'patient|paciente|personal|\.env(?:\.|$)|id_rsa|id_ed25519|'
                       r'auth\.json|\.npmrc|\.pypirc|\.netrc|config\.json|'
                       r'api[-_.]?key|access[-_.]?key|appsettings|service[-_.]?account|'
                       r'connection[-_.]?string|ghp_|github_pat_|sk-[A-Za-z0-9]|eyJ[A-Za-z0-9])')
DOC_EXT = {'.md', '.rst', '.adoc', '.txt'}
LANGUAGES = {'.py': 'Python', '.cs': 'C#', '.xaml': 'XAML', '.js': 'JavaScript',
             '.jsx': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TypeScript',
             '.go': 'Go', '.rs': 'Rust', '.java': 'Java', '.kt': 'Kotlin',
             '.swift': 'Swift', '.rb': 'Ruby', '.php': 'PHP', '.c': 'C',
             '.cpp': 'C++', '.dart': 'Dart', '.ex': 'Elixir', '.scala': 'Scala'}
TOOLS = ('git', 'python', 'py', 'node', 'npm', 'dotnet', 'cargo', 'go', 'java',
         'ruby', 'php', 'cmake', 'make', 'docker', 'graphify', 'codex', 'ecc')
CATEGORIES = {
    'instrucciones': r'\bagents\b|\bcontributing\b|instrucciones|instructions|codex',
    'arquitectura': r'arquitectura|architecture|arquitetura|dependency|dependencias|layers|capas',
    'roadmap': r'roadmap|hoja de ruta|roteiro|milestones|hitos|plan de (?:trabajo|desarrollo)',
    'estado': r'project.state|estado|status|progreso|progress|situa[cç][aã]o',
    'decisiones': r'decisi[oó]n|decision|decis[aã]o|\badr\b',
    'pruebas': r'pruebas|tests?|testing|validaci[oó]n|verification|verifica[cç][aã]o',
    'memoria': r'memoria|memory|sesiones|sessions|handoff|transferencia',
}
OFFICIAL = re.compile(r'(?i)oficial|official|authoritative|source of truth|fuente de verdad|can[oó]nic')
LINK = re.compile(r'\[[^\]\n]*\]\(([^)\n]+)\)')


def safe_label(value):
    """Paths can themselves contain credentials or personal data: strict output guard."""
    value = str(value)
    if (SENSITIVE.search(value) or re.search(r'[\x00-\x1f\x7f@:?<>|]', value)
            or re.search(r'\d{24,}', value)
            or any(len(part) >= 40 and re.search('[a-z]', part)
                   and re.search('[A-Z]', part) and re.search('[0-9]', part)
                   for part in re.findall(r'[A-Za-z0-9_=]+', value))):
        return '[ruta omitida por seguridad]'
    return value


def git_read(root, *args, timeout=5):
    executable = shutil.which('git')
    if not executable:
        return None
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith('GIT_'):
            environment.pop(key)
    environment.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat')
    try:
        result = subprocess.run([executable, '--no-optional-locks', '--no-replace-objects', *args], cwd=root,
                                env=environment, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def discover_root(project=None, *, deadline=None):
    requested = Path(project or Path.cwd()).absolute()
    # Reject linked components before resolve hides them.
    for component in (requested, *requested.parents):
        if is_link(component):
            raise ValueError('linked root')
    start = requested.resolve()
    if not start.is_dir():
        raise ValueError('invalid root')
    if project:
        return start, 'confirmado: raíz explícita'
    # Git metadata discovery does not execute repository scripts or inspect credentials.
    remaining = 5 if deadline is None else min(5, deadline - time.monotonic())
    found = git_read(start, 'rev-parse', '--show-toplevel', timeout=remaining) if remaining > 0 else None
    if found and Path(found).is_dir():
        return Path(found).resolve(), 'confirmado: Git'
    for ancestor in (start, *start.parents):
        if any((ancestor / marker).exists() for marker in ('.git', '.hg', '.svn')):
            return ancestor, 'inferido: marcador de control de versiones'
    return start, 'desconocido: sin marcador; directorio actual como alcance, usar --project'


def read_documents(root, files, *, max_bytes, max_document_bytes, max_documents,
                   chunk_size, deadline, reasons):
    """Probe bounded prefixes, then resume offsets in priority order; never skip bytes."""
    candidates = [p for p in files if p.suffix.lower() in DOC_EXT]
    states = []
    spent = opened = 0
    for path in candidates:
        source = {'path': safe_label(path.relative_to(root).as_posix()),
                  'categories': [], 'lines': {}, 'authority': 'candidata; autoridad desconocida'}
        state = {'path': path, 'source': source, 'decoder': codecs.getincrementaldecoder('utf-8-sig')(),
                 'chunks': [], 'read': 0, 'size': None, 'stat': None, 'reason': None,
                 'priority': False, 'opened': False, 'complete': False}
        try:
            if (is_link(path)
                    or not path.resolve().is_relative_to(root) or SENSITIVE.search(str(path.relative_to(root)))):
                state['reason'] = 'ruta excluida antes de lectura'
            else:
                info = path.stat()
                state['size'] = info.st_size
                state['stat'] = (info.st_size, info.st_mtime_ns, info.st_ino)
        except OSError:
            state['reason'] = 'documento inaccesible'
        state['order'] = len(states)
        states.append(state)

    def advance(state, allowance):
        nonlocal spent, opened
        if state['reason'] or state['complete']:
            return
        if time.monotonic() >= deadline:
            state['reason'] = 'presupuesto de tiempo agotado'
            return
        if not state['opened'] and opened >= max_documents:
            state['reason'] = 'presupuesto de documentos agotado'
            return
        cap = min(allowance, max_bytes - spent,
                  max_document_bytes - state['read'], state['size'] - state['read'])
        if cap <= 0 and state['read'] < state['size']:
            state['reason'] = ('presupuesto global de bytes agotado' if spent >= max_bytes
                               else 'presupuesto de bytes por documento agotado')
            return
        path = state['path']
        try:
            if safe_path(root, path.relative_to(root).as_posix(), EXCLUDED, SENSITIVE, max_depth=10000) is None:
                state['reason'] = 'enlace excluido antes de lectura'
                return
            info = path.stat()
            if (info.st_size, info.st_mtime_ns, info.st_ino) != state['stat']:
                state['reason'] = 'documento cambiado; no verificable'
                state['chunks'].clear()
                return
            if not state['opened']:
                opened += 1
                state['opened'] = True
            with path.open('rb', buffering=0) as stream:
                stream.seek(state['read'])
                while cap > 0:
                    if time.monotonic() >= deadline:
                        state['reason'] = 'presupuesto de tiempo agotado'
                        break
                    data = stream.read(min(chunk_size, cap))
                    if not data:
                        state['reason'] = 'documento cambiado; no verificable'
                        state['chunks'].clear()
                        break
                    spent += len(data)
                    state['read'] += len(data)
                    cap -= len(data)
                    if b'\x00' in data:
                        state['reason'] = 'archivo binario excluido'
                        state['chunks'].clear()
                        break
                    state['chunks'].append(state['decoder'].decode(data, final=False))
            info = path.stat()
            if (info.st_size, info.st_mtime_ns, info.st_ino) != state['stat']:
                state['reason'] = 'documento cambiado; no verificable'
                state['chunks'].clear()
            if not state['reason'] and state['read'] == state['size']:
                state['chunks'].append(state['decoder'].decode(b'', final=True))
                state['complete'] = True
        except UnicodeError:
            state['reason'] = 'codificación desconocida; análisis omitido'
            state['chunks'].clear()
        except OSError:
            state['reason'] = 'documento inaccesible'
            state['chunks'].clear()

    # All probe bytes count once. Remaining 75% is reserved for continuation.
    probe_budget = max_bytes // 4
    by_path = {s['path'].resolve(): s for s in states}
    queue = []
    def discover_priority(state):
        text = ''.join(state['chunks'])
        if not state['complete']:
            text = text[:text.rfind('\n') + 1]
        for line in text.splitlines():
            if not OFFICIAL.search(line):
                continue
            for target in LINK.findall(line):
                target = target.split('#', 1)[0].strip('<>')
                if not target or re.match(r'\w+:|//', target) or SENSITIVE.search(target):
                    continue
                resolved = (state['path'].parent / target).resolve()
                if resolved in by_path:
                    destination = by_path[resolved]
                    if not destination['priority']:
                        destination['priority'] = True
                        heapq.heappush(queue, (0, destination['order']))
                    destination['source']['authority'] = 'prioritaria: referencia explícita de autoridad'
    for state in states:
        heapq.heappush(queue, (not state['priority'], state['order']))
    probed = set()
    while queue and spent < probe_budget and time.monotonic() < deadline and opened < max_documents:
        _, index = heapq.heappop(queue)
        if index in probed:
            continue
        probed.add(index)
        state = states[index]
        advance(state, min(4096, probe_budget - spent))
        discover_priority(state)
    queue.clear()
    for state in states:
        heapq.heappush(queue, (not state['priority'], state['order']))
    finished = set()
    while queue:
        # Reevaluate after every document to prioritize newly discovered authority links.
        _, index = heapq.heappop(queue)
        if index in finished:
            continue
        finished.add(index)
        state = states[index]
        advance(state, max_document_bytes - state['read'])
        if not state['complete'] and not state['reason']:
            state['reason'] = ('presupuesto global de bytes agotado' if spent >= max_bytes
                               else 'presupuesto de bytes por documento agotado')
        discover_priority(state)
    docs = []
    coverage = []
    for state in states:
        source = state['source']
        content = ''.join(state['chunks'])
        status = 'completa' if state['complete'] else ('parcial' if state['read'] else 'omitida')
        if state['reason'] and ('binario' in state['reason'] or 'codificación' in state['reason']
                                or 'cambiado' in state['reason'] or 'inaccesible' in state['reason']):
            content = ''
            status = 'omitida'
        if not state['complete']:
            # Only complete lines are analysed; trailing bytes remain reported as read.
            content = content[:content.rfind('\n') + 1]
        line_count = len(content.splitlines())
        analysed_bytes = len(content.encode('utf-8'))
        item = {'path': source['path'], 'status': status, 'size_bytes': state['size'],
                'bytes_read': state['read'], 'bytes_analysed': analysed_bytes,
                'byte_ranges_read': [[0, state['read']]] if state['read'] else [],
                'line_ranges_analysed': [[1, line_count]] if line_count else [],
                'coverage_ratio': state['read'] / state['size'] if state['size'] else (1 if state['complete'] else 0),
                'reason': state['reason'] or ('fin del documento' if state['complete'] else 'presupuesto agotado'),
                'decisions': 'no verificadas; candidatos documentales solamente'}
        coverage.append(item)
        source['coverage'] = item
        if status != 'completa':
            reasons[item['reason']] += 1
        if status != 'omitida' and content:
            for category, pattern in CATEGORIES.items():
                matches = [i for i, line in enumerate(content.splitlines(), 1) if re.search(pattern, line, re.I)]
                if matches or re.search(pattern, source['path'], re.I):
                    source['categories'].append(category)
                    source['lines'][category] = matches[:5] or [1]
            if OFFICIAL.search(content) and not source['authority'].startswith('prioritaria'):
                source['authority'] = 'declaración de autoridad detectada; requiere verificar alcance'
            docs.append((state['path'], content, source))
    return docs, coverage, spent, opened


def audit_project(project=None, *, max_files=5000, max_bytes=2000000, max_depth=12,
                  exclude=(), max_document_bytes=1000000, max_documents=1000,
                  max_seconds=20, chunk_size=16384):
    if (max_files < 1 or max_bytes < 1 or max_depth < 0 or max_document_bytes < 1
            or max_documents < 1 or chunk_size < 1 or not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError('invalid limits')
    deadline = time.monotonic() + max_seconds
    root, root_evidence = discover_root(project, deadline=deadline)
    report = {'version': '1.1.1', 'root': safe_label(root.name), 'root_evidence': root_evidence,
              'read_only': True, 'partial': False, 'limitations': [], 'sources': [],
              'findings': [], 'structure': [], 'technologies': [], 'tools': {}, 'git': {}}
    reasons = Counter()
    files = []
    stack = [(root, 0)]
    directories = set()
    exclusions = EXCLUDED | {x.casefold() for x in exclude}
    inventory_deadline = min(deadline, time.monotonic() + 10)
    entries_seen = 0
    while stack:
        directory, depth = stack.pop()
        if time.monotonic() > inventory_deadline or entries_seen >= max_files:
            reasons['límite de entradas o tiempo (10 s)'] += 1
            break
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > max_files or time.monotonic() > inventory_deadline:
                        reasons['límite de entradas o tiempo (10 s)'] += 1
                        stack.clear()
                        break
                    path = Path(entry.path)
                    if entry.is_symlink() or is_link(path):
                        reasons['enlaces/junctions excluidos'] += 1
                        continue
                    if SENSITIVE.search(entry.name):
                        reasons['rutas potencialmente sensibles excluidas'] += 1
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.casefold() in exclusions:
                            reasons['directorios generados/dependencias/voluminosos excluidos'] += 1
                        elif depth >= max_depth:
                            reasons['profundidad máxima'] += 1
                        else:
                            directories.add(path.relative_to(root).as_posix())
                            stack.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path)
        except OSError:
            reasons['entradas inaccesibles'] += 1
    files.sort(key=lambda p: (len(p.relative_to(root).parts), p.as_posix().casefold()))
    report['structure'] = [safe_label(p) for p in sorted(directories)[:60]]
    if len(directories) > 60:
        reasons['estructura mostrada limitada a 60 directorios'] += 1
    counts = Counter(LANGUAGES[p.suffix.lower()] for p in files if p.suffix.lower() in LANGUAGES)
    report['technologies'] = [{'name': name, 'files': count,
                               'evidence': 'inferido por extensiones; framework/versión desconocidos'}
                              for name, count in sorted(counts.items())]
    suffixes = Counter(p.suffix.lower() or '[sin extensión]' for p in files)
    report['extensions'] = {safe_label(k): v for k, v in suffixes.most_common(25)}
    report['manifests'] = [safe_label(p.relative_to(root).as_posix()) for p in files
                           if p.suffix.lower() in {'.csproj', '.fsproj', '.vbproj', '.sln', '.slnx'}
                           or p.name.casefold() in {'package.json', 'pyproject.toml', 'cargo.toml',
                                                   'go.mod', 'pom.xml', 'build.gradle', 'gemfile',
                                                   'composer.json', 'pubspec.yaml', 'cmakelists.txt'}][:30]
    # This registry improves recognition, but unknown stacks still receive an inventory.
    report['tools'] = {name: bool(shutil.which(name)) for name in TOOLS}
    docs, document_coverage, spent, opened = read_documents(
        root, files, max_bytes=max_bytes, max_document_bytes=max_document_bytes,
        max_documents=max_documents, chunk_size=chunk_size, deadline=deadline, reasons=reasons)
    report['documents'] = document_coverage
    # Authority is not assigned just because a filename looks official. Local references
    # on explicitly authoritative lines get priority, but conflicting declarations remain.
    by_path = {path.resolve(): source for path, _, source in docs}
    global_tasks = {}
    for path, content, source in docs:
        for line_no, line in enumerate(content.splitlines(), 1):
            for target in LINK.findall(line):
                target = target.split('#', 1)[0].strip('<>')
                if not target or re.match(r'\w+:|//', target):
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.is_relative_to(root) or SENSITIVE.search(target):
                    continue
                if OFFICIAL.search(line) and resolved in by_path:
                    by_path[resolved]['authority'] = 'prioritaria: referencia explícita de autoridad'
                if not resolved.exists():
                    report['findings'].append({'kind': 'confirmado: referencia local no resuelta',
                                               'source': source['path'], 'line': line_no})
        # Narrow, verifiable checkbox conflicts; never infer a global decision from history.
        tasks = {}
        for line_no, line in enumerate(content.splitlines(), 1):
            match = re.match(r'\s*[-*]\s+\[([ xX])\]\s+(.+)', line)
            if match:
                key = match[2].strip().casefold()
                if key in tasks and tasks[key][0] != match[1].casefold():
                    report['findings'].append({'kind': 'posible inconsistencia: tarea con marcas opuestas',
                                               'source': source['path'],
                                               'lines': [tasks[key][1], line_no]})
                tasks[key] = (match[1].casefold(), line_no)
                if key in global_tasks and global_tasks[key][0] != match[1].casefold():
                    previous = global_tasks[key]
                    if previous[1] != source['path']:
                        report['findings'].append({'kind': 'posible diferencia entre documentos/histórico',
                                                   'sources': [previous[1], source['path']],
                                                   'lines': [previous[2], line_no]})
                global_tasks[key] = (match[1].casefold(), source['path'], line_no)
    sources = [source for _, _, source in docs]
    sources.sort(key=lambda s: (not s['authority'].startswith('prioritaria'), s['path']))
    report['sources'] = sources[:100]
    if len(sources) > 100:
        reasons['fuentes mostradas limitadas a 100'] += 1
    report['categories'] = {}
    for category in CATEGORIES:
        candidates = [s for s in sources if category in s['categories']]
        priority = [s for s in candidates if s['authority'].startswith('prioritaria')]
        report['categories'][category] = {'status': 'candidatas detectadas' if candidates else 'desconocido',
                                          'sources': [s['path'] for s in (priority or candidates)[:10]]}
        if len(priority) > 1:
            report['findings'].append({'kind': 'autoridad ambigua: varias referencias prioritarias',
                                       'category': category})
    test_paths = [safe_label(p.relative_to(root).as_posix()) for p in files
                  if re.search(r'(?i)(?:test|spec|prueba)', p.relative_to(root).as_posix())]
    report['tests'] = {'status': 'candidatas detectadas' if test_paths else 'desconocido',
                       'sources': test_paths[:20], 'results': 'desconocido: no se ejecutaron pruebas'}
    remaining = deadline - time.monotonic()
    history = git_read(root, 'log', '-20', '--format=%H %cs', timeout=min(5, remaining)) if remaining > 0 else None
    report['git'] = {'status': 'confirmado' if history else 'desconocido: no disponible o sin commits',
                      'history': [line for line in (history or '').splitlines()
                                  if re.fullmatch(r'[0-9a-f]{40,64} \d{4}-\d{2}-\d{2}', line)],
                      'scope': 'últimos 20 commits; sin mensajes, autores ni remotos'}
    if len(report['findings']) > 100:
        reasons['hallazgos mostrados limitados a 100'] += 1
    if time.monotonic() >= deadline:
        reasons['presupuesto de tiempo total alcanzado'] += 1
    report['partial'] = bool(reasons)
    report['limitations'] = [f'{reason}: {count}' for reason, count in sorted(reasons.items())]
    report['limitations'] += ['Análisis heurístico: no prueba arquitectura, implementación ni decisiones aprobadas.',
                              'Lectura secuencial por fragmentos; cobertura parcial no verifica decisiones ni ausencia de contradicciones.',
                              'Tiempo cooperativo entre operaciones; una lectura/llamada del sistema en curso puede exceder el plazo.',
                              'No copia contenido; datos sensibles por nombre se excluyen antes de lectura.',
                              'Contenido sensible no identificable por nombre puede existir en documentación pública.',
                              'No ejecuta herramientas del proyecto, pruebas, hooks, red ni inicializa memoria.']
    report['coverage'] = {'entries_seen': entries_seen, 'files_seen': len(files),
                           'documents_read': opened, 'bytes_read': spent,
                           'document_statuses': dict(Counter(x['status'] for x in document_coverage)),
                           'limits': {'entries': max_files, 'bytes': max_bytes, 'depth': max_depth,
                                      'document_bytes': max_document_bytes, 'documents': max_documents,
                                      'seconds': max_seconds, 'chunk_bytes': chunk_size}}
    report['findings'] = report['findings'][:100]
    return report


def render(report):
    import json
    return 'NEOPRARS PROJECT MEMORY V1.1.1 — AUDIT (solo lectura)\n' + json.dumps(
        report, ensure_ascii=True, indent=2)
