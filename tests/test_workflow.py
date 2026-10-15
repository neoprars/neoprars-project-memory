"""Observable approval, freshness, preservation and path invariants."""
import copy
import hashlib
import json
import os
import stat
import subprocess
import unittest
from unittest.mock import patch

import test_memory as fixtures
import audit
import start
from safety import safe_path, is_link
from store import Store
from workflow import prepare, apply, digest, writer_lock


class WorkflowTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put
    cli = fixtures.MemoryTests.cli
    confirm = fixtures.MemoryTests.confirm

    def store(self, **kwargs):
        return Store(self.root, **kwargs)

    def commit(self, kind, data):
        proposal = prepare(self.store(),kind,data)
        apply(self.store(),proposal,proposal['approval_sha256'],'fixture-user','supervised')
        return proposal

    def seed(self):
        self.commit('initialize',{})
        source=self.put('notes/facts.md','Estado\nfixture evidence\n')
        self.commit('source',{'id':'facts','path':'notes/facts.md','sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                             'authority':'confirmada','reason':'Fuente revisada'})
        return source

    def decision(self, ident='d1', **kwargs):
        return dict(schema_version=2,id=ident,date='2026-10-11',created_at='2026-10-11T12:00:00-03:00',
            classification='confirmado',status='validado',evidence=[{'source_id':'facts','lines':[1,2]}],
            title='Fixture decision',reason='Motivo comprobado',detail='Detalle',supersedes=None,**kwargs)

    def conflict(self):
        data=self.decision('c1')
        for key in ('reason','detail','supersedes'):
            del data[key]
        data.update(title='Fixture conflict',conflict_type='autoridad',resolution=None,
            alternatives=[{'id':'a','claim':'Alternativa A','evidence':[{'source_id':'facts'}]},
                          {'id':'b','claim':'Alternativa B','evidence':[{'source_id':'facts'}]}])
        return data

    def resolution(self):
        data=self.decision('r1')
        for key in ('detail','supersedes'):
            del data[key]
        data.update(resolves='c1',choice='b',resolution='supervisada')
        return data

    def test_proposals_never_write_even_supervised(self):
        before=fixtures.snapshot(self.root)
        proposal=json.loads(self.cli('initialize','--mode','supervised'))
        self.assertTrue(proposal['read_only'])
        self.assertEqual(before,fixtures.snapshot(self.root))
        close=json.loads(self.cli('close','--date','2026-10-11','--done','Fixture'))
        self.assertEqual(close['proposal']['data']['tests_status'],'desconocidas')
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_approval_required_and_content_bound(self):
        proposal=prepare(self.store(),'initialize',{})
        before=fixtures.snapshot(self.root)
        for mode,actor,code in [('automatic','fixture-user',proposal['approval_sha256']),
                                ('supervised','',proposal['approval_sha256']),('supervised','fixture-user','0'*64)]:
            with self.assertRaises(ValueError):
                apply(self.store(),proposal,code,actor,mode)
        altered=copy.deepcopy(proposal)
        altered['proposal']['memory_root']='notes/memory'
        with self.assertRaises(ValueError):
            apply(self.store(),altered,proposal['approval_sha256'],'fixture-user','supervised')
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_fast_close_records_locally_and_skips_equivalent_session(self):
        self.seed()
        self.commit('decision', self.decision())
        options = ('--date','2026-10-15','--done','Avance comprobado',
                   '--pending','Pendiente real','--tests','Resultado previo vigente',
                   '--tests-status','aprobadas','--decision-id','d1',
                   '--evidence','{"source_id":"facts"}')
        before = fixtures.snapshot(self.root)
        envelope = json.loads(self.cli('close-fast', *options))
        self.assertEqual(before, fixtures.snapshot(self.root))
        self.assertFalse(envelope['tests_executed'])
        self.assertFalse(envelope['duplicate'])
        self.assertEqual(envelope['proposal']['kind'], 'close')
        self.assertEqual(envelope['proposal']['data']['decisions'], ['d1'])
        self.confirm(json.dumps(envelope))
        before = fixtures.snapshot(self.root)
        duplicate = json.loads(self.cli('close-fast', *options))
        self.assertTrue(duplicate['duplicate'])
        self.assertNotIn('proposal', duplicate)
        self.assertEqual(before, fixtures.snapshot(self.root))
        changed = json.loads(self.cli('close-fast', *options, '--pending','Otro pendiente'))
        self.assertFalse(changed['duplicate'])

    def test_fast_close_git_is_read_only_and_optional(self):
        import memory
        from types import SimpleNamespace
        args = SimpleNamespace(id=None, date='2026-10-15', classification='confirmado',
                               status='pendiente', evidence=[])
        data = __import__('workflow').event_base(args)
        data.update(module='general', done='Fixture', pending='', tests='', files='',
                    tests_status='desconocidas', decisions=[], conflicts=[])
        before = fixtures.snapshot(self.root)
        with patch('memory.git_read', side_effect=['a'*40, '', '', None]) as git:
            result = memory.prepare_fast_close(self.store(), data)
        self.assertEqual([call.args[1:] for call in git.call_args_list], [
            ('rev-parse','--verify','HEAD'),
            ('status','--short','--branch','--untracked-files=normal'),
            ('diff','--check'), ('diff','--cached','--check')])
        self.assertTrue(result['git']['status']['verified'])
        self.assertFalse(result['git']['cached_diff_check']['verified'])
        with patch('memory.git_read', return_value=None):
            result = memory.prepare_fast_close(self.store(), data)
        self.assertFalse(result['git']['head']['verified'])
        self.assertEqual(before, fixtures.snapshot(self.root))

    def test_fast_close_rejects_partial_duplicate_scan(self):
        self.put('docs/memory/sessions/broken.json', '{invalid')
        import memory
        data = dict(schema_version=2,id='fast',date='2026-10-15',
            created_at='2026-10-15T12:00:00-03:00',classification='confirmado',
            status='pendiente',evidence=[],module='general',done='Fixture',
            pending='',tests='',files='',tests_status='desconocidas',decisions=[],conflicts=[])
        with self.assertRaises(ValueError):
            memory.prepare_fast_close(self.store(), data)

    def fast_data(self, ident=None, **changes):
        import workflow
        from types import SimpleNamespace
        data = workflow.event_base(SimpleNamespace(id=ident, date='2026-10-15', classification='confirmado',
                                                   status='pendiente', evidence=[]))
        data.update(module='general', done='Fixture', pending='', tests='', files='',
                    tests_status='desconocidas', decisions=[], conflicts=[])
        data.update(changes)
        return data

    def test_fast_close_git_summary_is_bounded_and_detail_is_optional(self):
        import memory
        NL = chr(10)
        status = NL.join(['## main...origin/main [ahead 1]'] + [' M src/f%02d.cs' % i for i in range(30)]
                         + ['?? nuevo.txt', 'M  indexado.cs'])
        data = self.fast_data()
        with patch('memory.git_read', side_effect=['a'*40, status, '', '']):
            summary = memory.prepare_fast_close(self.store(), data)['git']['status']
        self.assertTrue(summary['verified'])
        self.assertEqual('main...origin/main [ahead 1]', summary['branch'])
        self.assertEqual((32, 31, 1, 1), (summary['total'], summary['tracked_changes'], summary['untracked'], summary['staged']))
        self.assertEqual(20, len(summary['paths']))
        self.assertTrue(summary['truncated'])
        self.assertNotIn('output', summary)
        with patch('memory.git_read', side_effect=['a'*40, status, '', '']):
            detailed = memory.prepare_fast_close(self.store(), data, detail=True)['git']['status']
        self.assertEqual(20, len(detailed['paths']))
        self.assertEqual(status, detailed['output'])
        short = NL.join(['## main', ' M uno.cs'])
        with patch('memory.git_read', side_effect=['a'*40, short, '', '']):
            small = memory.prepare_fast_close(self.store(), data)['git']['status']
        self.assertFalse(small['truncated']); self.assertEqual([' M uno.cs'], small['paths'])

    def test_fast_close_cli_accepts_git_detail_without_writing(self):
        self.seed()
        before = fixtures.snapshot(self.root)
        envelope = json.loads(self.cli('close-fast','--date','2026-10-15','--done','Avance','--git-detail'))
        self.assertEqual(before, fixtures.snapshot(self.root))
        self.assertIn('git', envelope); self.assertFalse(envelope['tests_executed'])

    def test_fast_close_diff_check_reports_real_findings_and_git_errors(self):
        import memory
        NL = chr(10)
        data = self.fast_data()
        findings = NL.join(['src/a.cs:3: trailing whitespace.', '+var x = 1; '])
        with patch('memory.git_read', side_effect=['a'*40, '', findings, '']) as git:
            git_info = memory.prepare_fast_close(self.store(), data)['git']
        worktree, cached = git_info['diff_check'], git_info['cached_diff_check']
        self.assertTrue(worktree['verified']); self.assertFalse(worktree['clean'])
        self.assertEqual(findings.splitlines(), worktree['findings'])
        self.assertTrue(cached['verified']); self.assertTrue(cached['clean']); self.assertEqual([], cached['findings'])
        # Solo diff --check acepta el codigo de salida 2; los demas comandos no.
        accepted = {call.args[1:][0]: call.kwargs.get('accept') for call in git.call_args_list}
        self.assertEqual((0, 2), accepted['diff']); self.assertEqual((0,), accepted['status'])
        with patch('memory.git_read', return_value=None):
            failed = memory.prepare_fast_close(self.store(), data)['git']
        self.assertTrue(all(item == {'verified': False} for item in failed.values()))

    def test_git_read_accept_only_listed_exit_codes(self):
        from types import SimpleNamespace
        def run(code):
            return patch('audit.subprocess.run', return_value=SimpleNamespace(returncode=code, stdout='x.cs:1: trailing whitespace.' + chr(10)))
        with run(2):
            self.assertIsNone(audit.git_read(self.root, 'diff', '--check'))
            self.assertEqual('x.cs:1: trailing whitespace.', audit.git_read(self.root, 'diff', '--check', accept=(0, 2)))
        with run(128):
            self.assertIsNone(audit.git_read(self.root, 'diff', '--check', accept=(0, 2)))

    def test_fast_close_same_id_is_idempotent_or_rejected(self):
        import memory
        self.seed()
        self.commit('close', self.fast_data('cierre-x'))
        before = fixtures.snapshot(self.root)
        with patch('memory.git_read', return_value=None):
            same = memory.prepare_fast_close(self.store(), self.fast_data('cierre-x'))
            other_id = memory.prepare_fast_close(self.store(), self.fast_data('cierre-y'))
            with self.assertRaises(ValueError):
                memory.prepare_fast_close(self.store(), self.fast_data('cierre-x', pending='Otro pendiente'))
        for result in (same, other_id):
            self.assertTrue(result['duplicate']); self.assertEqual('cierre-x', result['existing_id'])
            self.assertNotIn('proposal', result); self.assertFalse(result['tests_executed'])
        self.assertEqual(before, fixtures.snapshot(self.root))

    def test_project_and_location_bound(self):
        proposal=prepare(self.store(),'initialize',{})
        proposal['proposal']['project_binding']='0'*64
        with self.assertRaises(ValueError):
            apply(self.store(),proposal,digest(proposal['proposal']),'fixture-user','supervised')

    def test_initialize_does_not_overwrite(self):
        self.seed()
        before=fixtures.snapshot(self.root)
        with self.assertRaises(ValueError):
            prepare(self.store(),'initialize',{})
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_changed_source_rejects_approved_proposal(self):
        source=self.seed()
        proposal=prepare(self.store(),'decision',self.decision())
        source.write_text('changed\n',encoding='utf-8')
        before=fixtures.snapshot(self.root)
        with self.assertRaises(ValueError):
            apply(self.store(),proposal,proposal['approval_sha256'],'fixture-user','supervised')
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_changed_memory_rejects_proposal(self):
        self.seed()
        proposal=prepare(self.store(),'decision',self.decision())
        self.commit('decision',self.decision('other'))
        before=fixtures.snapshot(self.root)
        with self.assertRaises(ValueError):
            apply(self.store(),proposal,proposal['approval_sha256'],'fixture-user','supervised')
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_supersession_preserves_original_bytes(self):
        self.seed()
        first=self.commit('decision',self.decision())
        path=self.root/'docs/memory/decisions.jsonl'
        original=path.read_bytes()
        second=self.decision('d2')
        second['supersedes']='d1'
        self.commit('decision',second)
        self.assertTrue(path.read_bytes().startswith(original))
        records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[1]['supersedes'],'d1')
        self.assertEqual(records[0]['approval']['proposal_sha256'],first['approval_sha256'])
        report=start.start_project(self.root)
        self.assertEqual(report['decisions'][0]['superseded_by'],['d2'])
        self.assertEqual(report['decisions'][0]['declared_status'],'validado')

    def test_source_revision_does_not_refresh_old_decision(self):
        source=self.seed()
        self.commit('decision',self.decision())
        source.write_text('changed\n',encoding='utf-8')
        self.commit('source',{'id':'facts','path':'notes/facts.md','sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                             'authority':'confirmada','reason':'Nueva revision'})
        report=start.start_project(self.root)
        self.assertEqual(report['sources'][0]['status'],'verificada por huella')
        self.assertTrue(report['decisions'][0]['review_required'])
        self.assertEqual(report['decisions'][0]['declared_status'],'validado')
        self.assertIn('version de fuente distinta',report['decisions'][0]['evidence_checks'][0]['status'])
        registry=json.loads((self.root/'docs/memory/sources.json').read_text(encoding='utf-8'))
        self.assertEqual(len(registry['revisions']),2)

    def test_changed_missing_excluded_duplicate_unknown_sources_require_review(self):
        source=self.seed()
        self.commit('decision',self.decision())
        path=self.root/'docs/memory/sources.json'
        registry=json.loads(path.read_text(encoding='utf-8'))
        for transform in (lambda x:x['sources'][0].update(path='absent.md'),
                          lambda x:x['sources'][0].update(authority='excluida'),
                          lambda x:x['sources'].append(copy.deepcopy(x['sources'][0])),
                          lambda x:x.update(schema_version=99),lambda x:x.update(sources=[])):
            value=copy.deepcopy(registry)
            transform(value)
            path.write_text(json.dumps(value),encoding='utf-8')
            report=start.start_project(self.root)
            self.assertTrue(report['decisions'][0]['review_required'])
        path.write_text(json.dumps(registry),encoding='utf-8')
        source.write_text('changed\n',encoding='utf-8')
        self.assertTrue(start.start_project(self.root)['decisions'][0]['review_required'])

    def test_supervised_resolution_preserves_alternatives(self):
        self.seed()
        self.commit('conflict',self.conflict())
        path=self.root/'docs/memory/conflicts.jsonl'
        original=path.read_bytes()
        self.commit('resolution',self.resolution())
        self.assertTrue(path.read_bytes().startswith(original))
        records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[1]['choice'],'b')
        self.assertEqual(records[1]['resolves'],'c1')
        self.assertEqual(len(records[0]['alternatives']),2)
        report=start.start_project(self.root)
        self.assertEqual(report['conflicts'][0]['resolved_by'],['r1'])
        with self.assertRaises(ValueError):
            prepare(self.store(),'resolution',self.resolution())

    def test_invalid_resolution_and_empty_evidence_rejected(self):
        self.seed()
        self.commit('conflict',self.conflict())
        for changes in ({'choice':'unknown'},{'reason':''},{'evidence':[]},{'resolves':'absent'}):
            data=self.resolution()
            data.update(changes)
            with self.assertRaises(ValueError):
                prepare(self.store(),'resolution',data)

    def test_close_structured_tests_links_and_exact_approval(self):
        self.seed()
        self.commit('decision',self.decision())
        result=self.cli('close','--date','2026-10-11','--done','Fixture','--tests','Executed fixture',
            '--tests-status','aprobadas','--decision-id','d1','--evidence','{"source_id":"facts"}')
        proposal=json.loads(result)
        self.confirm(result)
        path=next((self.root/'docs/memory/sessions').glob('*.json'))
        saved=json.loads(path.read_text(encoding='utf-8'))
        without_approval={key:value for key,value in saved.items() if key!='approval'}
        self.assertEqual(without_approval,proposal['proposal']['data'])
        self.assertEqual(saved['decisions'],['d1'])
        self.assertEqual(saved['tests_status'],'aprobadas')

    def test_partial_utf8_never_discards_invalid_interior(self):
        path=self.put('notes.md','unused')
        path.write_bytes(b'{"title":"a\xffb"}\n'+b'x'*100)
        budget=start.Budget(30,1,30,7, __import__('time').monotonic()+10)
        value,coverage=budget.read(path)
        self.assertIsNone(value)
        self.assertEqual(coverage['status'],'omitida')
        path.write_bytes(b'line\n\xc3\xa1more')
        budget=start.Budget(6,1,6,7,__import__('time').monotonic()+10)
        value,coverage=budget.read(path)
        self.assertEqual(value,'line\n')
        self.assertEqual(coverage['status'],'parcial')

    def test_corrupt_sessions_reported_and_bounded(self):
        self.put('docs/memory/sessions/a.json','[]')
        self.put('docs/memory/sessions/b.json','{invalid')
        self.put('docs/memory/sessions/c.json','{"done":"'+('x'*1000)+'"}')
        store=self.store(max_document_bytes=50)
        self.assertEqual(store.sessions(),[])
        self.assertTrue(store.coverage()['partial'])
        self.assertLessEqual(store.budget.spent,150)

    def test_all_commands_use_custom_location(self):
        # confirm helper selects default location; use explicit apply here.
        for command in [('initialize',),('close','--date','2026-10-11','--done','Custom session')]:
            envelope=json.loads(self.cli(*command,'--memory-root','notes/memory'))
            self.put('work/proposal.json',json.dumps(envelope))
            self.cli('apply','--memory-root','notes/memory','--proposal','work/proposal.json','--mode','supervised',
                     '--approval-sha256',envelope['approval_sha256'],'--approved-by','fixture-user')
        self.put('notes/state.md','Custom state')
        self.put('notes/plan.md','- [ ] custom pending')
        for command in ('recall','summary','resume','handoff'):
            options=['--state-path','notes/state.md'] if command in ('resume','handoff') else []
            if command=='handoff':
                options+=['--roadmap-path','notes/plan.md']
            result=json.loads(self.cli(command,'--memory-root','notes/memory',*options))
            self.assertIn('Custom session',result['output'])
        self.assertFalse((self.root/'docs/memory').exists())

    def test_no_git_ecc_graphify_required(self):
        with patch.object(audit.shutil,'which',return_value=None):
            self.seed()
            self.commit('decision',self.decision())
            report=start.start_project(self.root)
        self.assertFalse(report['decisions'][0]['review_required'])

    def test_reparse_attribute_guard_without_new_path_api(self):
        source=self.put('notes/facts.md','Fixture')
        real=type(source).lstat
        def reparse(path):
            if path==self.root/'notes':
                return type('Info',(),{'st_file_attributes':stat.FILE_ATTRIBUTE_REPARSE_POINT})()
            return real(path)
        with patch.object(type(source),'lstat',reparse):
            self.assertTrue(is_link(self.root/'notes'))
            self.assertIsNone(safe_path(self.root,'notes/facts.md'))
            with self.assertRaises(ValueError):
                Store(self.root,'notes/memory')

    @unittest.skipUnless(os.name=='nt','Windows junction test')
    def test_real_junction_blocks_read_and_write(self):
        target=self.root/'target'
        target.mkdir()
        link=self.root/'linked'
        result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],capture_output=True)
        if result.returncode:
            self.skipTest('Junction creation unavailable')
        try:
            self.assertIsNone(safe_path(self.root,'linked/file.json'))
            with self.assertRaises(ValueError):
                Store(self.root,'linked/memory')
            report=audit.audit_project(self.root)
            self.assertTrue(any('junction' in reason for reason in report['limitations']))
        finally:
            os.rmdir(link)  # Removes this test junction only, never its target.

    def test_export_scrubs_recognizable_sensitive_values(self):
        self.put('PROJECT_STATE.md','token=fixture-value\nmail fixture@example.test\nhttps://example.test')
        self.put('ROADMAP.md','- [ ] fixture')
        output=json.loads(self.cli('handoff'))
        content=__import__('pathlib').Path(output['export']).read_text(encoding='utf-8')
        self.assertNotIn('fixture-value',content)
        self.assertNotIn('example.test',content)

    def test_writer_lock_rejects_second_writer_without_changing_memory(self):
        self.seed()
        proposal=prepare(self.store(),'decision',self.decision())
        store=self.store()
        with writer_lock(store):
            before=fixtures.snapshot(self.root)
            with self.assertRaises(ValueError):
                apply(self.store(),proposal,proposal['approval_sha256'],'fixture-user','supervised')
            self.assertEqual(before,fixtures.snapshot(self.root))
        self.assertFalse(list(self.root.glob('.neoprars-memory-*.lock')))

    def test_initialization_content_is_reviewable_and_bound(self):
        proposal=prepare(self.store(),'initialize',{})
        self.assertIn('INDEX.md',proposal['proposal']['data']['files'])
        altered=copy.deepcopy(proposal)
        altered['proposal']['data']['files']['INDEX.md']='invented'
        before=fixtures.snapshot(self.root)
        with self.assertRaises(ValueError):
            apply(self.store(),altered,proposal['approval_sha256'],'fixture-user','supervised')
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_invalid_event_id_and_evidence_budget_do_not_certify(self):
        self.seed()
        data=self.decision()
        data['id']=['invalid']
        self.put('docs/memory/decisions.jsonl',json.dumps(data)+'\n')
        report=start.start_project(self.root)
        self.assertTrue(report['decisions'][0]['review_required'])
        data=self.decision()
        data['evidence']=[{'source_id':'facts'}]*100
        self.put('docs/memory/decisions.jsonl',json.dumps(data)+'\n')
        report=start.start_project(self.root,max_files=15)
        self.assertTrue(report['partial'])
        self.assertLessEqual(report['coverage']['entries_checked'],15)

    def test_history_suppresses_commit_subject_and_limits_time(self):
        self.put('ROADMAP.md','Fixture')
        with patch('memory.git_read',return_value='a'*40+' 2026-10-11') as git:
            import memory
            from types import SimpleNamespace
            result=memory.query(self.store(),SimpleNamespace(cmd='history',roadmap_path=None),{})
        args=git.call_args.args
        self.assertIn('--format=%H %cs',args)
        self.assertIn('-20',args)
        self.assertLessEqual(git.call_args.kwargs['timeout'],5)
        self.assertNotIn('subject',result['output'])


if __name__=='__main__':
    unittest.main()
