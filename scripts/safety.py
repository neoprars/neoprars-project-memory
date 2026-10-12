"""Shared containment and Windows reparse-point checks (Python 3.10+)."""
import os
import re
import stat
from pathlib import Path


def is_link(path):
    # st_file_attributes is available before Path.is_junction (Python 3.12).
    info = path.lstat()
    return bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()


def safe_path(root, relative, exclusions=(), sensitive=None, max_depth=12):
    if not isinstance(relative, str) or not relative or len(relative) > 1000:
        return None
    if re.search(r'[\x00-\x1f\x7f@:?<>|]', relative) or relative.startswith(('/', '\\')):
        return None
    path = Path(relative)
    if path.is_absolute() or re.match(r'\w+:', relative):
        return None
    if sensitive and sensitive.search(relative):
        return None
    if len(path.parts) - 1 > max_depth:
        return None
    root = Path(root).absolute()
    current = root
    try:
        if is_link(root):
            return None
        for component in path.parts:
            # Check before normalizing .., so an excluded/link component cannot
            # be bypassed with a subsequent parent traversal.
            if component.casefold() in exclusions:
                return None
            current = current / component
            if not current.resolve().is_relative_to(root.resolve()):
                return None
            if os.path.lexists(current) and is_link(current):
                return None
        return current.resolve()
    except (OSError, ValueError):
        return None


def ensure_safe(root, relative, **kwargs):
    path = safe_path(root, relative, **kwargs)
    if path is None:
        raise ValueError('Ruta insegura, enlazada o fuera del proyecto.')
    return path
