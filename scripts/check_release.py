"""Validate version consistency and the explicitly permitted public file set."""
import ast
import pathlib
import re

ROOT=pathlib.Path(__file__).resolve().parents[1]
RUNTIME_FILES={'__init__.py','_conf_schema.json','engine.py','main.py','metrics.py','metadata.yaml','README.md','LICENSE'}
PUBLIC_FILES=RUNTIME_FILES|{'CHANGELOG.md','SECURITY.md','.gitignore','.gitattributes',
    '.github/workflows/tests.yml','docs/CONFIGURATION.md','docs/VALIDATION.md','docs/VERSIONING.md',
    'tests/test_governor.py','tests/integration_astrbot.py','scripts/check_release.py','scripts/build_release.py'}
IGNORED={'.git','__pycache__','test-artifacts','dist','.pytest_cache'}
PATTERNS={
    'private-key':re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----'),
    'github-token':re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'cloud-access-key':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'absolute-windows-path':re.compile(r'(?i)\b[A-Z]:[\\/]'),
    'private-deployment-path':re.compile(r'/(?:opt|root|var/backups)/'),
    'ipv4-address':re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
    'email-address':re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'),
}


def version():
    text=(ROOT/'metadata.yaml').read_text(encoding='utf-8')
    match=re.search(r'^version:\s*v?(\d+\.\d+\.\d+)\s*$',text,re.M)
    assert match,'Invalid metadata version'
    return match[1]


def scan_text(relative,content):
    text=content.decode('utf-8')
    for name,pattern in PATTERNS.items():
        assert not pattern.search(text),f'Public-content check failed: {relative}: {name}'


def check():
    actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
        and not any(part in IGNORED for part in p.relative_to(ROOT).parts)}
    assert actual==PUBLIC_FILES, f'Unexpected or missing public paths: {sorted(actual ^ PUBLIC_FILES)}'
    for relative in sorted(actual):
        scan_text(relative,(ROOT/relative).read_bytes())
        if relative.endswith('.py'):
            compile((ROOT/relative).read_bytes(),relative,'exec')
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    plugin=next(n for n in tree.body if isinstance(n,ast.ClassDef))
    register=next(d for d in plugin.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='register')
    assert ast.literal_eval(register.args[3])==version(),'Runtime/metadata versions differ'
    assert ast.literal_eval(register.args[1])=='Boston-Lobster','Public author identity differs'
    assert 'repo: https://github.com/Boston-Lobster/astrbot_plugin_cache_governor' in (ROOT/'metadata.yaml').read_text(encoding='utf-8')
    print(f'Validated {len(actual)} public files and v{version()}; no forbidden content patterns found')


if __name__=='__main__':check()
