# GENERATED-BY: portable extraction / repository bootstrap, 2026-11-09 +08:00
# LAST-EDIT: 2026-11-09 +08:00
# PURPOSE: Portable project entrypoint or verification.
# ORIGIN: none
# CUMULATIVE-DELTA: Independent kernel and target profile integration.
"""Static public-source checks. Does not import project modules or read ignored state."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

SKIP={'.git','.local','.autoresearch','temp','__pycache__','node_modules'}
PRIVATE=[('source workspace',re.compile(r'kaggle_competition',re.I)),
         ('live session identity',re.compile(r'session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',re.I)),
         ('original machine names',re.compile(r'(?:008|010|176|180)机')),
         ('private Windows layout',re.compile(r'C:[\\/](?:Users[\\/]Administrator|Program Files[\\/](?:agent|harness|feishu_CLI))',re.I)),
         ('private key',re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'))]
TEXT={'.py','.md','.json','.jsonl','.toml','.js','.mjs','.cjs','.yml','.yaml','.ps1','.txt','.gitignore','.gitattributes'}

def inspect(root):
    root=Path(root).resolve();errors=[];manifest=[]
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if any(part in SKIP for part in relative.parts) or not path.is_file():continue
        raw=path.read_bytes()
        manifest.append({'path':relative.as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        if path.suffix not in TEXT and path.name not in {'.gitignore','.gitattributes'}:continue
        try:text=raw.decode('utf-8-sig')
        except UnicodeError:
            errors.append(f'{relative}: not UTF-8');continue
        if path.suffix=='.py':
            try:ast.parse(text,filename=str(relative))
            except SyntaxError as exc:errors.append(f'{relative}: {exc}')
        if path.suffix=='.json':
            try:json.loads(text)
            except ValueError as exc:errors.append(f'{relative}: invalid JSON: {exc}')
        # The checker contains its own literal signatures, not instance contents.
        if relative.as_posix()=='tools/check_repository.py':continue
        for label,pattern in PRIVATE:
            for found in pattern.finditer(text):
                line=text.count('\n',0,found.start())+1
                errors.append(f'{relative}:{line}: {label}')
        if path.suffix=='.md':
            for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^\n)]+)\)',text):
                target=target.strip().strip('<>')
                if not target or target.startswith(('#','http:','https:','mailto:')):continue
                local=target.split('#',1)[0]
                if '`' in local or '<' in local or '>' in local:continue
                if not (path.parent/local).exists():errors.append(f'{relative}: broken link: {target}')
    return {'format':1,'files':manifest,'file_count':len(manifest),'bytes':sum(f['bytes'] for f in manifest),
            'errors':errors,'ok':not errors,
            'scope':'Static known-pattern and syntax checks; no guarantee against every possible private value.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));parser.add_argument('--report')
    args=parser.parse_args();result=inspect(args.root)
    if args.report:
        target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False,indent=2))
    return 0 if result['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
