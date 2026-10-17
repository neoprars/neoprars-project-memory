"""M2 selective loading, partial recovery and regression tests."""
import json
import os
from pathlib import Path
import unittest

import test_memory as fixtures
from context import consolidate
from store import Store


class M2Tests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put
    cli = fixtures.MemoryTests.cli

    def make_sessions(self, count):
        for number in range(count):
            self.put('docs/memory/sessions/{:04d}.json'.format(number), json.dumps({
                'date': '2026-10-{:02d}'.format((number % 28) + 1),
                'module': 'volume', 'done': 'session {}'.format(number)}) + '\n')

    def test_selective_session_loading_reads_less_history_and_reports_omissions(self):
        self.make_sessions(120)
        full = Store(self.root, max_bytes=2_000_000, max_documents=5000,
                     max_document_bytes=10000, max_seconds=20)
        full_records = full.sessions()
        selective = Store(self.root, max_bytes=2_000_000, max_documents=5000,
                          max_document_bytes=10000, max_seconds=20)
        selected_records = selective.sessions(limit=5)
        self.assertEqual(len(full_records), 120)
        self.assertEqual(len(selected_records), 5)
        self.assertLess(selective.coverage()['bytes_read'], full.coverage()['bytes_read'])
        self.assertTrue(any(item.get('reason') == 'historial omitido'
                            for item in selective.coverage()['omitted']))
        self.assertTrue(selective.coverage()['partial'])

    def test_invalid_json_and_missing_files_keep_recoverable_records_and_are_reported(self):
        self.put('docs/memory/decisions.jsonl',
                 json.dumps({'id': 'd1', 'title': 'Recoverable'}) + '\n{invalid\n')
        store = Store(self.root, max_bytes=10000, max_documents=20,
                      max_document_bytes=10000, max_seconds=20)
        records = store.objects('decisions.jsonl', jsonl=True)
        store.objects('pending.jsonl', jsonl=True)
        self.assertEqual([record['id'] for record in records], ['d1'])
        coverage = store.coverage()
        self.assertTrue(coverage['partial'])
        self.assertTrue(any(item.get('reason') == 'JSON invalido' for item in coverage['omitted']))
        self.assertTrue(any(item.get('reason') == 'ausente' for item in coverage['omitted']))

    def test_compact_context_is_bounded_and_deduplicated(self):
        context = {'partial': True, 'issues': ['historial omitido'], 'omitted': [{'path': 'sessions/'}],
                   'decisions': [{'id': 'd1', 'title': 'Decision', 'status': 'validado'},
                                 {'id': 'd1', 'title': 'Decision repetida', 'status': 'validado'},
                                 {'id': 'd2', 'title': 'Otra', 'status': 'validado'}],
                   'pending': [{'id': 'p1', 'title': 'Pending', 'status': 'pendiente'}],
                   'conflicts': []}
        result = consolidate(context, [{'date': '2026-10-17', 'done': 'Avance'}], max_items=1)
        self.assertEqual([item['id'] for item in result['decisions']], ['d1'])
        self.assertEqual(result['limits']['omitted']['decisions'], 2)
        self.assertTrue(result['coverage']['partial'])
        self.assertEqual(result['coverage']['omitted'][0]['path'], 'sessions/')

    def test_resume_and_handoff_regression_keep_m1_fields(self):
        self.put('PROJECT_STATE.md', 'Estado vigente')
        self.put('docs/memory/decisions.jsonl', json.dumps({
            'id': 'd1', 'title': 'Decision', 'schema_version': 2,
            'classification': 'confirmado', 'status': 'validado'}) + '\n')
        self.put('docs/memory/sessions/2026-10-17.json', json.dumps({
            'date': '2026-10-17', 'done': 'Avance', 'tests_status': 'aprobadas'}) + '\n')
        resume = json.loads(self.cli('resume', '--json', '--session-limit', '1', '--max-context-items', '5'))
        self.assertIn('consolidated_context', resume)
        self.assertIn('decisions', resume['consolidated_context'])
        handoff = json.loads(self.cli('handoff', '--preview', '--sessions', '1', '--max-context-items', '5'))
        self.assertIn('consolidated_context', handoff)
        self.assertIn('## Contexto consolidado', handoff['output'])


if __name__ == '__main__':
    unittest.main()
