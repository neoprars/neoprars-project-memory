"""Bounded readers and contained persistence; no external packages."""
import datetime as dt
import json
import os
import re
import time
import uuid
from pathlib import Path

from audit import EXCLUDED, SENSITIVE, safe_label, discover_root
from safety import ensure_safe
from start import Budget

READ_DEFAULTS = dict(max_files=5000, max_bytes=2000000, max_depth=12,
    max_document_bytes=1000000, max_documents=1000, max_seconds=20, chunk_size=16384)


class Store:
    def __init__(self, project=None, memory_root='docs/memory', exclude=(), **limits):
        self.limits = {**READ_DEFAULTS, **limits}
        self.exclusions = EXCLUDED | {x.casefold() for x in exclude}
        if any(self.limits[k] <= 0 for k in READ_DEFAULTS if k != 'max_depth') or self.limits['max_depth'] < 0:
            raise ValueError('Limites invalidos.')
        self.deadline = time.monotonic() + self.limits['max_seconds']
        self.root, self.root_evidence = discover_root(project, deadline=self.deadline)
        self.location = memory_root or 'docs/memory'
        self.folder = ensure_safe(self.root, self.location, exclusions=self.exclusions,
            sensitive=SENSITIVE, max_depth=self.limits['max_depth'])
        self.location = self.folder.relative_to(self.root).as_posix()
        self.budget = Budget(self.limits['max_bytes'], self.limits['max_documents'],
            self.limits['max_document_bytes'], self.limits['chunk_size'], self.deadline)
        self.issues = []
        self.entries = 0

    def path(self, name, *, export=False):
        exclusions = self.exclusions - {'exports'} if export else self.exclusions
        return ensure_safe(self.root, self.location + '/' + name, exclusions=exclusions,
            sensitive=SENSITIVE, max_depth=self.limits['max_depth'])

    def read(self, name, *, official=False):
        if self.entries >= self.limits['max_files'] or time.monotonic() >= self.deadline:
            self.issues.append('Presupuesto de entradas o tiempo agotado.')
            return None
        self.entries += 1
        try:
            path = ensure_safe(self.root, name, exclusions=self.exclusions, sensitive=SENSITIVE,
                max_depth=self.limits['max_depth']) if official else self.path(name)
            if not path.is_file():
                self.issues.append('Fuente ausente: ' + safe_label(name))
                return None
            text, coverage = self.budget.read(path)
            coverage['path'] = safe_label(path.relative_to(self.root).as_posix())
            if coverage['status'] != 'completa':
                self.issues.append('Lectura no completa: ' + safe_label(name))
            return text
        except (OSError, ValueError):
            self.issues.append('Fuente insegura o inaccesible: ' + safe_label(name))
            return None

    def objects(self, name, *, jsonl=False):
        text = self.read(name)
        if text is None:
            return []
        if not jsonl and self.budget.coverage[-1]['status'] != 'completa':
            return []
        out = []
        for number, line in enumerate(text.splitlines() if jsonl else [text], 1):
            if not line.strip():
                continue
            if self.entries >= self.limits['max_files'] or time.monotonic() >= self.deadline:
                self.issues.append('Presupuesto agotado al interpretar registros.')
                break
            self.entries += 1
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError()
                out.append(obj)
            except (ValueError, RecursionError):
                self.issues.append('Registro invalido: ' + safe_label(name) + ':' + str(number))
        return out

    def sessions(self):
        names = []
        try:
            folder = self.path('sessions')
            if not folder.exists():
                return []
            with os.scandir(folder) as scan:
                for entry in scan:
                    if self.entries >= self.limits['max_files'] or time.monotonic() >= self.deadline:
                        self.issues.append('Inventario de sesiones parcial.')
                        break
                    self.entries += 1
                    if entry.name.endswith('.json'):
                        names.append(entry.name)
            records = []
            for name in sorted(names):
                records.extend(self.objects('sessions/' + name))
            return sorted(records, key=lambda x: str(x.get('date', '')))
        except (OSError, ValueError):
            self.issues.append('Sesiones inseguras o inaccesibles.')
            return []

    def coverage(self):
        return {'partial': bool(self.issues), 'issues': self.issues,
            'bytes_read': self.budget.spent, 'documents_read': self.budget.opened,
            'entries_checked': self.entries, 'files': self.budget.coverage}

    def mkdir_for(self, name, *, export=False):
        path = self.path(name, export=export)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after mkdir and immediately before each write.
        return self.path(name, export=export)

    def create(self, name, data, *, export=False):
        path = self.mkdir_for(name, export=export)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(data)
        return path

    def append_event(self, name, event):
        # Existing content must be complete and valid before append; malformed
        # tails are never silently concatenated with a new JSON record.
        path = self.path(name)
        if path.exists():
            previous = self.read(name)
            if previous is None or self.budget.coverage[-1]['status'] != 'completa':
                raise ValueError('Registro no verificable; no se agrega evento.')
            for line in previous.splitlines():
                if line.strip() and not isinstance(json.loads(line), dict):
                    raise ValueError('Registro invalido; no se agrega evento.')
        else:
            previous = ''
        path = self.mkdir_for(name)
        prefix = '\n' if previous and not previous.endswith('\n') else ''
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(prefix + json.dumps(event, ensure_ascii=False) + '\n')
        return path

    def replace_sources(self, obj):
        # Only sources.json is replaceable. Old snapshots remain in revisions.
        temp_name = 'sources-' + uuid.uuid4().hex + '.tmp'
        temp = self.create(temp_name, json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
        try:
            target = self.path('sources.json')
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()


def scrub(text):
    """Conservative export guard, not a certification of absence of secrets."""
    pattern = r'(?i)(?:ghp_|github_pat_|sk-)[\w-]+|\beyJ[\w.-]+|\S+@\S+|https?://\S+|(?:password|token|secret|contrase\w*)\s*[:=]\s*\S+'
    return re.sub(pattern, '[dato omitido]', text)
