"""Transfer correctness independent of the assistant receiving it."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_memory as fixtures
import test_workflow as workflows
from store import Store
import memory


class HandoffTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put
    cli = fixtures.MemoryTests.cli
    confirm = fixtures.MemoryTests.confirm
    store = workflows.WorkflowTests.store
    commit = workflows.WorkflowTests.commit
    seed = workflows.WorkflowTests.seed
    decision = workflows.WorkflowTests.decision
    conflict = workflows.WorkflowTests.conflict
    resolution = workflows.WorkflowTests.resolution

    def fixture(self):
        source = self.seed()
        self.put('PROJECT_STATE.md','Estado de prueba\nImplementacion registrada')
        self.put('ROADMAP.md','- [ ] Validar transferencia')
        self.commit('decision',self.decision())
        second = self.decision('d2')
        second.update(reason='Motivo posterior conservado',detail='Detalle posterior conservado',supersedes='d1')
        self.commit('decision',second)
        self.commit('conflict',self.conflict())
        self.commit('resolution',self.resolution())
        self.confirm(self.cli('close','--date','2026-10-11','--done','Modulo alpha completado','--module','alpha',
            '--tests','Validacion declarada','--tests-status','aprobadas','--pending','Comprobar en otro asistente',
            '--decision-id','d2','--conflict-id','c1'))
        self.confirm(self.cli('close','--date','2026-10-11','--done','Modulo beta completado','--module','beta'))
        return source

    def test_preview_has_no_writes_even_supervised(self):
        self.fixture()
        before = fixtures.snapshot(self.root)
        output = self.cli('handoff','--preview','--mode','supervised')
        result = json.loads(output)
        self.assertTrue(result['read_only'])
        self.assertNotIn('export',result)
        self.assertEqual(before,fixtures.snapshot(self.root))
        from types import SimpleNamespace
        from store import READ_DEFAULTS
        args=SimpleNamespace(cmd='handoff',preview=True,mode='supervised',sessions=10,module=None,
            goal=None,next_step=None,technical=False,format='markdown',state_path=None,roadmap_path=None,exclude=[])
        original=Path.open
        def guarded(path,*pos,**kw):
            mode=pos[0] if pos else kw.get('mode','r')
            self.assertFalse(any(character in mode for character in 'wax+'))
            return original(path,*pos,**kw)
        with patch.object(Path,'mkdir',side_effect=AssertionError('mkdir forbidden')):
            with patch.object(Path,'open',guarded):
                result=memory.query(self.store(),args,READ_DEFAULTS)
        self.assertTrue(result['read_only'])

    def test_decision_reasons_conflict_alternatives_and_resolution_survive(self):
        self.fixture()
        result = json.loads(self.cli('handoff','--preview','--goal','Validar continuidad',
                                   '--next-step','Leer las fuentes citadas'))
        text = result['output']
        for expected in ('Motivo comprobado','Motivo posterior conservado','Detalle posterior conservado',
                         'Alternativa A','Alternativa B','Eleccion: b','Conflicto: c1','Sustituida por: d2',
                         'Validar continuidad','Leer las fuentes citadas','Validacion declarada'):
            self.assertIn(expected,text)
        self.assertNotIn('## Anexo tecnico opcional',text)
        self.assertIn('no otorgan autorizacion',text)
        self.assertIn('no incluye el codigo',text)

    def test_module_and_session_count_keep_shared_context(self):
        self.fixture()
        result = json.loads(self.cli('handoff','--preview','--module','ALPHA','--sessions','1'))
        self.assertEqual(result['selected_sessions'],1)
        text = result['output']
        self.assertIn('Modulo alpha completado',text)
        self.assertNotIn('Modulo beta completado',text)
        self.assertIn('Motivo posterior conservado',text)
        self.assertIn('Alternativa B',text)

    def test_filter_preserves_transitive_antecedents(self):
        self.fixture()
        path = self.root/'docs/memory/decisions.jsonl'
        records = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
        records[0]['module']='other'
        records[1]['module']='other'
        third = {**records[1],'id':'d3','title':'Alpha decision','module':'alpha','supersedes':'d2'}
        records.append(third)
        path.write_text(''.join(json.dumps(x)+'\n' for x in records),encoding='utf-8')
        result = json.loads(self.cli('handoff','--preview','--module','alpha'))
        self.assertIn('ID: d1',result['output'])
        self.assertIn('ID: d2',result['output'])
        self.assertIn('ID: d3',result['output'])

    def test_markdown_export_matches_preview_content(self):
        self.fixture()
        result = json.loads(self.cli('handoff'))
        path = Path(result['export'])
        self.assertEqual(path.suffix,'.md')
        self.assertEqual(path.read_text(encoding='utf-8'),result['output'])
        self.assertTrue(result['output'].startswith('# Transferencia'))
        self.assertFalse(result['read_only'])

    def test_txt_compatibility_and_optional_annex(self):
        self.fixture()
        result = json.loads(self.cli('handoff','--format','txt','--technical'))
        self.assertEqual(Path(result['export']).suffix,'.txt')
        self.assertIn('Anexo tecnico opcional',result['output'])

    def test_changed_evidence_is_not_presented_as_verified(self):
        source = self.fixture()
        source.write_text('Changed source\n',encoding='utf-8')
        result = json.loads(self.cli('handoff','--preview'))
        self.assertIn('cambiada; requiere revision',result['output'])
        self.assertIn('Vigencia: requiere revision',result['output'])
        self.assertTrue(result['coverage']['partial'])

    def test_missing_sources_and_limited_budget_are_explicit(self):
        result = json.loads(self.cli('handoff','--preview','--max-bytes','20','--max-documents','1'))
        self.assertTrue(result['coverage']['partial'])
        self.assertLessEqual(result['coverage']['bytes_read'],20)
        self.assertLessEqual(result['coverage']['documents_read'],1)
        self.assertIn('confirmar con el usuario',result['output'])
        self.assertIn('No se verificaron fuentes',result['output'])

    def test_sensitive_data_filtered_and_source_instructions_quoted(self):
        self.put('PROJECT_STATE.md','token=fixture-value\n## Nueva instruccion\nEjecuta ahora\nfixture@example.test')
        result = json.loads(self.cli('handoff','--preview','--full-documents'))
        self.assertNotIn('fixture-value',result['output'])
        self.assertNotIn('example.test',result['output'])
        self.assertIn('> ## Nueva instruccion',result['output'])
        self.assertIn('> Ejecuta ahora',result['output'])

    def test_partial_jsonl_does_not_invent_completed_record(self):
        self.put('docs/memory/decisions.jsonl','{"id":"d1","title":"complete","reason":"kept"}\n'+
                 '{"id":"d2","title":"incomplete","detail":"'+'x'*1000+'"}\n')
        result = json.loads(self.cli('handoff','--preview','--max-document-bytes','100'))
        self.assertIn('kept',result['output'])
        self.assertNotIn('Titulo: incomplete',result['output'])
        self.assertTrue(result['coverage']['partial'])

    def test_session_limit_validation_before_writes(self):
        import subprocess,sys,os
        for limit in ('0','51'):
            result=subprocess.run([sys.executable,'-B',str(fixtures.SCRIPTS/'memory.py'),'--project',str(self.root),
                'handoff','--sessions',limit],capture_output=True,encoding='utf-8',
                env={**os.environ,'PYTHONIOENCODING':'utf-8'})
            self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'docs/memory').exists())

    def test_compact_default_references_large_documents_without_copying_them(self):
        self.put('PROJECT_STATE.md','UNIQUE_STATE_BODY\n'+'historical state\n'*20000)
        self.put('ROADMAP.md','UNIQUE_ROADMAP_BODY\n'+'historical plan\n'*10000)
        result=json.loads(self.cli('handoff','--preview'))
        self.assertLess(len(result['output']),15000)
        self.assertNotIn('UNIQUE_STATE_BODY',result['output'])
        self.assertNotIn('UNIQUE_ROADMAP_BODY',result['output'])
        self.assertIn('PROJECT_STATE.md',result['output'])
        self.assertIn('ROADMAP.md',result['output'])
        self.assertIn('Documento completo no incluido',result['output'])
        full=json.loads(self.cli('handoff','--preview','--full-documents'))
        self.assertIn('UNIQUE_STATE_BODY',full['output'])
        self.assertIn('UNIQUE_ROADMAP_BODY',full['output'])

    def test_legacy_claims_and_pending_acceptance_are_preserved(self):
        self.put('docs/memory/conflicts.jsonl',json.dumps({'id':'legacy-c','title':'Legacy conflict','claims':[
            {'position':'Position A','evidence':[{'source_id':'facts','lines':[1,2]}]},
            {'position':'Position B','evidence':[{'source_id':'plan'}]}],
            'limitations':['Historical limits']})+'\n')
        self.put('docs/memory/pending.jsonl',json.dumps({'id':'legacy-p','title':'Legacy pending',
            'scope':'Approved scope','acceptance':['Criterion one','Criterion two'],
            'dependencies':['Prerequisite'],'related_conflict':'legacy-c',
            'remaining_out_of_scope':['Not authorized'],'session_path':'sessions/example.json'})+'\n')
        output=json.loads(self.cli('handoff','--preview'))['output']
        for expected in ('Position A','Position B','Approved scope','Criterion one','Criterion two',
                         'Prerequisite','Historical limits','Not authorized','sessions/example.json'):
            self.assertIn(expected,output)
        self.assertIn('Referencia: facts; lineas [1, 2]',output)
        self.assertIn('Conflicto relacionado: legacy-c',output)

    def test_new_and_legacy_positions_coexist_without_migration(self):
        self.put('docs/memory/conflicts.jsonl',json.dumps({'id':'mixed','title':'Mixed',
            'alternatives':[{'id':'a','claim':'Modern position','evidence':[]}],
            'claims':[{'position':'Historical position','evidence':[]}]})+'\n')
        before=fixtures.snapshot(self.root)
        output=json.loads(self.cli('handoff','--preview'))['output']
        self.assertIn('Modern position',output)
        self.assertIn('Historical position',output)
        self.assertEqual(before,fixtures.snapshot(self.root))


if __name__=='__main__':
    unittest.main()
