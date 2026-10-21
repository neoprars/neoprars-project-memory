"""Directed tests for provider-neutral global and project-specific rules."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_memory as fixtures


class ProjectRulesTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put

    def cli(self, *args):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'memory.py'
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script),
                                 '--project', str(self.root), *args], cwd=self.root,
                                capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def apply(self, proposal):
        self.put('work/proposal.json', json.dumps(proposal, ensure_ascii=False))
        return self.cli('apply', '--proposal', 'work/proposal.json', '--mode', 'supervised',
                        '--approval-sha256', proposal['approval_sha256'], '--approved-by', 'fixture-user')

    def test_defaults_are_listed_and_project_rule_round_trips(self):
        listed = self.cli('rules', 'list', '--json')
        ids = {rule['id'] for rule in listed['rules']}
        self.assertTrue({'protect-main', 'minimal-changes', 'database-changes',
                         'data-protection', 'decision-log'} <= ids)

        proposal = self.cli('rules', 'add', '--id', 'fixture-scope', '--title', 'Alcance fixture',
                            '--text', 'No extender el cambio fixture.', '--json')
        self.assertTrue(proposal['read_only'])
        self.apply(proposal)

        start = self.cli('start', '--json')
        self.assertIn('fixture-scope', {rule['id'] for rule in start['project_rules']['rules']})
        resume = self.cli('resume', '--json')
        self.assertIn('project_rules', resume['consolidated_context'])

    def test_deactivate_delete_and_restore_default_are_persisted(self):
        proposal = self.cli('rules', 'deactivate', '--id', 'protect-main', '--json')
        self.apply(proposal)
        disabled = next(rule for rule in self.cli('rules', 'list', '--json')['rules']
                        if rule['id'] == 'protect-main')
        self.assertFalse(disabled['enabled'])

        proposal = self.cli('rules', 'restore', '--id', 'protect-main', '--json')
        self.apply(proposal)
        self.assertIn('protect-main', {rule['id'] for rule in self.cli('rules', 'list', '--json')['rules']})

    def test_preflight_checks_rules_and_requests_authorization_on_conflict(self):
        ready = self.cli('preflight', '--task', 'Implementar una función pequeña con pruebas dirigidas', '--json')
        self.assertEqual(ready['status'], 'ready')
        self.assertTrue(ready['read_only'])
        self.assertTrue(ready['rules']['rules'])

        blocked = self.cli('check-task', '--task', 'Hacer commit y push de los cambios', '--json')
        self.assertEqual(blocked['status'], 'authorization_required')
        self.assertTrue(blocked['authorization_question'])
        self.assertTrue(any(item.get('rule_id') == 'git-operations'
                            for item in blocked['conflicts']))


if __name__ == '__main__':
    unittest.main()
