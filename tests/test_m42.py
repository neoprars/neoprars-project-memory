"""M4.2 regressions for provider-neutral execution and compact resume metadata."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_memory as fixtures


class M42Tests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put

    def seed(self):
        self.put('PROJECT_STATE.md', 'Estado vigente\n')
        self.put('docs/memory/INDEX.md', '# Memoria\n')
        self.put('docs/memory/sources.json', json.dumps({'schema_version': 1, 'sources': [
            {'id': 'missing', 'path': 'missing.md', 'sha256': 'a' * 64}]}))
        for name in ('decisions.jsonl', 'conflicts.jsonl', 'pending.jsonl'):
            self.put('docs/memory/' + name, '')

    def cli(self, *args):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'memory.py'
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script),
                                 '--project', str(self.root), *args],
                                cwd=self.root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_resume_exposes_partial_reasons_and_state_integrity_compactly(self):
        self.seed()
        result = self.cli('resume', '--json')
        coverage = result['consolidated_context']['coverage']
        self.assertTrue(coverage['partial'])
        self.assertTrue(any('fuentes no verificadas' in item
                            for item in coverage['partial_reasons']))
        self.assertEqual(coverage['state']['coverage_class'], 'completa')
        self.assertEqual(coverage['state']['integrity'], 'verificada por lectura estable')
        self.assertEqual(coverage['state']['budget_scope'], 'documento oficial aislado')
        self.assertLess(len(result['output'].encode('utf-8')), 20000)

    def test_skill_documents_execution_contract_without_provider_dependency(self):
        skill = (Path(__file__).resolve().parents[1] / 'SKILL.md').read_text(encoding='utf-8')
        for phrase in ('Contrato operativo para Codex y Claude Code',
                       'Cargar `SKILL.md` solo incorpora instrucciones',
                       'SKILL_ROOT/scripts/memory.py',
                       'context.partial_reasons',
                       'no depende de Codex, Claude Code'):
            self.assertIn(phrase, skill)


if __name__ == '__main__':
    unittest.main()
