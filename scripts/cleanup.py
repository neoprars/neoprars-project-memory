"""Conservative, provider-neutral cleanup preview and execution."""
import hashlib
import json
import os
import tempfile
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


def _size(path):
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    return sum(_size(file) for file in _walk_files(path))


def _display(path, root):
    if _inside(path, root):
        return _canonical(path).relative_to(_canonical(root)).as_posix()
    return safe_label(_canonical(path).as_posix())


def _candidate(path, root, scope, reason):
    return {'path': _display(path, root), 'absolute_path': str(_canonical(path)),
            'scope': scope, 'reason': reason, 'size_bytes': _size(path), 'verified': True}


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


def scan(project=None, *, include_external=True, max_items=500):
    if max_items < 1:
        raise ValueError('max_items invalido')
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
            if _protected(current_path, root):
                dirs[:] = []
                continue
            for name in list(dirs):
                path = current_path / name
                if path.name in BUILD_DIRS or path.name in ('bin', 'obj'):
                    if _protected(path, root):
                        omitted.append({'path': _display(path, root), 'reason': 'ruta protegida'})
                    elif _artifact_dir(path):
                        candidates.append(_candidate(path, root, 'project', 'directorio de artefactos verificable'))
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
            'include_external': bool(include_external), 'candidates': unique}
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {'command': 'cleanup-day', 'mode': 'preview', 'read_only': True,
            'root': safe_label(str(root)), 'candidates': unique, 'omitted': omitted,
            'errors': [], 'space_recoverable_bytes': sum(x['size_bytes'] for x in unique),
            'plan_sha256': plan_hash, 'next': 'Revisar la vista previa y ejecutar sólo con autorización explícita.'}


def execute(project=None, *, include_external=True, max_items=500, plan_sha256=None,
            approved_by=None, confirm_cleanup=False):
    if (not confirm_cleanup or not isinstance(approved_by, str) or not approved_by.strip()
            or not isinstance(plan_sha256, str) or len(plan_sha256) != 64):
        raise ValueError('Limpieza requiere confirmacion explicita, approved_by y hash del plan.')
    preview = scan(project, include_external=include_external, max_items=max_items)
    if plan_sha256 != preview['plan_sha256']:
        raise ValueError('El plan cambio; revisar una nueva vista previa.')
    deleted, errors, omitted = [], [], list(preview['omitted'])
    recovered = 0
    for item in preview['candidates']:
        path = Path(item['absolute_path'])
        try:
            if _protected(path, _canonical(preview['root'])):
                omitted.append({'path': item['path'], 'reason': 'ruta protegida al ejecutar'})
                continue
            before = _size(path)
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                for child in sorted(_walk_files(path), key=lambda value: len(value.parts), reverse=True):
                    child.unlink()
                for current, dirs, _ in os.walk(path, topdown=False):
                    for name in dirs:
                        (Path(current) / name).rmdir()
                path.rmdir()
            else:
                omitted.append({'path': item['path'], 'reason': 'ya no existe'})
                continue
            deleted.append({'path': item['path'], 'size_bytes': before})
            recovered += before
        except (OSError, ValueError) as exc:
            errors.append({'path': item['path'], 'reason': str(exc)})
    return {'command': 'cleanup-day', 'mode': 'executed', 'read_only': False,
            'approved_by': approved_by, 'deleted': deleted, 'omitted': omitted,
            'errors': errors, 'space_recovered_bytes': recovered,
            'space_recoverable_bytes': preview['space_recoverable_bytes'],
            'plan_sha256': preview['plan_sha256']}
