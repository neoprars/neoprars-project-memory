"""M3 concurrency and documentation freshness tests."""
import hashlib
import json
import unittest
from unittest.mock import patch

import test_memory as fixtures
import start
from workflow import ConcurrentChangeError, apply, prepare
from store import Store


class M3Tests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put

    def seed_source(self):
        source = self.put('notes/facts.md', 'Current documentation\n')
        self.put('docs/memory/INDEX.md', '[facts](../../notes/facts.md)\n')
        self.put('docs/memory/sources.json', json.dumps({'schema_version': 1, 'sources': [{
            'id': 'facts', 'path': 'notes/facts.md',
            'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'authority': 'confirmada'}], 'revisions': []}))
        return source

    def decision(self):
        return {'schema_version': 2, 'id': 'd1', 'date': '2026-10-17',
                'created_at': '2026-10-17T12:00:00-03:00', 'classification': 'confirmado',
                'status': 'validado', 'evidence': [{'source_id': 'facts'}],
                'title': 'Decision', 'reason': 'Reason', 'detail': 'Detail', 'supersedes': None}

    def test_apply_reports_concurrent_source_change_without_writing(self):
        source = self.seed_source()
        store = Store(self.root, max_bytes=100000, max_documents=20,
                      max_document_bytes=10000, max_seconds=20)
        proposal = prepare(store, 'decision', self.decision())
        source.write_text('Changed documentation\n', encoding='utf-8')
        before = fixtures.snapshot(self.root)
        with self.assertRaises(ConcurrentChangeError) as error:
            apply(Store(self.root, max_bytes=100000, max_documents=20,
                        max_document_bytes=10000, max_seconds=20), proposal,
                  proposal['approval_sha256'], 'fixture-user', 'supervised')
        self.assertIn('sources/notes/facts.md', str(error.exception))
        self.assertEqual(before, fixtures.snapshot(self.root))

    def test_registered_document_freshness_changes_without_auto_repair(self):
        source = self.seed_source()
        report = start.start_project(self.root)
        self.assertEqual(report['sources'][0]['freshness'], 'confirmada')
        self.assertEqual(report['references'][0]['freshness'], 'confirmada')
        source.write_text('Stale documentation\n', encoding='utf-8')
        report = start.start_project(self.root)
        self.assertEqual(report['sources'][0]['freshness'], 'posiblemente obsoleta')
        self.assertEqual(report['references'][0]['freshness'], 'posiblemente obsoleta')
        self.assertTrue(report['partial'])
        self.assertEqual(source.read_text(encoding='utf-8'), 'Stale documentation\n')

    def test_source_registry_change_is_a_concurrency_conflict(self):
        self.seed_source()
        store = Store(self.root, max_bytes=100000, max_documents=20,
                      max_document_bytes=10000, max_seconds=20)
        proposal = prepare(store, 'decision', self.decision())
        registry_path = self.root / 'docs/memory/sources.json'
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        registry['revisions'].append({'reason': 'independent revision'})
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        with self.assertRaises(ConcurrentChangeError) as error:
            apply(Store(self.root, max_bytes=100000, max_documents=20,
                        max_document_bytes=10000, max_seconds=20), proposal,
                  proposal['approval_sha256'], 'fixture-user', 'supervised')
        self.assertIn('memory/sources.json', str(error.exception))

    def test_missing_reference_is_not_presented_as_confirmed(self):
        self.put('docs/memory/INDEX.md', '[missing](../../notes/missing.md)\n')
        report = start.start_project(self.root)
        self.assertEqual(report['references'][0]['status'], 'ausente')
        self.assertEqual(report['references'][0]['freshness'], 'no verificable')
        self.assertTrue(report['partial'])

    def test_unrelated_git_change_does_not_mark_document_stale(self):
        self.seed_source()
        with patch.object(start, 'git_read', return_value='?? unrelated.md'):
            report = start.start_project(self.root)
        self.assertEqual(report['git']['status'], 'verificable')
        self.assertEqual(report['sources'][0]['freshness'], 'confirmada')
        self.assertEqual(report['references'][0]['freshness'], 'confirmada')


if __name__ == '__main__':
    unittest.main()
