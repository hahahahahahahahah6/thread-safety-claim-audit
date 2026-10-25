"""Offline documentation contract checks using static syntax only."""
import argparse
import ast
import fnmatch
import json
import os
from pathlib import Path
import re
import sys

THREADSAFE = (
    'thread safe', 'safe for concurrent use',
    'safe to call from multiple threads', 'safe to use from multiple threads',
    'safe to use concurrently', 'safe across threads',
    'safe for multithreaded use', 'safe for multi threaded use',
    'thread safe by design',
)
NOTSAFE = (
    'not thread safe', 'not safe for concurrent use',
    'not safe to call from multiple threads', 'not safe to use from multiple threads',
    'not safe to use concurrently', 'not safe across threads',
    'not safe for multithreaded use', 'not safe for multi threaded use',
    'no thread safety', 'must not be called from multiple threads',
    'must not be used from multiple threads',
)


def normalize(text):
    return re.sub(r'\s+', ' ', text.lower().replace("n't", ' not').replace('-', ' ')).strip()


def extract_claims(text, path='README.md'):
    claims = []
    fence = None
    pending = []

    def flush():
        if not pending:
            return
        start = pending[0][0]
        prose = ' '.join(value for _, value in pending)
        for sentence in re.split(r'(?<=[.!?])\s+', prose):
            normalized = normalize(sentence)
            masked = normalized
            found_not = False
            for phrase in sorted(NOTSAFE, key=len, reverse=True):
                pattern = r'\b' + re.escape(phrase) + r'\b'
                if re.search(pattern, masked):
                    found_not = True
                    masked = re.sub(pattern, lambda m: ' ' * len(m.group()), masked)
            families = ['notsafe'] if found_not else []
            if not re.search(r'\b(no|not|never|without)\b', normalized):
                if any(re.search(r'\b' + re.escape(p) + r'\b', masked) for p in THREADSAFE):
                    families.append('threadsafe')
            for family in families:
                claims.append(dict(family=family, path=path, line=start, sentence=sentence.strip()))
        pending.clear()

    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                flush()
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        if not line.strip():
            flush()
        else:
            pending.append((number, line.strip()))
    flush()
    return claims


def extract_evidence(text, path):
    tree = ast.parse(text, filename=path)
    modules = {}
    imported = {}
    constructors = {'Lock', 'RLock', 'Semaphore', 'BoundedSemaphore', 'Condition'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ('threading', 'multiprocessing'):
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module in ('threading', 'multiprocessing'):
                for alias in node.names:
                    imported[alias.asname or alias.name] = (node.module, alias.name)

    def kind_for(module, name):
        if module == 'threading' and name == 'local':
            return 'thread-local'
        allowed = constructors if module == 'threading' else {'Lock', 'RLock'}
        return 'lock-constructor' if name in allowed else None

    items = set()
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == 'acquire':
                    kind = 'acquire'
                elif isinstance(func.value, ast.Name) and func.value.id in modules:
                    kind = kind_for(modules[func.value.id], func.attr)
            elif isinstance(func, ast.Name) and func.id in imported:
                kind = kind_for(*imported[func.id])
        if kind:
            items.add((node.lineno, kind))
        if isinstance(node, ast.With):
            for item in node.items:
                context = item.context_expr
                name = context.id if isinstance(context, ast.Name) else context.attr if isinstance(context, ast.Attribute) else ''
                if 'lock' in name.lower():
                    items.add((node.lineno, 'with-lock'))
    return [dict(path=path, line=line, kind=kind) for line, kind in sorted(items)]


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('audit path must be a directory')
    claims, production, test_only = [], [], []
    source_count = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(directory) / d).is_symlink() and d not in {'.git', '__pycache__', '.venv', 'venv', 'build', 'dist'} and not d.endswith('.egg-info'))
        for filename in sorted(files):
            path = Path(directory) / filename
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or filename == '.env' or any(fnmatch.fnmatchcase(relative, p) for p in ignore):
                continue
            parts = Path(relative).parts
            is_doc = relative == 'README.md' or (parts[0] == 'docs' and path.suffix == '.md')
            is_source = path.suffix == '.py' and 'docs' not in parts[:-1] and 'examples' not in parts[:-1]
            if not is_doc and not is_source:
                continue
            text = path.read_text(encoding='utf-8')
            if is_doc:
                claims.extend(extract_claims(text, relative))
            if is_source:
                testing = 'tests' in parts[:-1] or 'fixtures' in parts[:-1] or filename.startswith('test_') or filename.endswith('_test.py')
                evidence = extract_evidence(text, relative)
                if testing:
                    test_only.extend(evidence)
                else:
                    source_count += 1
                    production.extend(evidence)
    findings, verified = [], []
    for family in ('threadsafe', 'notsafe'):
        family_claims = [c for c in claims if c['family'] == family]
        if not family_claims:
            continue
        rule = 'documented-' + ('thread-safe' if family == 'threadsafe' else 'not-thread-safe') + '-claim-false'
        evidence = production
        severity = 'HIGH'
        if source_count == 0:
            evidence = [dict(kind='no-source')]
        elif not production and test_only:
            rule += '-test-only'
            severity = 'MEDIUM'
            evidence = test_only
        elif (family == 'threadsafe' and production) or (family == 'notsafe' and not production):
            verified.append(dict(family=family, claims=family_claims, evidence=production))
            continue
        findings.append(dict(rule=rule, severity=severity, family=family, claims=family_claims, evidence=evidence))
    return dict(findings=findings, verified_claims=verified)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    parser = Parser(prog='thread-safety-claim-audit', description='Offline audit of documented thread-safety claims against Python source.')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB')
    parser.add_argument('--fail-on', choices=('MEDIUM', 'HIGH'), default='MEDIUM')
    parser.add_argument('--version', action='version', version='0.1.0')
    args = parser.parse_args(argv)
    try:
        result = audit(args.path, args.ignore)
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        print('thread-safety-claim-audit: ' + str(exc), file=sys.stderr)
        return 1
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings'])
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print('FAIL' if failed else 'PASS')
        for finding in result['findings']:
            print(finding['severity'] + ' ' + finding['rule'])
            for claim in finding['claims']:
                print('  {path}:{line}: {sentence}'.format(**claim))
            if args.verbose:
                for evidence in finding['evidence']:
                    print('  ' + json.dumps(evidence, sort_keys=True))
        if args.verbose:
            for claim in result['verified_claims']:
                print('verified: ' + claim['family'])
    return 2 if failed else 0
