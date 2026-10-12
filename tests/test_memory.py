"""Compatibility and audit safety tests. Fixtures contain synthetic data only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))
import audit


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('NEOPRARS_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def cli(self, *args, project=True, cwd=None):
        if args and args[0]=='handoff' and '--json' not in args and '--help' not in args:
            args=(*args,'--json')
        command = [sys.executable, '-B', str(SCRIPTS / 'memory.py')]
        if project:
            command += ['--project', str(self.root)]
        result = subprocess.run(command + list(args), cwd=cwd or self.root,
                                capture_output=True, encoding='utf-8', timeout=20,
                                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def confirm(self, output):
        proposal = json.loads(output)
        self.put('work/proposal.json', json.dumps(proposal))
        return self.cli('apply', '--proposal', 'work/proposal.json', '--mode', 'supervised',
                        '--approval-sha256', proposal['approval_sha256'], '--approved-by', 'fixture-user')

    def test_help_keeps_commands(self):
        text = self.cli('help')
        self.assertIn('V1.4', text)
        for cmd in ('audit', 'recall', 'summary', 'handoff', 'decisions', 'resume',
                    'close', 'roadmap', 'history', 'add-decision'):
            self.assertIn(cmd, text)

    def test_seven_capabilities_and_formats(self):
        self.put('PROJECT_STATE.md', '# Estado\nTrabajo verificado')
        self.put('ROADMAP.md', '# Roadmap\n- [ ] pendiente fixture\n- [x] realizado fixture')
        self.confirm(self.cli('close', '--date', '2026-10-09', '--done', 'compatibilidad fixture',
                 '--tests', 'fixture tests', '--files', 'fixture.py', '--module', 'fixture'))
        sessions = list((self.root / 'docs/memory/sessions').glob('*.json'))
        self.assertEqual(len(sessions), 1)
        self.assertEqual(json.loads(sessions[0].read_text())['date'], '2026-10-09')
        self.assertIn('compatibilidad fixture', self.cli('recall', '--date', '2026-10-09'))
        self.assertIn('Sin sesiones', self.cli('recall', '--module', 'absent'))
        self.assertIn('compatibilidad fixture', self.cli('summary', '--date', '2026-10-09', '--export'))
        self.confirm(self.cli('add-decision', '--date', '2026-10-09', '--title', 'fixture decision',
                 '--reason', 'fixture reason', '--detail', 'fixture detail'))
        self.assertIn('fixture decision', self.cli('decisions'))
        self.assertIn('Trabajo verificado', self.cli('resume'))
        self.cli('handoff')
        self.cli('roadmap', '--mode', 'pending')
        self.cli('roadmap', '--mode', 'full')
        self.cli('roadmap', '--mode', 'handoff')
        self.cli('history')
        exports = list((self.root / 'docs/memory/exports').glob('*.txt')) + list((self.root / 'docs/memory/exports').glob('*.md'))
        self.assertEqual(len(exports), 5)
        self.assertTrue(any('fixture decision' in p.read_text(encoding='utf-8') for p in exports))
        self.assertEqual((self.root / 'PROJECT_STATE.md').read_text(), '# Estado\nTrabajo verificado')

    def test_alternative_document_names_and_authority(self):
        self.put('overview.md', 'Fuente oficial: [plan](guides/future.adoc)\n')
        self.put('guides/future.adoc', '= Hoja de ruta\nArquitectura\nEstado\nDecisiones\nPruebas')
        report = audit.audit_project(self.root)
        source = next(s for s in report['sources'] if s['path'] == 'guides/future.adoc')
        self.assertTrue(source['authority'].startswith('prioritaria'))
        for category in ('roadmap', 'arquitectura', 'estado', 'decisiones', 'pruebas'):
            self.assertEqual(report['categories'][category]['sources'], ['guides/future.adoc'])

    def test_audit_no_writes_api_and_cli(self):
        self.put('notes.md', '# Estado\nNo writes')
        before = snapshot(self.root)
        with patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir forbidden')):
            report = audit.audit_project(self.root)
        self.assertTrue(report['read_only'])
        self.cli('audit', '--json')
        self.assertEqual(before, snapshot(self.root))
        self.assertFalse((self.root / 'docs/memory').exists())
        self.assertFalse(list(SCRIPTS.glob('__pycache__')))

    def test_excluded_files_never_opened_and_no_content_output(self):
        forbidden = [self.put(name, 'SYNTHETIC_SECRET_SENTINEL') for name in (
            '.env', 'secrets.txt', 'credentials.md', 'node_modules/nested.md',
            'obj/data.md', 'private/facts.md', '.aws/info.md', 'database.db',
            'settings.json', 'app/config.yaml', 'customers.csv')]
        self.put('public.md', '# Estado\nSYNTHETIC_SECRET_SENTINEL\nhttps://user:pass@example.test\n')
        real_open = Path.open
        def guarded_open(path, *args, **kwargs):
            self.assertNotIn(path, forbidden)
            self.assertNotIn('w', args[0] if args else kwargs.get('mode', 'r'))
            return real_open(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open):
            report = audit.audit_project(self.root)
        output = json.dumps(report)
        self.assertNotIn('SYNTHETIC_SECRET_SENTINEL', output)
        self.assertNotIn('example.test', output)
        self.assertNotIn('credentials.md', output)
        self.assertTrue(report['partial'])

    def test_limits_large_binary_and_additional_exclusion(self):
        self.put('huge.md', 'x' * 129000)
        self.put('image.txt', '\x00binary')
        self.put('media/notes.md', 'roadmap')
        self.put('depth/next/doc.md', 'estado')
        report = audit.audit_project(self.root, max_bytes=50, max_depth=1, exclude=['media'])
        self.assertTrue(report['partial'])
        self.assertLessEqual(report['coverage']['bytes_read'], 50)
        self.assertFalse(report['sources'])
        report = audit.audit_project(self.root, max_files=1)
        self.assertLessEqual(report['coverage']['entries_seen'], 2)
        self.assertTrue(report['partial'])

    def test_unknown_repository(self):
        with patch.object(audit, 'git_read', return_value=None):
            with patch.object(Path, 'cwd', return_value=self.root):
                report = audit.audit_project()
        self.assertIn('desconocido', report['root_evidence'])
        self.assertEqual(report['categories']['roadmap']['status'], 'desconocido')
        self.assertEqual(report['tests']['results'], 'desconocido: no se ejecutaron pruebas')

    def test_technology_independent_inventory(self):
        for name in ('src/a.py', 'ui/a.ts', 'native/a.rs', 'other/a.unrecognized'):
            self.put(name, 'not inspected')
        self.put('package.json', '{"secret": "NOT_READ"}')
        report = audit.audit_project(self.root)
        self.assertEqual({t['name'] for t in report['technologies']}, {'Python', 'TypeScript', 'Rust'})
        self.assertIn('.unrecognized', report['extensions'])
        self.assertIn('package.json', report['manifests'])
        self.assertNotIn('NOT_READ', json.dumps(report))

    def test_inconsistencies_and_no_invention(self):
        self.put('notes.md', '# Estado\n- [ ] same task\n- [x] same task\n[broken](missing.md)')
        self.put('other.md', '- [ ] same task')
        report = audit.audit_project(self.root)
        kinds = [f['kind'] for f in report['findings']]
        self.assertTrue(any('referencia local' in k for k in kinds))
        self.assertTrue(any('marcas opuestas' in k for k in kinds))
        self.assertTrue(any('histórico' in k for k in kinds))
        self.assertNotIn('same task', json.dumps(report))

    @unittest.skipUnless(shutil.which('git'), 'Git unavailable')
    def test_git_root_history_no_subjects_and_no_writes(self):
        def git(*args):
            result = subprocess.run(['git', *args], cwd=self.root, capture_output=True,
                                    encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        git('init', '-q')
        self.put('notes.md', '# Estado')
        git('add', 'notes.md')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'SYNTHETIC_SECRET_COMMIT')
        (self.root / 'nested').mkdir()
        before = snapshot(self.root)
        text = self.cli('audit', '--json', project=False, cwd=self.root / 'nested')
        report = json.loads(text)
        self.assertEqual(report['root_evidence'], 'confirmado: Git')
        self.assertEqual(len(report['git']['history']), 1)
        self.assertNotIn('SYNTHETIC_SECRET_COMMIT', text)
        self.assertNotIn('fixture@example.test', text)
        self.assertEqual(before, snapshot(self.root))

    def test_git_unavailable_and_timeout(self):
        with patch.object(audit.shutil, 'which', return_value=None):
            self.assertIsNone(audit.git_read(self.root, 'log'))
        with patch.object(audit.shutil, 'which', return_value='git'):
            with patch.object(audit.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 5)):
                self.assertIsNone(audit.git_read(self.root, 'log'))

    def test_invalid_limits_and_safe_labels(self):
        with self.assertRaises(ValueError):
            audit.audit_project(self.root, max_files=0)
        for name in ('secret.md', 'abc@example.test', '1' * 40, 'path\nvalue'):
            self.assertEqual(audit.safe_label(name), '[ruta omitida por seguridad]')
        self.assertEqual(audit.safe_label('ROADMAP_NEOPRARSPOSVENTA_V5_2026-10-07.txt'),
                         'ROADMAP_NEOPRARSPOSVENTA_V5_2026-10-07.txt')

    def test_symlink_guard_without_host_privileges(self):
        from unittest.mock import MagicMock
        entry = MagicMock()
        entry.path = str(self.root / 'public.md')
        entry.name = 'public.md'
        entry.is_symlink.return_value = True
        scan = MagicMock()
        scan.__enter__.return_value = iter([entry])
        with patch.object(audit.os, 'scandir', return_value=scan):
            with patch.object(Path, 'open', side_effect=AssertionError('link opened')):
                report = audit.audit_project(self.root)
        self.assertEqual(report['coverage']['documents_read'], 0)
        self.assertTrue(any('enlaces' in reason for reason in report['limitations']))

    def test_symlink_excluded(self):
        target = self.put('private/hidden.md', 'SYNTHETIC_LINK_SECRET')
        try:
            (self.root / 'public.md').symlink_to(target)
        except OSError:
            self.skipTest('Symlinks not permitted on this host')
        report = audit.audit_project(self.root)
        self.assertFalse(report['sources'])
        self.assertTrue(report['partial'])


if __name__ == '__main__':
    unittest.main()
