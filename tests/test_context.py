"""M1 tests for autonomous resume and shared context consolidation."""
import json
import unittest

import test_memory as fixtures
from context import consolidate


class ContextTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put
    cli = fixtures.MemoryTests.cli

    def test_consolidate_keeps_current_decisions_progress_pending_risks_and_last_session(self):
        result = consolidate({
            'partial': False,
            'issues': [],
            'limitations': [],
            'decisions': [
                {'id': 'old', 'title': 'Old', 'effective_status': 'sustituida; antecedente preservado'},
                {'id': 'current', 'title': 'Current', 'effective_status': 'validado'},
            ],
            'pending': [{'id': 'p1', 'title': 'Pending', 'status': 'pendiente', 'review_required': True}],
            'conflicts': [{'id': 'c1', 'title': 'Risk', 'resolution': 'pendiente', 'resolves': None}],
        }, [
            {'date': '2026-10-10', 'done': 'Earlier'},
            {'date': '2026-10-11', 'done': 'Latest', 'tests': 'Suite', 'tests_status': 'aprobadas'},
        ])
        self.assertEqual([x['id'] for x in result['decisions']], ['current'])
        self.assertEqual(result['last_session']['done'], 'Latest')
        self.assertEqual(result['pending'][0]['id'], 'p1')
        self.assertEqual({x['id'] for x in result['risks'] if x['kind'] == 'conflict'}, {'c1'})
        self.assertTrue(any(x['kind'] == 'pending' for x in result['risks']))

    def test_resume_exposes_structured_consolidated_context_without_writing(self):
        self.put('PROJECT_STATE.md', '# Estado\nEstado fixture')
        self.put('docs/memory/decisions.jsonl', json.dumps({
            'id': 'd1', 'title': 'Decision vigente', 'classification': 'confirmado',
            'status': 'validado'}) + '\n')
        self.put('docs/memory/pending.jsonl', json.dumps({
            'id': 'p1', 'title': 'Pendiente fixture', 'classification': 'confirmado',
            'status': 'pendiente'}) + '\n')
        self.put('docs/memory/conflicts.jsonl', json.dumps({
            'id': 'c1', 'title': 'Riesgo fixture', 'status': 'pendiente',
            'resolution': None}) + '\n')
        self.put('docs/memory/sessions/2026-10-11.json', json.dumps({
            'date': '2026-10-11', 'module': 'm1', 'done': 'Avance fixture',
            'pending': 'Continuar', 'tests_status': 'no_ejecutadas'}) + '\n')
        before = fixtures.snapshot(self.root)
        result = json.loads(self.cli('resume', '--json'))
        summary = result['consolidated_context']
        self.assertEqual(summary['decisions'][0]['id'], 'd1')
        self.assertEqual(summary['last_session']['done'], 'Avance fixture')
        self.assertEqual(summary['pending'][0]['id'], 'p1')
        self.assertTrue(any(r['id'] == 'c1' for r in summary['risks']))
        self.assertIn('CONTEXTO CONSOLIDADO', result['output'])
        self.assertEqual(before, fixtures.snapshot(self.root))


if __name__ == '__main__':
    unittest.main()
