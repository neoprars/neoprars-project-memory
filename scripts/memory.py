#!/usr/bin/env python3
"""Neoprars Project Memory V1.4.2 — local stdlib CLI."""
import sys
sys.dont_write_bytecode = True
import argparse
import copy
import datetime as dt
import json
import math
import re
import time
from audit import audit_project, git_read, render, EXCLUDED, SENSITIVE
from start import start_project, render_start
from store import Store, READ_DEFAULTS, scrub
from safety import safe_path
from workflow import prepare, apply, event_base, snapshot, source_registry

ROADMAP_NAMES = ('ROADMAP.md','ROADMAP.txt','roadmap.md','roadmap.txt')
STATE_NAMES = ('PROJECT_STATE.md','PROJECT_STATE.txt')
COMMANDS = (
    ('start','Punto de entrada recomendado; contexto o propuesta sin escribir','start --json'),
    ('audit','Auditar fuentes y cobertura sin escribir','audit --max-bytes 2000000'),
    ('recall','Recuperar sesiones registradas','recall --date 2026-10-09'),
    ('resume','Mostrar estado, vigencia, conflictos y ultimas sesiones','resume'),
    ('summary','Resumir sesiones; exportacion opcional','summary --date 2026-10-09 --export'),
    ('roadmap','Exportar roadmap existente','roadmap --mode pending'),
    ('decisions','Consultar decisiones y vigencia de evidencia','decisions'),
    ('handoff','Transferir contexto portable en Markdown o TXT','handoff --preview'),
    ('close','Preparar cierre; guardar solo propuesta confirmada','close --date 2026-10-09 --done "Trabajo confirmado"'),
    ('history','Consultar hashes y fechas Git del roadmap','history'),
    ('help','Mostrar comandos y ejemplos','help'),
)
EXTRA = (
    ('add-decision','Preparar decision o sustitucion','add-decision --help'),
    ('initialize','Preparar inicializacion sin registros ficticios','initialize --json'),
    ('register-source','Preparar registro o revision de fuente','register-source --id facts --path notes/facts.md --reason Revisado'),
    ('add-conflict','Preparar conflicto con alternativas','add-conflict --help'),
    ('resolve-conflict','Preparar resolucion supervisada','resolve-conflict --help'),
    ('apply','Persistir propuesta exacta confirmada','apply --proposal work/proposal.json --mode supervised --approval-sha256 HUELLA --approved-by usuario'),
    ('close-fast','Preparar cierre local sin repetir pruebas vigentes','close-fast --date 2026-10-15 --done "Trabajo confirmado"'),
)


def add_read_options(parser):
    parser.add_argument('--json',action='store_true')
    for name, default in READ_DEFAULTS.items():
        parser.add_argument('--'+name.replace('_','-'),type=float if name=='max_seconds' else int,default=default)
    parser.add_argument('--exclude',action='append',default=[])


def session_text(items):
    if not items:
        return 'Sin sesiones registradas para el filtro solicitado. No implica que no se haya trabajado.\n'
    lines=[]
    for item in items:
        lines.append('Fecha: '+str(item.get('date','?'))+' | Modulo: '+str(item.get('module','no indicado')))
        for key,title in (('done','Realizado'),('tests','Pruebas'),('files','Archivos'),('pending','Pendientes'),
                          ('decisions','Decisiones'),('conflicts','Conflictos')):
            if item.get(key):
                lines.append(title+': '+str(item[key]))
        lines.append('Resultado de pruebas declarado: '+str(item.get('tests_status','desconocidas (legacy)')))
        lines.append('')
    return scrub('\n'.join(lines))


def official(store, explicit, names):
    if explicit:
        return store.read(explicit,official=True),explicit
    for name in names:
        path=safe_path(store.root,name,store.exclusions,SENSITIVE,store.limits['max_depth'])
        if path and path.is_file():
            return store.read(name,official=True),name
    store.issues.append('Fuente desconocida; indicar ruta explicita.')
    return None,None


def roadmap_text(store,args):
    contents,source=official(store,args.roadmap_path,ROADMAP_NAMES)
    if contents is None:
        return 'No se encontro roadmap verificable. Revisar la ubicacion manualmente.\n'
    if args.cmd=='roadmap' and args.mode=='pending':
        contents='\n'.join(line for line in contents.splitlines()
            if re.search(r'\[ \]|pendiente|por hacer|todo|in progress|en progreso',line,re.I))
        return scrub('FUENTE CANDIDATA: '+source+'\nFILTRO DE PENDIENTES (no sustituye el roadmap completo)\n'+contents)
    return scrub('FUENTE CANDIDATA: '+source+'\n'+contents)


GIT_LIST_LIMIT = 20


def _git_lines(value, limit=GIT_LIST_LIMIT):
    lines = [scrub(line) for line in value.splitlines()] if value else []
    return lines[:limit], len(lines), len(lines) > limit


def summarize_git(name, value, detail=False):
    """Compact, read-only view of a Git query; None always means not verified, never clean."""
    if value is None:
        return {'verified': False}
    if name == 'head':
        return {'verified': True, 'output': scrub(value)}
    if name == 'status':
        lines = value.splitlines()
        branch = next((scrub(line[3:]) for line in lines if line.startswith('## ')), None)
        entries = [line for line in lines if not line.startswith('## ')]
        untracked = sum(1 for line in entries if line.startswith('??'))
        staged = sum(1 for line in entries if line[:1] not in (' ', '?', '!'))
        paths, total, truncated = _git_lines('\n'.join(entries))
        item = {'verified': True, 'branch': branch, 'total': total, 'tracked_changes': total - untracked,
                'untracked': untracked, 'staged': staged, 'paths': paths, 'truncated': truncated}
    else:
        findings, total, truncated = _git_lines(value)
        item = {'verified': True, 'clean': total == 0, 'total_lines': total, 'findings': findings, 'truncated': truncated}
    if detail:
        item['output'] = scrub(value)
    return item


def prepare_fast_close(store, data, detail=False):
    """Reuse close validation and persistence; never execute tests or mutate Git."""
    sessions = store.sessions()
    if store.issues:
        raise ValueError('Sesiones no verificables; no se puede descartar duplicado.')
    # prepare congela el hash de cada evidencia; se compara contra la misma forma, sin mutar data.
    candidate = copy.deepcopy(data)
    if candidate.get('evidence'):
        registry = {x['id']: x for x in source_registry(store)['sources']}
        for proof in candidate['evidence']:
            known = registry.get(proof.get('source_id')) if isinstance(proof, dict) else None
            if known:
                proof.setdefault('sha256', known['sha256'])
    fields = set(candidate) - {'id', 'created_at'}
    duplicate = next((item for item in sessions
        if all(item.get(key) == candidate[key] for key in fields)), None)
    if duplicate:
        result = {'read_only': True, 'duplicate': True, 'existing_id': duplicate.get('id'),
                  'next': 'Cierre equivalente ya registrado; no aplicar otra sesion.'}
    else:
        result = prepare(store, 'close', data)  # mismo ID con contenido distinto sigue rechazado
        result['duplicate'] = False
    git = {}
    for key, command in (
        ('head', ('rev-parse', '--verify', 'HEAD')),
        ('status', ('status', '--short', '--branch', '--untracked-files=normal')),
        ('diff_check', ('diff', '--check')),
        ('cached_diff_check', ('diff', '--cached', '--check')),
    ):
        remaining = store.deadline - time.monotonic()
        # git diff --check devuelve 2 cuando hay hallazgos: es un resultado valido, no un fallo.
        value = git_read(store.root, *command, timeout=min(5, remaining),
                         accept=(0, 2) if 'diff' in command else (0,)) if remaining > 0 else None
        git[key] = summarize_git(key, value, detail)
    result['git'] = git
    result['tests_executed'] = False
    return result


def main():
    parser=argparse.ArgumentParser(description='Neoprars Project Memory V1.4.2; start es el punto de entrada recomendado')
    parser.add_argument('--project')
    sub=parser.add_subparsers(dest='cmd',required=True)
    parsers={name:sub.add_parser(name,help=description,description=description,epilog='Ejemplo: '+example)
             for name,description,example in COMMANDS+EXTRA}
    for name,p in parsers.items():
        add_read_options(p)
        p.add_argument('--memory-root',default='docs/memory')
        if name!='roadmap':
            p.add_argument('--mode',choices=('automatic','supervised'),default='automatic')
    for name in ('recall','summary'):
        p=parsers[name]
        p.add_argument('--date')
        p.add_argument('--from',dest='since')
        p.add_argument('--to',dest='until')
        p.add_argument('--module')
        if name=='summary':
            p.add_argument('--export',action='store_true')
    for name in ('resume','handoff'):
        parsers[name].add_argument('--state-path')
    for name in ('roadmap','handoff','history'):
        parsers[name].add_argument('--roadmap-path')
    p=parsers['handoff']
    p.add_argument('--preview',action='store_true',help='Mostrar transferencia sin crear archivos')
    p.add_argument('--format',choices=('markdown','txt'),default='markdown')
    p.add_argument('--module',help='Modulo exacto de sesiones/eventos; conservar contexto compartido')
    p.add_argument('--sessions',type=int,default=10,help='Ultimas 1 a 50 sesiones seleccionadas')
    p.add_argument('--goal',help='Objetivo aportado por el usuario; no inferirlo')
    p.add_argument('--next-step',help='Siguiente paso propuesto; no equivale a autorizacion')
    p.add_argument('--technical',action='store_true',help='Incluir anexo tecnico de contexto')
    p.add_argument('--full-documents',action='store_true',help='Incluir estado y roadmap completos; por defecto solo referencias')
    parsers['roadmap'].add_argument('--mode',choices=('full','pending','handoff'),default='full')
    for name in ('close','close-fast','add-decision','add-conflict','resolve-conflict'):
        p=parsers[name]
        p.add_argument('--id')
        p.add_argument('--date',required=True)
        p.add_argument('--evidence',action='append',default=[],help='JSON source_id, sha256 opcional y lines opcional')
        p.add_argument('--classification',choices=('confirmado','historico','propuesto','desconocido'),default='confirmado')
        p.add_argument('--status',choices=('pendiente','implementado','validado','cerrado','no_aplica'),default='pendiente')
    for name in ('close','close-fast'):
        p=parsers[name]
        p.add_argument('--module',default='general')
        p.add_argument('--done',required=True)
        for key in ('pending','tests','files'):
            p.add_argument('--'+key,default='')
        p.add_argument('--tests-status',choices=('aprobadas','fallidas','no_ejecutadas','desconocidas'),default='desconocidas')
        p.add_argument('--decision-id',action='append',default=[])
        p.add_argument('--conflict-id',action='append',default=[])
    parsers['close-fast'].add_argument('--git-detail',action='store_true',help='Incluir salida Git completa (puede ser extensa)')
    p=parsers['add-decision']
    for key in ('title','reason','detail'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--supersedes')
    p=parsers['add-conflict']
    p.add_argument('--title',required=True)
    p.add_argument('--conflict-type',choices=('contenido','autoridad','historico','evidencia','formato'),required=True)
    p.add_argument('--alternative',action='append',required=True,help='JSON id, claim y evidence')
    p=parsers['resolve-conflict']
    for key in ('conflict-id','choice','reason'):
        p.add_argument('--'+key,required=True)
    p=parsers['register-source']
    for key in ('id','path','reason'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--authority',choices=('candidata','confirmada','excluida'),default='candidata')
    p=parsers['apply']
    for key in ('proposal','approval-sha256','approved-by'):
        p.add_argument('--'+key,required=True)
    args=parser.parse_args()
    limits={key:getattr(args,key) for key in READ_DEFAULTS}
    if not math.isfinite(args.max_seconds):
        parser.error('Tiempo invalido.')
    try:
        if args.cmd in ('audit','start'):
            options={**limits,'exclude':args.exclude}
            report=(start_project(args.project,memory_root=args.memory_root,mode=args.mode,**options)
                    if args.cmd=='start' else audit_project(args.project,**options))
            print(json.dumps(report,ensure_ascii=True,indent=2) if args.json else
                  (render_start(report) if args.cmd=='start' else render(report)))
            return
        if args.cmd=='help':
            print('NEOPRARS PROJECT MEMORY V1.4.2 — start: punto de entrada recomendado')
            for name,description,example in COMMANDS+EXTRA:
                print(f'{name:<10} {description}\n           Ejemplo: {example}')
            print('Solo lectura: start, audit y propuestas. Escritura: apply supervised y exportaciones solicitadas.\n'
                  '--project antes del comando; --memory-root y presupuestos despues.')
            return
        store=Store(args.project,args.memory_root,exclude=args.exclude,**limits)
        if args.cmd=='apply':
            contents=store.read(args.proposal,official=True)
            if contents is None or store.budget.coverage[-1]['status']!='completa':
                raise ValueError('Propuesta no verificable.')
            result=apply(store,json.loads(contents),args.approval_sha256,args.approved_by,args.mode)
        elif args.cmd in ('initialize','register-source','close','close-fast','add-decision','add-conflict','resolve-conflict'):
            kind={'initialize':'initialize','register-source':'source','close':'close','close-fast':'close','add-decision':'decision',
                  'add-conflict':'conflict','resolve-conflict':'resolution'}[args.cmd]
            if kind=='initialize':
                data={}
            elif kind=='source':
                data={'id':args.id,'path':args.path,'reason':args.reason,'authority':args.authority,
                      'sha256':snapshot(store,args.path,official=True)}
            else:
                data=event_base(args)
                if kind=='close':
                    data.update({key:getattr(args,key) for key in ('module','done','pending','tests','files','tests_status')})
                    data.update(decisions=args.decision_id,conflicts=args.conflict_id)
                elif kind=='decision':
                    data.update({key:getattr(args,key) for key in ('title','reason','detail','supersedes')})
                elif kind=='conflict':
                    data.update(title=args.title,conflict_type=args.conflict_type,
                                alternatives=[json.loads(x) for x in args.alternative],resolution=None)
                else:
                    data.update(title='Resolucion de '+args.conflict_id,resolves=args.conflict_id,
                                choice=args.choice,reason=args.reason,resolution='supervisada')
            result=(prepare_fast_close(store,data,args.git_detail) if args.cmd=='close-fast' else prepare(store,kind,data))
        else:
            result=query(store,args,limits)
        if args.cmd=='handoff' and not args.json:
            print(result['output'])
            if result.get('export'):
                print('Archivo generado:',result['export'],file=sys.stderr)
        else:
            print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        parser.error('Operacion rechazada: ruta, formato, evidencia, presupuesto o aprobacion no verificable. Revisar estado antes de reintentar.')


def query(store,args,limits):
    if args.cmd=='handoff':
        from handoff import transfer
        return transfer(store,args,limits,official)
    output=''
    context=None
    export=None
    if args.cmd in ('resume','decisions'):
        context=start_project(store.root,memory_root=args.memory_root,mode=args.mode,
            **{**limits,'max_seconds':max(0.001,store.deadline-time.monotonic())},exclude=args.exclude)
        coverage=context.get('coverage',context.get('audit',{}).get('coverage',{}))
        store.budget.spent+=coverage.get('bytes_read',0)
        store.budget.opened+=coverage.get('documents_read',0)
        store.entries+=coverage.get('entries_checked',coverage.get('entries_seen',0))
        if context.get('partial'):
            store.issues.append('Contexto de memoria parcial; revisar vigencia.')
    if args.cmd in ('recall','summary','resume'):
        items=store.sessions()
        if args.cmd in ('recall','summary'):
            since,until=args.date or args.since,args.date or args.until
            items=[x for x in items if (not since or str(x.get('date',''))>=since)
                   and (not until or str(x.get('date',''))<=until)
                   and (not args.module or args.module.casefold() in json.dumps(x,ensure_ascii=False).casefold())]
        else:
            items=items[-3:] if args.cmd=='resume' else items[-10:]
        output=session_text(items)
        if args.cmd=='summary' and args.export:
            export='SESSION_SUMMARY'
    if args.cmd=='resume':
        state,source=official(store,args.state_path,STATE_NAMES)
        output='ESTADO (fuente candidata: '+str(source)+')\n'+scrub(state or 'Fuente desconocida.')+'\n\nULTIMAS SESIONES\n'+output
    if args.cmd=='roadmap':
        output+='\nROADMAP\n'+roadmap_text(store,args)
        export='ROADMAP_'+args.mode.upper()
    if args.cmd=='history':
        _,source=official(store,args.roadmap_path,ROADMAP_NAMES)
        remaining=store.deadline-time.monotonic()
        history=git_read(store.root,'log','-20','--format=%H %cs','--',source,timeout=min(5,remaining)) if source and remaining>0 else None
        output=history or 'No se pudo consultar historial Git acotado.'
    result={'command':args.cmd,'output':output,'coverage':store.coverage(),'read_only':export is None}
    if context is not None:
        result['context']=context
    if export:
        contents=output+'\n\nCONTEXTO Y LIMITES\n'+json.dumps({'context':context,'coverage':store.coverage()},ensure_ascii=False,indent=2)
        name='exports/'+export+'_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.txt'
        path=store.create(name,scrub(contents),export=True)
        result['export']=str(path)
        print('Archivo TXT generado:',path,file=sys.stderr)
    return result


if __name__=='__main__':
    main()
