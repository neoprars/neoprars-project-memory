"""M4.1 regressions for compact resume and isolated state verification."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_memory as fixtures
import start


class M41Tests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put

    def seed(self, state='Estado vigente\n'):
        self.put('PROJECT_STATE.md', state)
        self.put('docs/memory/INDEX.md', '# Memoria\n')
        self.put('docs/memory/sources.json', json.dumps({'schema_version': 1, 'sources': []}))
        for name in ('decisions.jsonl', 'conflicts.jsonl', 'pending.jsonl'):
            self.put('docs/memory/' + name, '')

    def cli(self, *args):
        command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts' / 'memory.py'),
                   '--project', str(self.root), *args]
        result = subprocess.run(command, cwd=self.root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_resume_default_is_compact_and_full_context_is_explicit(self):
        self.seed('ESTADO_COMPLETO_NO_DEBE_APARECER\n' + ('detalle\n' * 2000))
        records = [json.dumps({'schema_version': 2, 'id': 'd%d' % n,
                               'title': 'Decision %d' % n, 'classification': 'historico',
                               'status': 'validado', 'evidence': []}) for n in range(25)]
        self.put('docs/memory/decisions.jsonl', '\n'.join(records) + '\n')
        compact = json.loads(self.cli('resume', '--json'))
        consolidated = compact['consolidated_context']
        self.assertTrue(all(len(consolidated[name]) <= 10
                            for name in ('decisions', 'progress', 'pending', 'risks')))
        self.assertNotIn('ESTADO_COMPLETO_NO_DEBE_APARECER', compact['output'])
        self.assertLess(len(compact['output'].encode('utf-8')), 20000)
        full = json.loads(self.cli('resume', '--json', '--full-context'))
        self.assertIn('ESTADO_COMPLETO_NO_DEBE_APARECER', full['output'])
        self.assertGreater(len(full['output']), len(compact['output']))

    def test_project_state_is_verified_in_isolated_budget(self):
        self.seed('Estado íntegro\n' + ('línea de estado\n' * 3000))
        result = json.loads(self.cli('resume', '--json', '--max-bytes', '1000'))
        coverage = result['context']['state']['coverage']
        self.assertEqual(coverage['coverage_class'], 'completa')
        self.assertEqual(coverage['budget_scope'], 'documento oficial aislado')
        self.assertLessEqual(result['coverage']['bytes_read'], 1000)

    def test_start_partial_explains_unverified_sources(self):
        self.seed()
        report = start.start_project(self.root)
        self.assertFalse(report['partial'])
        self.put('docs/memory/sources.json', json.dumps({'schema_version': 1, 'sources': [
            {'id': 'missing', 'path': 'missing.md', 'sha256': 'a' * 64}]}))
        report = start.start_project(self.root)
        self.assertTrue(report['partial'])
        self.assertTrue(any('fuentes no verificadas' in reason for reason in report['partial_reasons']))


if __name__ == '__main__':
    unittest.main()
