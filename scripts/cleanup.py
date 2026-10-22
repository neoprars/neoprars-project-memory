"""Conservative, provider-neutral cleanup preview and execution."""
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from audit import discover_root, safe_label

MARKER = '.neoprars-cleanup.json'
SCHEMA_VERSION = 1
PROTECTED_PARTS = {
    '.git', '.agents', '.claude', '.codex', 'docs/memory', 'docs/audits', 'memory',
    'backup', 'backups', 'evidence', 'sessions', 'sdk', 'node_modules',
    '.npm', '.nuget', '.cache', 'NeoprarsPosVenta',
}
BUILD_DIRS = {'.pytest_cache', '__pycache__', '.mypy_cache', '.ruff_cache',
              '.tox', '.nox', 'htmlcov', 'test-results', 'TestResults', 'build', 'dist'}
BUILD_FILES = {'.coverage', 'coverage.xml', 'coverage.json'}
ARTIFACT_SUFFIXES = {'.pyc', '.pyo', '.dll', '.pdb', '.exe', '.deps.json',
                     '.runtimeconfig.json', '.cache', '.tmp', '.trx', '.coverage'}
SOURCE_SUFFIXES = {'.py', '.pyw', '.cs', '.fs', '.fsx', '.vb', '.js', '.ts',
                   '.tsx', '.jsx', '.java', '.kt', '.kts', '.go', '.rs', '.cpp',
                   '.c', '.h', '.hpp', '.sql', '.md', '.txt', '.json', '.yaml',
                   '.yml', '.toml', '.xml', '.sln', '.csproj', '.fsproj'}
PROJECT_MARKERS = {'.sln', '.slnx', '.csproj', '.fsproj', '.vbproj',
                   'package.json', 'pom.xml', 'build.gradle', 'build.gradle.kts',
                   'cargo.toml', 'go.mod', 'makefile', 'cmakelists.txt',
                   'pyproject.toml', 'setup.py', 'setup.cfg'}
BUILD_CACHE_DIRS = {'bin', 'obj'}
BUILD_GENERATED_SUFFIXES = {'.a', '.class', '.dylib', '.flat', '.jar', '.lz4',
                           '.mui', '.props', '.resx', '.resources', '.so',
                           '.stamp', '.targets', '.up2date', '.winmd'}
DISTRIBUTION_SUFFIXES = {'.aab', '.appimage', '.dmg', '.exe', '.ipa', '.msix',
                         '.pkg', '.apk'}


def _canonical(path):
    return Path(path).resolve()


def _inside(path, root):
    try:
        _canonical(path).relative_to(_canonical(root))
        return True
    except ValueError:
        return False


def _protected(path, root):
    try:
        relative = _canonical(path).relative_to(_canonical(root)).as_posix()
    except ValueError:
        return False
    parts = relative.split('/')
    for index, part in enumerate(parts):
        if part.casefold() in PROTECTED_PARTS:
            return True
        if '/'.join(parts[:index + 1]).casefold() in PROTECTED_PARTS:
            return True
    return False


def _excluded(path, root, excludes):
    relative = _display(path, root).casefold().strip('/')
    absolute = _canonical(path).as_posix().casefold().rstrip('/')
    for raw in excludes or ():
        value = str(raw).replace('\\', '/').strip().casefold().strip('/')
        if not value:
            continue
        if value.startswith('/') or ':' in value[:3]:
            if absolute == value.rstrip('/') or absolute.startswith(value.rstrip('/') + '/'):
                return True
        elif relative == value or relative.startswith(value + '/'):
            return True
    return False


def _walk_files(folder):
    files = []
    try:
        for current, dirs, names in os.walk(folder, topdown=True, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                files.append(path)
    except OSError:
        return []
    return files


def _artifact_dir(path):
    files = _walk_files(path)
    if not files:
        return False
    if any(file.name.casefold() in {'.lock', 'lock', '.pid', 'pid'}
           or file.suffix.casefold() in {'.lock', '.pid'} for file in files):
        return False
    if path.name in ('__pycache__',):
        return all(file.suffix.lower() in {'.pyc', '.pyo'} or file.name == '.gitignore' for file in files)
    if path.name in BUILD_DIRS:
        return all(file.suffix.lower() in ARTIFACT_SUFFIXES or file.name in {MARKER, '.gitignore'}
                   for file in files)
    if path.name in ('bin', 'obj'):
        return all(file.suffix.lower() in ARTIFACT_SUFFIXES or file.name.endswith(('.deps.json', '.runtimeconfig.json'))
                   for file in files)
    return False


def _project_evidence(path, root):
    current = path.parent
    root = _canonical(root)
    while True:
        try:
            current.relative_to(root)
        except ValueError:
            return False
        try:
            if any(child.is_file() and (child.suffix.casefold() in PROJECT_MARKERS
                                        or child.name.casefold() in PROJECT_MARKERS)
                   for child in current.iterdir()):
                return True
        except OSError:
            return False
        if current == root:
            return False
        current = current.parent


def _build_cache_dir(path, root):
    if not _project_evidence(path, root):
        return False, 'evidencia de proyecto insuficiente'
    files = _walk_files(path)
    if not files:
        return False, 'directorio vacio o no verificable'
    for file in files:
        valid, reason = _build_cache_file(file, path)
        if not valid:
            return False, reason
    return True, 'perfil build-cache: evidencia contextual y artefactos regenerables verificados'


def _build_cache_file(file, build_dir):
    name = file.name.casefold()
    suffix = file.suffix.casefold()
    relative_parts = {part.casefold() for part in file.relative_to(build_dir).parts[:-1]}
    if name in {'.lock', 'lock', '.pid', 'pid'} or suffix in {'.lock', '.pid'}:
        return False, 'archivo activo o lock'
    generated_name = (name == 'android_debug_keystore.flag'
                      or name == 'audit.txt' and 'native-tests' in relative_parts
                      or name.startswith('.mscoveragesourcerootsmapping_')
                      or name.endswith('generatedmsbuildeditorconfig.editorconfig')
                      or name == 'apphost.exe')
    if generated_name:
        return True, ''
    if suffix in DISTRIBUTION_SUFFIXES:
        return False, 'entregable de distribucion protegido'
    if suffix in ARTIFACT_SUFFIXES or suffix in BUILD_GENERATED_SUFFIXES:
        return True, ''
    if suffix == '.cs':
        generated = (name.endswith('.g.cs') or name.endswith('.g.i.cs')
                     or name.endswith('.assemblyinfo.cs')
                     or name.endswith('.assemblyattributes.cs')
                     or name.endswith('.globalusings.g.cs')
                     or name.endswith('.generated.cs')
                     or name.startswith('__microsoft.android.resource.designer'))
        return (True, '') if generated else (False, 'posible fuente original')
    if suffix == '.java':
        generated = name in {'r.java', 'buildconfig.java'} or 'android' in relative_parts
        return (True, '') if generated else (False, 'posible fuente original')
    if suffix == '.json':
        generated = (name in {'project.assets.json', 'native-apk-audit.json'}
                     or name.endswith('.deps.json') or name.endswith('.runtimeconfig.json')
                     or name.endswith('.nuget.dgspec.json') or name.endswith('.sourcelink.json'))
        return (True, '') if generated else (False, 'archivo JSON no reconocido')
    if suffix == '.xml':
        generated = (name == 'androidmanifest.xml' or name.endswith('.jlo.xml')
                     or name.endswith('.typemap.xml')
                     or bool(relative_parts & {'android', 'assets', 'res', 'resources'}))
        return (True, '') if generated else (False, 'archivo XML no reconocido')
    if suffix == '.png':
        return ((True, '') if relative_parts & {'android', 'assets', 'res', 'resources', 'drawable', 'mipmap'}
                else (False, 'imagen fuera de una ruta generada reconocida'))
    if suffix == '.txt':
        generated = (name in {'acw-map.txt', 'case_map.txt', 'customview-map.txt',
                              'native-dedup.txt', '__environment__.txt', 'r.txt'}
                     or name.startswith('_compile') or name.endswith('.filelistabsolute.txt'))
        return (True, '') if generated else (False, 'archivo de texto no reconocido')
    return False, 'extension o archivo desconocido'


def _select_build_cache(path, root, excludes, build_root=None):
    """Return safe descendants without promoting a mixed build directory."""
    build_root = path if build_root is None else build_root
    try:
        entries = list(path.iterdir())
    except OSError:
        return [], False, [(path, 'contenido no verificable')]
    selected = []
    blockers = []
    all_safe = True
    has_content = False
    for entry in entries:
        if entry.is_symlink():
            all_safe = False
            blockers.append((entry, 'enlace simbolico protegido'))
            continue
        if entry.is_dir():
            has_content = True
            if _protected(entry, root):
                all_safe = False
                blockers.append((entry, 'ruta protegida'))
                continue
            if _excluded(entry, root, excludes):
                all_safe = False
                blockers.append((entry, 'exclusion configurable'))
                continue
            children, child_safe, child_blockers = _select_build_cache(
                entry, root, excludes, build_root)
            selected.extend(children)
            blockers.extend(child_blockers)
            if not child_safe:
                all_safe = False
        elif entry.is_file():
            has_content = True
            valid, reason = _build_cache_file(entry, build_root)
            if valid:
                selected.append(entry)
            else:
                all_safe = False
                blockers.append((entry, reason))
    if all_safe and has_content:
        # Keep build-cache candidates at file granularity. This makes the
        # plan snapshot precise and prevents a later child mutation from
        # being hidden behind a directory candidate.
        return selected, True, []
    return selected, False, blockers


def _blocker_summary(blockers, root):
    if not blockers:
        return 'contenido no verificablemente descartable'
    reasons = Counter(reason for _, reason in blockers)
    summary = ', '.join(f'{reason} ({count})' for reason, count in reasons.most_common(3))
    examples = ', '.join(_display(path, root) for path, _ in blockers[:3])
    return f'contenido mixto; bloqueos: {summary}; ejemplos: {examples}'


def _size(path):
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    return sum(_size(file) for file in _walk_files(path))


def _snapshot(path):
    """Return size and a metadata snapshot used to invalidate stale plans."""
    digest = hashlib.sha256()
    if path.is_file():
        stat = path.stat()
        digest.update(b'file\0')
        digest.update(path.name.encode('utf-8', 'surrogateescape'))
        digest.update(f'\0{stat.st_size}\0{stat.st_mtime_ns}\0{stat.st_ino}'.encode())
        return stat.st_size, digest.hexdigest()

    total = 0
    entries = []
    try:
        for current, dirs, names in os.walk(path, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(name for name in dirs
                             if not (current_path / name).is_symlink())
            for name in dirs:
                child = current_path / name
                stat = child.stat()
                entries.append(('dir', child.relative_to(path).as_posix(),
                                0, stat.st_mtime_ns, stat.st_ino))
            for name in sorted(names):
                child = current_path / name
                if child.is_symlink() or not child.is_file():
                    continue
                stat = child.stat()
                total += stat.st_size
                entries.append(('file', child.relative_to(path).as_posix(),
                                stat.st_size, stat.st_mtime_ns, stat.st_ino))
    except OSError:
        return 0, 'unverificable'
    for kind, relative, size, mtime_ns, inode in sorted(entries):
        digest.update(f'{kind}\0{relative}\0{size}\0{mtime_ns}\0{inode}\n'.encode(
            'utf-8', 'surrogateescape'))
    return total, digest.hexdigest()


def _display(path, root):
    if _inside(path, root):
        return _canonical(path).relative_to(_canonical(root)).as_posix()
    return safe_label(_canonical(path).as_posix())


def _candidate(path, root, scope, reason):
    size, snapshot_sha256 = _snapshot(path)
    return {'path': _display(path, root), 'absolute_path': str(_canonical(path)),
            'scope': scope, 'reason': reason, 'size_bytes': size,
            'snapshot_sha256': snapshot_sha256, 'verified': True}


def _marker_candidate(folder, root):
    marker = folder / MARKER
    if not marker.is_file() or marker.is_symlink():
        return None
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    owner = data.get('project_root')
    if (data.get('schema_version') != SCHEMA_VERSION or data.get('discardable') is not True
            or data.get('active') is True
            or not isinstance(owner, str) or _canonical(owner) != _canonical(root)):
        return None
    files = _walk_files(folder)
    if not files or any(file.name != MARKER and file.suffix.lower() not in ARTIFACT_SUFFIXES for file in files):
        return None
    return _candidate(folder, root, 'external-temp', 'marcador de propiedad y descartabilidad verificado')


def scan(project=None, *, include_external=True, max_items=500,
         profile='default', exclude=()):
    if max_items < 1:
        raise ValueError('max_items invalido')
    if profile not in ('default', 'build-cache'):
        raise ValueError('perfil de limpieza invalido')
    root, evidence = discover_root(project)
    root = _canonical(root)
    if not root.is_dir():
        raise ValueError('La raiz del proyecto no es un directorio.')
    candidates = []
    omitted = []
    seen = set()
    try:
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
            if _protected(current_path, root) or _excluded(current_path, root, exclude):
                dirs[:] = []
                continue
            for name in list(dirs):
                path = current_path / name
                is_profile_dir = (path.name in BUILD_CACHE_DIRS if profile == 'build-cache'
                                  else path.name in BUILD_DIRS or path.name in BUILD_CACHE_DIRS)
                if is_profile_dir:
                    if _protected(path, root):
                        omitted.append({'path': _display(path, root), 'reason': 'ruta protegida'})
                    elif _excluded(path, root, exclude):
                        omitted.append({'path': _display(path, root), 'reason': 'exclusion configurable'})
                    elif profile == 'build-cache':
                        if not _project_evidence(path, root):
                            omitted.append({'path': _display(path, root),
                                            'reason': 'evidencia de proyecto insuficiente'})
                            dirs.remove(name)
                            continue
                        selected, all_safe, blockers = _select_build_cache(path, root, exclude)
                        for selected_path in selected:
                            candidates.append(_candidate(
                                selected_path, root, 'project',
                                'perfil build-cache: subruta regenerable verificada'))
                        if not all_safe:
                            omitted.append({'path': _display(path, root),
                                            'reason': _blocker_summary(blockers, root)})
                        dirs.remove(name)
                    elif _artifact_dir(path):
                        reason = 'directorio de artefactos verificable'
                        candidates.append(_candidate(path, root, 'project', reason))
                        dirs.remove(name)
                    else:
                        omitted.append({'path': _display(path, root), 'reason': 'contenido no verificablemente descartable'})
                        dirs.remove(name)
            for name in files:
                path = current_path / name
                if path.is_symlink() or _protected(path, root):
                    continue
                if path.name in BUILD_FILES and path.is_file():
                    candidates.append(_candidate(path, root, 'project', 'artefacto de pruebas reconocido'))
    except OSError as exc:
        omitted.append({'path': _display(root, root), 'reason': 'inventario incompleto: ' + str(exc)})
    if include_external:
        temp_root = _canonical(tempfile.gettempdir())
        try:
            for entry in temp_root.iterdir():
                if entry.is_symlink() or not entry.is_dir():
                    continue
                candidate = _marker_candidate(entry, root)
                if candidate:
                    candidates.append(candidate)
        except OSError as exc:
            omitted.append({'path': safe_label(str(temp_root)), 'reason': 'temporales externos no verificables: ' + str(exc)})
    unique = []
    for item in candidates:
        key = item['absolute_path'].casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique = unique[:max_items]
    plan = {'root': str(root), 'root_evidence': evidence,
            'include_external': bool(include_external), 'profile': profile,
            'exclude': sorted(str(value).replace('\\', '/') for value in (exclude or ())),
            'candidates': unique}
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {'command': 'cleanup-day', 'mode': 'preview', 'read_only': True,
            'root': safe_label(str(root)), 'candidates': unique, 'omitted': omitted,
            'errors': [], 'space_recoverable_bytes': sum(x['size_bytes'] for x in unique),
            'plan_sha256': plan_hash, 'next': 'Revisar la vista previa y ejecutar sólo con autorización explícita.'}


def execute(project=None, *, include_external=True, max_items=500, profile='default',
            exclude=(), plan_sha256=None, approved_by=None, confirm_cleanup=False):
    if (not confirm_cleanup or not isinstance(approved_by, str) or not approved_by.strip()
            or not isinstance(plan_sha256, str) or len(plan_sha256) != 64):
        raise ValueError('Limpieza requiere confirmacion explicita, approved_by y hash del plan.')
    preview = scan(project, include_external=include_external, max_items=max_items,
                   profile=profile, exclude=exclude)
    if plan_sha256 != preview['plan_sha256']:
        raise ValueError('El plan cambio; revisar una nueva vista previa.')
    deleted, errors, omitted = [], [], list(preview['omitted'])
    recovered = 0
    for item in preview['candidates']:
        path = Path(item['absolute_path'])
        removed_files = []
        try:
            if _protected(path, _canonical(preview['root'])):
                omitted.append({'path': item['path'], 'reason': 'ruta protegida al ejecutar'})
                continue
            before = _size(path)
            if path.is_file():
                path.unlink()
                deleted.append({'path': item['path'], 'size_bytes': before})
                recovered += before
            elif path.is_dir():
                removed_files = []
                for child in sorted(_walk_files(path), key=lambda value: len(value.parts), reverse=True):
                    child_size = _size(child)
                    child.unlink()
                    removed_files.append({'path': _display(child, _canonical(preview['root'])),
                                          'size_bytes': child_size})
                for current, dirs, _ in os.walk(path, topdown=False):
                    for name in dirs:
                        (Path(current) / name).rmdir()
                path.rmdir()
                deleted.extend(removed_files)
                recovered += sum(item['size_bytes'] for item in removed_files)
            else:
                omitted.append({'path': item['path'], 'reason': 'ya no existe'})
                continue
        except (OSError, ValueError) as exc:
            if 'removed_files' in locals() and removed_files:
                deleted.extend(removed_files)
                recovered += sum(item['size_bytes'] for item in removed_files)
            errors.append({'path': item['path'], 'reason': str(exc)})
    return {'command': 'cleanup-day', 'mode': 'executed', 'read_only': False,
            'approved_by': approved_by, 'deleted': deleted, 'omitted': omitted,
            'errors': errors, 'space_recovered_bytes': recovered,
            'space_recoverable_bytes': preview['space_recoverable_bytes'],
            'plan_sha256': preview['plan_sha256']}
