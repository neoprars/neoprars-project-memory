"""M4 integration regressions: streaming registries, scope, UTF-8 and resume output."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_memory as fixtures
import audit
import start


class M4Tests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put

    def source(self):
        path = self.put('notes/facts.md', 'Evidencia UTF-8: decisión válida\n')
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def memory_files(self, registry):
        self.put('docs/memory/INDEX.md', '[facts](../../notes/facts.md)\n')
        self.put('docs/memory/sources.json', json.dumps(registry))
        for name in ('decisions.jsonl', 'conflicts.jsonl', 'pending.jsonl'):
            self.put('docs/memory/' + name, '')

    def test_large_sources_stream_without_json_loads_or_full_materialization(self):
        source, digest = self.source()
        registry = {'schema_version': 1, 'sources': [
            {'id': 'facts-%04d' % number, 'path': 'notes/facts.md', 'sha256': digest}
            for number in range(450)
        ]}
        self.memory_files(registry)
        with patch.object(start.json, 'loads', side_effect=AssertionError('sources loaded as one document')):
            report = start.start_project(self.root, max_bytes=2000000, max_document_bytes=2000000)
        self.assertEqual(len(report['sources']), 450)
        registry_coverage = next(item for item in report['coverage']['files']
                                 if item['path'].endswith('sources.json'))
        self.assertEqual(registry_coverage['coverage_class'], 'completa')
        self.assertEqual(registry_coverage['integrity'], 'verificada por lectura estable')

    def test_limited_and_incomplete_coverage_are_distinct(self):
        source, digest = self.source()
        self.memory_files({'schema_version': 1, 'sources': [
            {'id': 'facts', 'path': 'notes/facts.md', 'sha256': digest}]})
        limited_budget = start.Budget(10, 10, 10, 8, start.time.monotonic() + 5)
        _, limited_item = limited_budget.stream_sources(self.root / 'docs/memory/sources.json')
        self.assertEqual(limited_item['coverage_class'], 'limitada')
        invalid_state = self.root / 'PROJECT_STATE.md'
        invalid_state.write_bytes(b'Estado invalido: \xff')
        state = start.Budget(10000, 10, 10000, 128, start.time.monotonic() + 5)
        _, incomplete = state.read(self.root / 'PROJECT_STATE.md')
        self.assertEqual(incomplete['coverage_class'], 'incompleta')
        self.assertEqual(incomplete['integrity'], 'no verificable')

    def test_project_state_coverage_is_reported_by_resume(self):
        source, digest = self.source()
        self.memory_files({'schema_version': 1, 'sources': [
            {'id': 'facts', 'path': 'notes/facts.md', 'sha256': digest}]})
        self.put('PROJECT_STATE.md', 'Estado vigente\n')
        result = json.loads(self._cli('resume', '--json'))
        coverage = result['context']['state']['coverage']
        self.assertEqual(coverage['coverage_class'], 'completa')
        self.assertEqual(coverage['integrity'], 'verificada por lectura estable')

    def test_explicit_project_wins_over_nested_git_scope(self):
        nested = self.root / 'nested-repository'
        nested.mkdir()
        (nested / '.git').mkdir()
        resolved, evidence = audit.discover_root(nested)
        self.assertEqual(resolved, nested.resolve())
        self.assertIn('--project', evidence)

    def test_cli_utf8_without_python_utf8_switch_and_external_cwd(self):
        self.put('PROJECT_STATE.md', 'Estado UTF-8: decisión válida\n')
        self.memory_files({'schema_version': 1, 'sources': []})
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'memory.py'
        env = {key: value for key, value in os.environ.items()
               if key not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
        result = subprocess.run([sys.executable, '-B', str(script), '--project', str(self.root),
                                 'resume', '--full-context'], cwd=Path(tempfile.gettempdir()),
                                capture_output=True, env=env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertIn('decisión válida', result.stdout.decode('utf-8'))

    def _cli(self, *args):
        command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts' / 'memory.py'),
                   '--project', str(self.root), *args]
        result = subprocess.run(command, cwd=self.root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


if __name__ == '__main__':
    unittest.main()
