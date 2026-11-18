"""stdlib-only CI detection of new direct engine consumers, not a Python sandbox."""
import ast
import hashlib
import json
import os
from pathlib import Path

PREFIXES=('extensions.sec_evidence_v1','extensions.sec_evidence_v1_1','extensions.financial_impact_v1')
SKIP={'.git','.venv','__pycache__','reports','local_data','node_modules','vendor','models','.agents','.codex'}


def restricted(name):return any(name==p or name.startswith(p+'.') for p in PREFIXES)


def constant(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,str):return node.value
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
        a,b=constant(node.left),constant(node.right)
        if a is not None and b is not None:return a+b
    return None


class Edges(ast.NodeVisitor):
    def __init__(self,module):
        self.module=module;self.aliases={};self.edges=[];self.functions=[];self.stack=[]

    def target(self,node):
        if isinstance(node,ast.Name):return self.aliases.get(node.id,node.id)
        if isinstance(node,ast.Attribute):return self.target(node.value)+'.'+node.attr
        return ''

    def add(self,node,kind,target):
        if restricted(target):self.edges.append({'line':node.lineno,'kind':kind,'target':target,'function':'.'.join(self.stack) or '<module>'})

    def visit_Import(self,node):
        for n in node.names:
            self.aliases[n.asname or n.name.split('.')[0]]=n.name if n.asname else n.name.split('.')[0]
            self.add(node,'import',n.name)

    def visit_ImportFrom(self,node):
        base=node.module or ''
        if node.level:
            package=self.module.split('.')[:-1]
            base='.'.join(package[:len(package)-node.level+1]+([base] if base else []))
        for n in node.names:
            name=base+'.'+n.name if base else n.name
            self.aliases[n.asname or n.name]=name;self.add(node,'import',name)

    def visit_Call(self,node):
        target=self.target(node.func);self.add(node,'call',target)
        if target in {'__import__','importlib.import_module'} and node.args:
            name=constant(node.args[0])
            if name:self.add(node,'dynamic_import',name)
        self.generic_visit(node)

    def visit_FunctionDef(self,node):
        self.stack.append(node.name);self.functions.append({'function':'.'.join(self.stack),'line':node.lineno})
        self.generic_visit(node);self.stack.pop()
    visit_AsyncFunctionDef=visit_FunctionDef

    def visit_ClassDef(self,node):
        self.stack.append(node.name);self.generic_visit(node);self.stack.pop()


def audit(root,policy=None):
    root=Path(root).resolve()
    policy=policy or json.loads((Path(__file__).parent/'legacy_boundary_policy.json').read_text(encoding='utf-8'))
    findings=[];violations=[];entries=[]
    for rel,expected in policy['frozen_files'].items():
        p=root/rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            violations.append({'path':rel,'code':'FROZEN_ENGINE_CHANGED'})
    paths=[]
    for directory,folders,names in os.walk(root):
        folders[:]=[n for n in folders if n not in SKIP]
        paths += [Path(directory)/n for n in names if n.endswith('.py')]
    for p in sorted(paths):
        rel=p.relative_to(root)
        if set(rel.parts)&SKIP:continue
        name=rel.as_posix();module='.'.join(rel.with_suffix('').parts)
        visitor=Edges(module)
        try:visitor.visit(ast.parse(p.read_text(encoding='utf-8-sig')))
        except (SyntaxError,UnicodeError) as exc:
            violations.append({'path':name,'code':'SOURCE_NOT_AUDITED','error':type(exc).__name__});continue
        is_test='tests' in rel.parts or p.name.startswith('test_')
        frozen=name in policy['frozen_files'];allowed=policy['official_edges'].get(name,{})
        for edge in visitor.edges:
            role='INTERNAL_TEST_ONLY' if is_test else 'LEGACY_REPLAY_ONLY' if frozen and '/sec_evidence_v1/' in name else 'READ_ONLY_DISCOVERY' if frozen else 'NEW_PRODUCTION_INPUT'
            okay=is_test or frozen or edge['target'] in allowed.get(edge['kind'],[])
            item={'path':name,**edge,'classification':role if okay else 'UNSAFE_BYPASS','allowed':okay}
            findings.append(item)
            if not okay:violations.append({'path':name,**edge,'code':'NEW_DIRECT_ENGINE_CONSUMER'})
        for f in visitor.functions:
            key=module+'.'+f['function']
            if key in policy['entrypoints']:
                entries.append({'path':name,**f,'symbol':key,**policy['entrypoints'][key]})
    expected=set(policy['entrypoints']);found={e['symbol'] for e in entries}
    for missing in sorted(expected-found):violations.append({'symbol':missing,'code':'DECLARED_ENTRYPOINT_NOT_FOUND'})
    return {'contract':'sec-current-boundary-audit-v1','status':'PASS' if not violations else 'FAIL',
        'findings':findings,'entrypoints':entries,'violations':violations,
        'active_external_consumers':[f for f in findings if not f['allowed']],
        'residual_direct_calls':[e for e in entries if e['classification']=='UNSAFE_BYPASS'],
        'limitations':['Static import/call/literal dynamic-import detection; arbitrary Python, reflection and edits to this policy are not sandboxed.',
                      'Frozen direct APIs/CLIs remain callable for historical compatibility. Official-gate success is separate from residual risk.']}


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path)
    args=p.parse_args(argv);value=audit(args.root)
    if args.out:
        with args.out.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'status':value['status'],'violations':len(value['violations']),'residual_direct_entrypoints':len(value['residual_direct_calls'])}))
    return int(bool(value['violations']))


if __name__=='__main__':raise SystemExit(main())
