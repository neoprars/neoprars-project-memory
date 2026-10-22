"""Directed tests for conservative cleanup-day preview and protection."""
import json
from pathlib import Path
import tempfile
import unittest

import test_memory as fixtures

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cleanup


class CleanupDayTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put

    def test_preview_detects_verified_artifacts_without_deleting_or_touching_protected_files(self):
        artifact = self.put('__pycache__/module.pyc', 'bytecode')
        source = self.put('src/module.py', 'source')
        protected = self.put('docs/memory/sessions/session.json', 'memory')
        protected.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as external:
            external_path = Path(external)
            with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as unrelated:
                unrelated_path = Path(unrelated)
                (unrelated_path / 'other-agent.tmp').write_text('foreign', encoding='utf-8')
                (external_path / cleanup.MARKER).write_text(json.dumps({
                    'schema_version': 1, 'project_root': str(self.root), 'discardable': True}), encoding='utf-8')
                (external_path / 'pytest-artifact.tmp').write_text('temp', encoding='utf-8')
                report = cleanup.scan(self.root)
                paths = {item['path'] for item in report['candidates']}
                self.assertIn('__pycache__', paths)
                self.assertIn(str(external_path.resolve()), {item['absolute_path'] for item in report['candidates']})
                self.assertNotIn(str(unrelated_path.resolve()), {item['absolute_path'] for item in report['candidates']})
                self.assertGreater(report['space_recoverable_bytes'], 0)
                self.assertTrue(artifact.exists())
                self.assertTrue(source.exists())
                self.assertTrue(protected.exists())

    def test_execute_requires_explicit_authorization_and_reports_recovery(self):
        artifact = self.put('__pycache__/module.pyc', 'bytecode')
        source = self.put('src/module.py', 'source')
        with self.assertRaises(ValueError):
            cleanup.execute(self.root)
        preview = cleanup.scan(self.root, include_external=False)
        with self.assertRaises(ValueError):
            cleanup.execute(self.root, include_external=False, approved_by='fixture-user',
                            confirm_cleanup=True)
        with self.assertRaises(ValueError):
            cleanup.execute(self.root, include_external=False, plan_sha256='0' * 64,
                            approved_by='fixture-user', confirm_cleanup=True)
        result = cleanup.execute(self.root, include_external=False,
                                 plan_sha256=preview['plan_sha256'],
                                 approved_by='fixture-user', confirm_cleanup=True)
        self.assertEqual(result['mode'], 'executed')
        self.assertEqual(len(result['errors']), 0)
        self.assertFalse(artifact.exists())
        self.assertTrue(source.exists())
        self.assertGreater(result['space_recovered_bytes'], 0)

    def test_rejects_foreign_or_false_markers_and_skips_active_or_symlinked_artifacts(self):
        active = self.put('__pycache__/active.pyc', 'bytecode')
        self.put('__pycache__/.lock', 'active')
        protected = self.put('docs/memory/sessions/keep.json', 'memory')
        target = self.put('real-artifacts/module.pyc', 'bytecode')
        link = self.root / 'linked-cache'
        try:
            link.symlink_to(target.parent, target_is_directory=True)
        except OSError:
            link = None
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as valid, \
             tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as false_marker, \
             tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as foreign:
            Path(valid, cleanup.MARKER).write_text(json.dumps({
                'schema_version': 1, 'project_root': str(self.root), 'discardable': True}), encoding='utf-8')
            Path(valid, 'artifact.tmp').write_text('owned', encoding='utf-8')
            Path(false_marker, cleanup.MARKER).write_text(json.dumps({
                'schema_version': 1, 'project_root': str(self.root), 'discardable': False}), encoding='utf-8')
            Path(false_marker, 'artifact.tmp').write_text('false', encoding='utf-8')
            Path(foreign, cleanup.MARKER).write_text(json.dumps({
                'schema_version': 1, 'project_root': str(self.root / 'other-project'), 'discardable': True}), encoding='utf-8')
            Path(foreign, 'artifact.tmp').write_text('foreign', encoding='utf-8')
            report = cleanup.scan(self.root)
            absolute = {item['absolute_path'] for item in report['candidates']}
            self.assertIn(str(Path(valid).resolve()), absolute)
            self.assertNotIn(str(Path(false_marker).resolve()), absolute)
            self.assertNotIn(str(Path(foreign).resolve()), absolute)
            self.assertNotIn(str(protected.resolve()), absolute)
            self.assertNotIn(str(active.parent.resolve()), absolute)
            if link is not None:
                self.assertNotIn(str(link.resolve()), absolute)
        self.assertTrue(active.exists())
        self.assertTrue(protected.exists())

    def test_protects_docs_audits_and_nested_pycache_from_preview_and_execution(self):
        protected = self.put('docs/audits/report.json', 'audit evidence')
        nested = self.put('docs/audits/__pycache__/audit.cpython-311.pyc', 'bytecode')
        other = self.put('docs/audits/nested/deep.txt', 'audit evidence')

        preview = cleanup.scan(self.root, include_external=False)
        candidates = {item['absolute_path'] for item in preview['candidates']}
        self.assertFalse(any(str(path.resolve()) in candidates for path in (protected, nested, other)))
        self.assertNotIn(str((self.root / 'docs' / 'audits' / '__pycache__').resolve()), candidates)

        result = cleanup.execute(self.root, include_external=False,
                                 plan_sha256=preview['plan_sha256'],
                                 approved_by='fixture-user', confirm_cleanup=True)
        self.assertEqual(result['mode'], 'executed')
        self.assertEqual(result['deleted'], [])
        self.assertTrue(protected.exists())
        self.assertTrue(nested.exists())
        self.assertTrue(other.exists())

    def test_build_cache_profile_requires_project_evidence_and_keeps_default_behavior(self):
        project = self.root / 'app'
        (project / 'app.csproj').parent.mkdir(parents=True, exist_ok=True)
        (project / 'app.csproj').write_text('<Project />', encoding='utf-8')
        artifact = self.put('app/bin/Debug/app.dll', 'artifact')
        legacy_only = self.put('other/bin/Debug/library.dll', 'artifact')

        legacy = cleanup.scan(self.root, include_external=False)
        self.assertIn(str(artifact.parent.parent.resolve()),
                      {item['absolute_path'] for item in legacy['candidates']})
        profile = cleanup.scan(self.root, include_external=False, profile='build-cache')
        paths = {item['absolute_path'] for item in profile['candidates']}
        self.assertIn(str(artifact.parent.parent.resolve()), paths)
        self.assertNotIn(str(legacy_only.parent.parent.resolve()), paths)
        self.assertTrue(legacy_only.exists())

    def test_build_cache_rejects_distribution_outputs_and_honors_exclusions(self):
        project = self.root / 'app'
        project.mkdir()
        (project / 'package.json').write_text('{}', encoding='utf-8')
        signed = self.put('app/bin/Release/app-Signed.apk', 'distribution')
        excluded = self.put('app/obj/Debug/generated.dll', 'artifact')

        report = cleanup.scan(self.root, include_external=False, profile='build-cache',
                              exclude=['app/obj'])
        paths = {item['absolute_path'] for item in report['candidates']}
        self.assertNotIn(str(signed.parent.parent.resolve()), paths)
        self.assertNotIn(str(excluded.parent.parent.resolve()), paths)
        self.assertTrue(signed.exists())
        self.assertTrue(excluded.exists())
        reasons = {item['reason'] for item in report['omitted']}
        self.assertIn('exclusion configurable', reasons)

    def test_build_cache_rejects_source_and_active_content(self):
        project = self.root / 'app'
        project.mkdir()
        (project / 'pyproject.toml').write_text('[build-system]\n', encoding='utf-8')
        source = self.put('app/bin/generated.py', 'source')
        lock = self.put('app/obj/.lock', 'active')

        report = cleanup.scan(self.root, include_external=False, profile='build-cache')
        paths = {item['absolute_path'] for item in report['candidates']}
        self.assertNotIn(str(source.parent.parent.resolve()), paths)
        self.assertNotIn(str(lock.parent.parent.resolve()), paths)
        self.assertTrue(source.exists())
        self.assertTrue(lock.exists())


if __name__ == '__main__':
    unittest.main()
