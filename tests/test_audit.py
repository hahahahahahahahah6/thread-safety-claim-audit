import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from thread_safety_claim_audit.audit import (
    THREADSAFE, NOTSAFE, audit, extract_claims, extract_evidence, main, normalize,
)

FIXTURES = Path(__file__).parent / 'fixtures'


class Claims(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize(" ISN'T thread-safe  "), 'is not thread safe')

    def test_fences(self):
        self.assertEqual(extract_claims('```text\nis thread-safe\n```'), [])

    def test_tilde_fences(self):
        self.assertEqual(extract_claims('~~~text\nis thread-safe\n~~~'), [])

    def test_inline(self):
        self.assertEqual(extract_claims('This is `thread-safe`.')[0]['family'], 'threadsafe')

    def test_overlap(self):
        self.assertEqual([c['family'] for c in extract_claims('is not thread-safe')], ['notsafe'])

    def test_contraction(self):
        self.assertEqual([c['family'] for c in extract_claims("isn't thread-safe")], ['notsafe'])

    def test_multiple_sentences(self):
        self.assertEqual(len(extract_claims('is thread safe. It is not thread safe.')), 2)

    def test_boundaries(self):
        self.assertEqual(extract_claims('unthread safeish'), [])

    def test_self_audit(self):
        self.assertEqual(audit(Path(__file__).parents[1])['findings'], [])
        self.assertEqual(extract_claims((Path(__file__).parents[1] / 'README.md').read_text()), [])


for index, phrase in enumerate(THREADSAFE):
    def check(self, phrase=phrase):
        self.assertEqual([c['family'] for c in extract_claims(phrase)], ['threadsafe'])
        self.assertEqual([c['family'] for c in extract_claims(phrase.replace(' ', '-'))], ['threadsafe'])
    setattr(Claims, 'test_positive_phrase_%02d' % index, check)
for index, phrase in enumerate(NOTSAFE):
    def check(self, phrase=phrase):
        self.assertEqual([c['family'] for c in extract_claims(phrase)], ['notsafe'])
    setattr(Claims, 'test_negative_phrase_%02d' % index, check)
for index, phrase in enumerate(('thread safety', 'threadsafe', 'never thread safe', 'does not claim to be thread safe', 'without being thread safe', 'no promise of thread safe', 'safe to call concurrently')):
    def check(self, phrase=phrase):
        self.assertEqual(extract_claims(phrase), [])
    setattr(Claims, 'test_nonclaim_%02d' % index, check)


class Evidence(unittest.TestCase):
    pass


CASES = [
 ('import threading\nx = threading.Lock()', ['lock-constructor']),
 ('import threading as t\nx = t.RLock()', ['lock-constructor']),
 ('from threading import Lock\nx = Lock()', ['lock-constructor']),
 ('from threading import Lock as L\nx = L()', ['lock-constructor']),
 ('x = Lock()', []),
 ('threading.Lock()', []),
 ('# threading.Lock()\ns = \"lock.acquire()\"', []),
 ('import multiprocessing\nx = multiprocessing.Lock()', ['lock-constructor']),
 ('from multiprocessing import RLock as R\nx = R()', ['lock-constructor']),
 ('from multiprocessing import Semaphore as S\nx = S()', []),
 ('thing.acquire()', ['acquire']),
 ('with lock:\n pass', ['with-lock']),
 ('with self.myLOCK:\n pass', ['with-lock']),
 ('with open("a"):\n pass', []),
 ('with resource:\n pass', []),
 ('import threading\nx = threading.local()', ['thread-local']),
 ('from threading import local as L\nx = L()', ['thread-local']),
 ('import threading', []),
 ('from threading import Thread\nt = Thread()', []),
 ('import asyncio\nx = asyncio.Lock()', []),
 ('import queue\nx = queue.Queue()', []),
 ('from threading import Lock\nx: Lock', []),
 ('import threading\nx = threading.Lock', []),
 ('import threading\nx = threading.Barrier(2)\ny = threading.Event()', []),
 ('import multiprocessing\nx = multiprocessing.Semaphore()', []),
 ('def unused():\n import threading\n return threading.Lock()', ['lock-constructor']),
 ('with lock, another_lock:\n pass', ['with-lock']),
]
for index, (source, kinds) in enumerate(CASES):
    def check(self, source=source, kinds=kinds):
        self.assertEqual([e['kind'] for e in extract_evidence(source, 'main.py')], kinds)
    setattr(Evidence, 'test_evidence_%02d' % index, check)
for name in ('Semaphore', 'BoundedSemaphore', 'Condition', 'RLock'):
    def check(self, name=name):
        self.assertEqual(len(extract_evidence('import threading\nx = threading.' + name + '()', 'main.py')), 1)
    setattr(Evidence, 'test_constructor_' + name, check)


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, output.getvalue()

    def test_fixtures(self):
        for name, code, rule in (
            ('good/threadsafe', 0, None), ('good/notsafe', 0, None),
            ('bad/threadsafe', 2, 'documented-thread-safe-claim-false'),
            ('bad/notsafe', 2, 'documented-not-thread-safe-claim-false'),
            ('testonly', 2, 'documented-thread-safe-claim-false-test-only'),
        ):
            with self.subTest(name=name):
                actual, output = self.run_cli(str(FIXTURES / name), '--json')
                self.assertEqual(actual, code)
                result = json.loads(output)
                self.assertEqual(set(result), {'findings', 'verified_claims'})
                self.assertEqual(len(result['findings']), int(rule is not None))
                if rule:
                    self.assertEqual(result['findings'][0]['rule'], rule)
                    self.assertEqual(result['findings'][0]['severity'], 'MEDIUM' if name == 'testonly' else 'HIGH')
                else:
                    self.assertEqual(len(result['verified_claims']), 1)

    def test_threshold(self):
        code, output = self.run_cli(str(FIXTURES / 'testonly'), '--fail-on', 'HIGH', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['findings'][0]['severity'], 'MEDIUM')

    def test_ignore(self):
        self.assertEqual(len(audit(FIXTURES / 'bad/notsafe', ['main.py'])['findings']), 1)
        self.write('README.md', 'is thread safe')
        self.write('main.py', 'x = 1')
        self.write('guard.py', 'x.acquire()')
        self.assertEqual(audit(self.root)['findings'], [])
        self.assertEqual(audit(self.root, ['guard.py'])['findings'][0]['rule'], 'documented-thread-safe-claim-false')

    def test_no_source_both(self):
        self.write('README.md', 'is thread safe. It is not thread safe.')
        findings = audit(self.root)['findings']
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f['severity'] == 'HIGH' and f['evidence'] == [{'kind': 'no-source'}] for f in findings))

    def test_both_independent_testonly(self):
        self.write('README.md', 'is thread safe. It is not thread safe.')
        self.write('main.py', 'x=1')
        self.write('tests/test_guard.py', 'lock.acquire()')
        findings = audit(self.root)['findings']
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f['severity'] == 'MEDIUM' for f in findings))

    def test_notsafe_testonly(self):
        self.write('README.md', 'is not thread safe')
        self.write('main.py', 'x=1')
        self.write('fixtures/a.py', 'lock.acquire()')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-not-thread-safe-claim-false-test-only')

    def test_no_claim(self):
        self.write('main.py', 'lock.acquire()')
        self.assertEqual(audit(self.root), {'findings': [], 'verified_claims': []})

    def test_docs_only_sources(self):
        self.write('README.md', 'No promises.')
        self.write('docs/nested/guide.md', 'is thread-safe')
        self.write('OTHER.md', 'is not thread-safe')
        self.write('main.py', 'x=1')
        result = audit(self.root)
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['claims'][0]['path'], 'docs/nested/guide.md')

    def test_exclusions(self):
        self.write('README.md', 'is thread safe')
        self.write('main.py', 'x=1')
        for path in ('docs/guard.py', 'examples/guard.py'):
            self.write(path, 'lock.acquire()')
        self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_test_patterns(self):
        for path in ('test_guard.py', 'guard_test.py', 'nested/tests/guard.py', 'fixtures/guard.py'):
            with self.subTest(path=path):
                self.write(path, 'lock.acquire()')
                self.write('main.py', 'x=1')
                self.write('README.md', 'is thread safe')
                self.assertEqual(audit(self.root)['findings'][0]['severity'], 'MEDIUM')
                (self.root / path).unlink()

    def test_symlinks(self):
        self.write('README.md', 'is thread safe')
        self.write('main.py', 'x=1')
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other)
            (outside / 'guard.py').write_text('lock.acquire()')
            (self.root / 'guard.py').symlink_to(outside / 'guard.py')
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
            self.assertEqual(audit(self.root)['findings'][0]['severity'], 'HIGH')

    def test_env_never_opened(self):
        marker = 'ENV_MARKER_MUST_NOT_LEAK'
        self.write('.env', marker)
        self.write('README.md', 'is thread safe')
        self.write('main.py', 'x=1')
        original = Path.open
        opened = []
        def guarded(path, *args, **kwargs):
            opened.append(path.name)
            if path.name == '.env':
                raise AssertionError('opened .env')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            code, output = self.run_cli(str(self.root), '--json')
        self.assertEqual(code, 2)
        self.assertNotIn('.env', opened)
        self.assertNotIn(marker, output)

    def test_all_evidence(self):
        self.write('README.md', 'is not thread safe. It is not thread safe.')
        self.write('main.py', 'import threading\nlock=threading.Lock()\nwith lock:\n lock.acquire()')
        result = audit(self.root)['findings']
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['evidence']), 3)
        self.assertEqual(len(result[0]['claims']), 2)

    def test_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exc:
            main(['--version'])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), '0.1.0')

    def test_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(['--unknown'])
        self.assertEqual(exc.exception.code, 1)

    def test_invalid_tree(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.root / 'missing')]), 1)

    def test_bad_source(self):
        self.write('main.py', 'def !!!')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.root)]), 1)

    def test_verbose(self):
        code, output = self.run_cli(str(FIXTURES / 'good/threadsafe'), '--verbose')
        self.assertEqual(code, 0)
        self.assertIn('verified: threadsafe', output)


if __name__ == '__main__':
    unittest.main()
