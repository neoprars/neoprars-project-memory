"""V1.1.1: sequential coverage, budgets and read-only compatibility."""
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import unittest
import test_memory as fixtures
import audit


class LargeDocumentTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put
    cli = fixtures.MemoryTests.cli

    def document(self, report, name):
        return next(d for d in report['documents'] if d['path'] == name)

    def test_large_complete_and_contradictions_in_middle_and_end(self):
        text = '# Estado\n- [ ] identical fixture\n' + 'normal line\n' * 17000
        text += '- [x] identical fixture\n' + 'middle line\n' * 17000
        text += '- [ ] identical fixture\n'
        path = self.put('arbitrary.md', text)
        report = audit.audit_project(self.root, chunk_size=17)
        d = self.document(report, 'arbitrary.md')
        self.assertEqual(d['status'], 'completa')
        self.assertEqual(d['bytes_read'], path.stat().st_size)
        self.assertEqual(d['bytes_analysed'], path.stat().st_size)
        self.assertEqual(d['byte_ranges_read'], [[0, path.stat().st_size]])
        self.assertEqual(d['line_ranges_analysed'], [[1, len(text.splitlines())]])
        self.assertEqual(d['coverage_ratio'], 1)
        contradictions = [f for f in report['findings'] if 'marcas opuestas' in f['kind']]
        self.assertEqual(len(contradictions), 2)
        self.assertGreater(contradictions[-1]['lines'][-1], 34000)
        self.assertNotIn('identical fixture', json.dumps(report))

    def test_sequential_offsets_no_gaps_or_duplicate_budget(self):
        path = self.put('any.md', '# Estado\n' + 'record\n' * 30000)
        real_open = Path.open
        offsets = []
        class Traced:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def seek(self, offset): return self.stream.seek(offset)
            def read(self, size):
                start = self.stream.tell()
                data = self.stream.read(size)
                offsets.append((start, len(data)))
                return data
        def open_traced(p, *args, **kwargs):
            self.assertEqual(args[0], 'rb')
            return Traced(real_open(p, *args, **kwargs))
        before = fixtures.snapshot(self.root)
        with patch.object(Path, 'open', open_traced):
            report = audit.audit_project(self.root, chunk_size=113)
        position = 0
        for start, length in offsets:
            self.assertEqual(start, position)
            self.assertLessEqual(length, 113)
            position += length
        self.assertEqual(position, path.stat().st_size)
        self.assertEqual(report['coverage']['bytes_read'], position)
        self.assertEqual(before, fixtures.snapshot(self.root))

    def test_global_budget_partial_and_omitted(self):
        self.put('a.md', '# Estado\n' + 'line\n' * 30000)
        self.put('b.md', 'more\n' * 30000)
        report = audit.audit_project(self.root, max_bytes=30)
        self.assertEqual(report['coverage']['bytes_read'], 30)
        self.assertEqual(self.document(report, 'a.md')['status'], 'parcial')
        self.assertEqual(self.document(report, 'b.md')['status'], 'omitida')
        self.assertIn('global', self.document(report, 'b.md')['reason'])
        self.assertTrue(report['partial'])
        self.assertIn('no verificadas', self.document(report, 'a.md')['decisions'])

    def test_document_budget_and_file_limit(self):
        self.put('a.md', '# Estado\n' + 'line\n' * 30000)
        self.put('b.md', '# Decisiones\n' + 'line\n' * 30000)
        report = audit.audit_project(self.root, max_document_bytes=40, max_documents=1)
        self.assertEqual(self.document(report, 'a.md')['bytes_read'], 40)
        self.assertIn('por documento', self.document(report, 'a.md')['reason'])
        self.assertEqual(self.document(report, 'b.md')['bytes_read'], 0)
        self.assertIn('documentos agotado', self.document(report, 'b.md')['reason'])
        self.assertEqual(report['coverage']['documents_read'], 1)

    def test_explicit_authority_priority_without_fixed_names(self):
        self.put('a.md', 'Fuente oficial: [referencia](guides/z.adoc)\n')
        self.put('b.md', 'noise\n' * 30000)
        target = self.put('guides/z.adoc', 'Decisiones\n' + 'record\n' * 900)
        report = audit.audit_project(self.root, max_bytes=10000)
        d = self.document(report, 'guides/z.adoc')
        self.assertEqual(d['status'], 'completa')
        self.assertEqual(d['bytes_read'], target.stat().st_size)
        self.assertEqual(self.document(report, 'b.md')['status'], 'parcial')
        source = next(s for s in report['sources'] if s['path'] == 'guides/z.adoc')
        self.assertTrue(source['authority'].startswith('prioritaria'))

    def test_priority_also_applies_to_open_document_budget(self):
        self.put('a.md', 'Fuente oficial: [referencia](guides/z.adoc)\n')
        self.put('b.md', 'noise\n' * 30000)
        self.put('guides/z.adoc', 'Decisiones\n' + 'record\n' * 900)
        report = audit.audit_project(self.root, max_documents=2)
        self.assertEqual(self.document(report, 'guides/z.adoc')['status'], 'completa')
        self.assertEqual(self.document(report, 'b.md')['status'], 'omitida')
        self.assertEqual(report['coverage']['documents_read'], 2)

    def test_late_authority_link_found_after_large_prefix(self):
        origin = self.put('a.md', 'line\n' * 30000 + 'Fuente oficial: [referencia](guides/z.adoc)\n')
        self.put('b.md', 'noise\n' * 30000)
        target = self.put('guides/z.adoc', 'Decisiones\n' + 'record\n' * 900)
        report = audit.audit_project(self.root, max_bytes=origin.stat().st_size + target.stat().st_size + 10000)
        self.assertEqual(self.document(report, 'a.md')['status'], 'completa')
        self.assertEqual(self.document(report, 'guides/z.adoc')['status'], 'completa')
        self.assertEqual(self.document(report, 'b.md')['status'], 'parcial')

    def test_utf8_split_chunks_and_truncated_codepoint(self):
        path = self.put('any.md', '\ufeff# Decisión\n' + 'acción\n' * 30000)
        complete = audit.audit_project(self.root, chunk_size=1)
        self.assertEqual(self.document(complete, 'any.md')['status'], 'completa')
        self.assertIn('decisiones', complete['sources'][0]['categories'])
        partial = audit.audit_project(self.root, chunk_size=1, max_bytes=9)
        d = self.document(partial, 'any.md')
        self.assertEqual(d['status'], 'parcial')
        self.assertEqual(d['bytes_read'], 9)
        self.assertLess(d['bytes_analysed'], 9)
        self.assertGreater(path.stat().st_size, 128000)

    def test_time_exhaustion_between_chunks(self):
        path = self.put('any.md', 'Estado\n' * 30000)
        clock = [0.0]
        real_open = Path.open
        class Slow:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def seek(self, offset): return self.stream.seek(offset)
            def read(self, size):
                clock[0] += 0.6
                return self.stream.read(size)
        with patch.object(audit.time, 'monotonic', side_effect=lambda: clock[0]):
            with patch.object(Path, 'open', side_effect=lambda p, *a, **k: Slow(real_open(p, *a, **k)), autospec=True):
                docs, coverage, spent, opened = audit.read_documents(
                    self.root, [path], max_bytes=10000, max_document_bytes=10000,
                    max_documents=10, chunk_size=10, deadline=1, reasons=Counter())
        self.assertEqual(spent, 20)
        self.assertEqual(coverage[0]['status'], 'parcial')
        self.assertIn('tiempo', coverage[0]['reason'])

    def test_late_binary_marker_discards_document(self):
        self.put('any.txt', '# Decisiones\n' + 'line\n' * 30000 + '\x00')
        report = audit.audit_project(self.root, chunk_size=3000)
        self.assertEqual(self.document(report, 'any.txt')['status'], 'omitida')
        self.assertFalse(report['sources'])
        self.assertFalse(report['findings'])
        self.assertEqual(self.document(report, 'any.txt')['bytes_analysed'], 0)

    def test_partial_does_not_claim_unseen_contradiction(self):
        self.put('any.md', '# Decisiones\n- [ ] task\n' + 'line\n' * 30000 + '- [x] task\n')
        report = audit.audit_project(self.root, max_document_bytes=500)
        self.assertEqual(self.document(report, 'any.md')['status'], 'parcial')
        self.assertFalse(report['findings'])
        self.assertIn('no verificadas', self.document(report, 'any.md')['decisions'])

    def test_changed_document_discarded(self):
        path = self.put('any.md', '# Estado\n' + 'line\n' * 30000)
        real_open = Path.open
        class Changed:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def seek(self, offset): return self.stream.seek(offset)
            def read(self, size):
                result = self.stream.read(size)
                # Fixture mutation, not an audit write.
                with real_open(path, 'ab') as writer: writer.write(b'changed\n')
                return result
        with patch.object(Path, 'open', side_effect=lambda p, *a, **k: Changed(real_open(p, *a, **k)), autospec=True):
            report = audit.audit_project(self.root)
        self.assertEqual(self.document(report, 'any.md')['status'], 'omitida')
        self.assertIn('cambiado', self.document(report, 'any.md')['reason'])
        self.assertFalse(report['sources'])

    def test_new_cli_options_and_no_writes(self):
        self.put('any.md', '# Estado\n' + 'line\n' * 30000)
        before = fixtures.snapshot(self.root)
        text = self.cli('audit', '--json', '--max-document-bytes', '200000',
                        '--max-documents', '2', '--max-seconds', '10', '--chunk-size', '17')
        self.assertEqual(json.loads(text)['version'], '1.1.1')
        self.assertEqual(self.document(json.loads(text), 'any.md')['status'], 'completa')
        self.assertEqual(before, fixtures.snapshot(self.root))

    def test_invalid_new_limits(self):
        for args in ({'max_document_bytes': 0}, {'max_documents': 0}, {'chunk_size': 0},
                     {'max_seconds': 0}, {'max_seconds': float('nan')}, {'max_seconds': float('inf')}):
            with self.assertRaises(ValueError): audit.audit_project(self.root, **args)


if __name__ == '__main__':
    unittest.main()
