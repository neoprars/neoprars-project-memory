"""V1.2 entrypoint, help ordering, safe context and read-only branches."""
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import test_memory as fixtures
import memory
import start


ORDER = ['start','audit','recall','resume','summary','roadmap','decisions','handoff','close','history','help']


class StartTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    put = fixtures.MemoryTests.put
    cli = fixtures.MemoryTests.cli

    def seed(self, *, location='docs/memory'):
        source = self.put('notes/facts.md', '# Estado\nfixture evidence\n')
        self.put(location+'/INDEX.md', '[facts](../../notes/facts.md)\n')
        self.put(location+'/sources.json', json.dumps({'schema_version':1,'sources':[
            {'id':'facts','path':'notes/facts.md','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
        self.put(location+'/decisions.jsonl', json.dumps({'id':'d1','title':'Fixture decision','classification':'historico',
            'status':'validado','evidence':[{'source_id':'facts','lines':[1,1]}]})+'\n')
        self.put(location+'/conflicts.jsonl', json.dumps({'id':'c1','title':'Fixture conflict','status':'pendiente','resolution':None})+'\n')
        self.put(location+'/pending.jsonl', json.dumps({'id':'p1','title':'Fixture pending','status':'pendiente'})+'\n')
        return source

    def test_help_order_examples_cli_and_docs(self):
        self.assertEqual([c[0] for c in memory.COMMANDS], ORDER)
        text = self.cli('help')
        found = re.findall(r'^(start|audit|recall|resume|summary|roadmap|decisions|handoff|close|history|help)\s',text,re.M)
        self.assertEqual(found, ORDER)
        self.assertEqual(text.count('Ejemplo:'), 18)  # 11 canonical + seven proposal commands
        self.assertIn('recomendado',text)
        package=fixtures.SCRIPTS.parent
        for name in ('SKILL.md','references/HELP.md'):
            text=(package/name).read_text(encoding='utf-8')
            found = re.findall(r'^\| (?:\*\*)?(start|audit|recall|resume|summary|roadmap|decisions|handoff|close|history|help)(?:\*\*)? \|',text,re.M)
            self.assertEqual(found, ORDER)
        parser_help=self.cli('--help',project=False)
        positions=[parser_help.index(name+',') if name!= 'help' else parser_help.index('help,add-decision') for name in ORDER]
        self.assertEqual(positions,sorted(positions))
        for name in ORDER:
            self.assertIn('Ejemplo:', self.cli(name,'--help'))

    def test_absent_audit_proposal_no_writes(self):
        self.put('notes.md','# Estado\nRoadmap\n')
        before=fixtures.snapshot(self.root)
        with patch.object(Path,'mkdir',side_effect=AssertionError('write attempted')):
            report=start.start_project(self.root)
        self.assertEqual(report['memory_status'],'ausente')
        self.assertIn('audit',report)
        self.assertTrue(report['requires_explicit_approval_for_writes'])
        self.assertIn('approval_question',report['proposal'])
        self.assertEqual(before,fixtures.snapshot(self.root))
        report=json.loads(self.cli('start','--json'))
        self.assertTrue(report['read_only'])
        self.assertEqual(before,fixtures.snapshot(self.root))
        self.assertFalse((self.root/'docs/memory').exists())

    def test_existing_context_no_writes(self):
        self.seed()
        before=fixtures.snapshot(self.root)
        report=json.loads(self.cli('start','--json'))
        self.assertEqual(report['memory_status'],'existente')
        self.assertEqual(report['index']['status'],'completa')
        self.assertEqual(report['sources'][0]['status'],'verificada por huella')
        self.assertEqual(len(report['decisions']),1)
        self.assertEqual(report['conflicts'][0]['resolution'],'pendiente')
        self.assertEqual(len(report['pending']),1)
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_read_modes_only_api(self):
        self.seed()
        real_open=Path.open
        def guarded(path,*args,**kwargs):
            mode=args[0] if args else kwargs.get('mode','r')
            self.assertNotRegex(mode,r'[wax+]')
            return real_open(path,*args,**kwargs)
        with patch.object(Path,'open',guarded):
            with patch.object(Path,'mkdir',side_effect=AssertionError('mkdir')):
                report=start.start_project(self.root)
        self.assertTrue(report['read_only'])

    def test_empty_memory_never_reinitialized(self):
        (self.root/'docs/memory').mkdir(parents=True)
        report=start.start_project(self.root)
        self.assertEqual(report['memory_status'],'incompleta')
        self.assertNotIn('proposal',report)
        self.assertNotIn('audit',report)
        self.assertFalse(list((self.root/'docs/memory').iterdir()))

    def test_legacy_decisions_unknown_authority(self):
        self.seed()
        self.put('docs/memory/decisions.jsonl',json.dumps({'date':'2026-10-09','title':'Legacy',
            'reason':'historic','detail':'not emitted'})+'\n')
        report=start.start_project(self.root)
        self.assertEqual(report['decisions'][0]['id'],'legacy sin ID')
        self.assertEqual(report['decisions'][0]['classification'],'desconocido')
        self.assertNotIn('not emitted',json.dumps(report))

    def test_changed_missing_and_unknown_refs(self):
        source=self.seed()
        source.write_text('changed\n',encoding='utf-8')
        self.put('docs/memory/INDEX.md','[missing](../../absent.md)\n')
        self.put('docs/memory/pending.jsonl',json.dumps({'title':'pending','evidence':[{'source_id':'unknown'}]})+'\n')
        report=start.start_project(self.root)
        self.assertIn('cambiada',report['sources'][0]['status'])
        self.assertEqual(report['references'][0]['status'],'ausente')
        self.assertIn('referencia desconocida',report['pending'][0]['references'])

    def test_invalid_json_duplicate_ids_and_no_repair(self):
        self.seed()
        self.put('docs/memory/decisions.jsonl','{invalid\n{"id":"same"}\n{"id":"same"}\n')
        self.put('docs/memory/sources.json','{invalid')
        before=fixtures.snapshot(self.root)
        report=start.start_project(self.root)
        self.assertEqual(report['memory_status'],'incompleta')
        self.assertTrue(any('duplicado' in x for x in report['issues']))
        self.assertEqual(before,fixtures.snapshot(self.root))

    def test_sensitive_paths_and_contents_not_exposed(self):
        self.seed()
        forbidden=self.put('secrets.md','SYNTHETIC_SECRET_CONTENT')
        self.put('docs/memory/sources.json',json.dumps({'sources':[{'id':'bad','path':'secrets.md','sha256':'a'*64}]}))
        self.put('docs/memory/INDEX.md','[secret](../../secrets.md)\n[remote](https://user:pass@example.test)\n')
        self.put('docs/memory/decisions.jsonl',json.dumps({'title':'token SYNTHETIC_SECRET_TITLE','detail':'SYNTHETIC_SECRET_DETAIL'})+'\n')
        real_open=Path.open
        def guard(p,*a,**k):
            self.assertNotEqual(p,forbidden)
            return real_open(p,*a,**k)
        with patch.object(Path,'open',guard): report=start.start_project(self.root)
        text=json.dumps(report)
        self.assertNotIn('SYNTHETIC_SECRET',text)
        self.assertNotIn('example.test',text)
        self.assertIn('omitida',report['sources'][0]['status'])

    def test_external_paths_and_links_rejected(self):
        self.seed()
        self.put('docs/memory/INDEX.md','[outside](/outside.md)\n[escape](../../../outside.md)\n')
        report=start.start_project(self.root)
        self.assertTrue(all(x['status']=='omitida por seguridad' for x in report['references']))
        for relative in ('../outside','C:/outside','/outside','docs/secrets','person@example.test/memory'):
            report=start.start_project(self.root,memory_root=relative)
            self.assertEqual(report['memory_status'],'desconocida')
            self.assertNotIn('proposal',report)

    def test_custom_memory_location(self):
        self.seed(location='notes/memory')
        report=json.loads(self.cli('start','--json','--memory-root','notes/memory'))
        self.assertEqual(report['memory_status'],'existente')

    def test_budgets_json_partial_not_parsed(self):
        self.seed()
        report=start.start_project(self.root,max_bytes=40,max_document_bytes=20,max_documents=2)
        self.assertTrue(report['partial'])
        self.assertLessEqual(report['coverage']['bytes_read'],40)
        self.assertLessEqual(report['coverage']['documents_read'],2)
        self.assertFalse(report['sources'])
        self.assertTrue(any('JSON parcial' in x for x in report['issues']))

    def test_hash_budget_after_memory_context(self):
        source=self.seed()
        source.write_text('line\n'*40000,encoding='utf-8')
        report=start.start_project(self.root,max_bytes=1500)
        self.assertEqual(len(report['pending']),1)
        self.assertEqual(len(report['decisions']),1)
        self.assertIn('no verificable',report['sources'][0]['status'])
        self.assertLessEqual(report['coverage']['bytes_read'],1500)

    def test_entry_and_depth_limits(self):
        self.seed()
        report=start.start_project(self.root,max_files=2)
        self.assertTrue(report['partial'])
        self.assertLessEqual(report['coverage']['entries_checked'],2)
        self.assertFalse(report['pending'])
        report=start.start_project(self.root,max_depth=0)
        self.assertTrue(report['partial'])

    def test_partial_jsonl_only_complete_records(self):
        self.seed()
        self.put('docs/memory/pending.jsonl','{"title":"first"}\n'+json.dumps({'title':'second','detail':'x'*1000})+'\n')
        report=start.start_project(self.root,max_document_bytes=500)
        self.assertEqual(len(report['pending']),1)
        self.assertEqual(report['pending'][0]['coverage'],'parcial')

    def test_unknown_schema_excluded_authority_and_binary(self):
        self.seed()
        self.put('image.png','binary fixture')
        self.put('docs/memory/sources.json',json.dumps({'schema_version':99,'sources':[
            {'id':'x','path':'notes/facts.md','authority':'excluida','sha256':'a'*64},
            {'id':'y','path':'image.png','sha256':'a'*64}]}))
        report=start.start_project(self.root)
        self.assertEqual(report['memory_status'],'incompleta')
        self.assertIn('excluida',report['sources'][0]['status'])
        self.assertIn('tipo',report['sources'][1]['status'])

    def test_invalid_record_reference_does_not_crash(self):
        self.seed()
        self.put('docs/memory/pending.jsonl','{"title":"bad", "evidence":[{"source_id":[]}]}\n')
        report=start.start_project(self.root)
        self.assertIn('referencia invalida',report['pending'][0]['references'])

    def test_long_official_paths_preserved_and_unknown_record_schema(self):
        self.seed()
        relative='notes/ROADMAP_NEOPRARSPOSVENTA_V5_2026-10-07.txt'
        source=self.put(relative,'Roadmap fixture\n')
        self.put('docs/memory/sources.json',json.dumps({'sources':[{'id':'roadmap','path':relative,
            'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
        self.put('docs/memory/pending.jsonl',json.dumps({'schema_version':99,'title':'Future format',
            'classification':'confirmado','status':'validado'})+'\n')
        report=start.start_project(self.root)
        self.assertEqual(report['sources'][0]['path'],relative)
        self.assertEqual(report['pending'][0]['classification'],'desconocido')
        self.assertEqual(report['pending'][0]['status'],'desconocido')
        self.assertEqual(report['memory_status'],'incompleta')

    def test_time_and_invalid_limits(self):
        self.seed()
        with patch.object(start.time,'monotonic',side_effect=[0]+[50]*1000):
            report=start.start_project(self.root,max_seconds=1)
        self.assertTrue(report['partial'])
        self.assertEqual(report['coverage']['bytes_read'],0)
        for args in ({'max_bytes':0},{'max_documents':0},{'max_seconds':float('nan')},{'chunk_size':0}):
            with self.assertRaises(ValueError):start.start_project(self.root,**args)

    def test_link_guard_without_host_privileges(self):
        self.seed()
        real=Path.is_symlink
        with patch.object(Path,'is_symlink',side_effect=lambda p:p.name=='memory' or real(p),autospec=True):
            report=start.start_project(self.root)
        self.assertEqual(report['memory_status'],'desconocida')
        self.assertNotIn('proposal',report)


if __name__=='__main__':
    unittest.main()
