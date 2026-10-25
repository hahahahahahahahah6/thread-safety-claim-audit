# thread-safety-claim-audit

Offline audit of documented thread-safety claims against Python source.

Version 0.1.0. A standard-library-only CLI for comparing documentation promises
about concurrent safety with static synchronisation evidence. No audited code
is executed, no network is used, and `.env` contents are never opened.

## Usage

Requires Python 3.9 or later. Install and run:

```sh
python3 -m pip install .
thread-safety-claim-audit path/to/project
python3 -m thread_safety_claim_audit path/to/project --json
thread-safety-claim-audit . --verbose --ignore 'vendor/*' --fail-on HIGH
python3 -m thread_safety_claim_audit --version
```

The path defaults to the current directory. `--ignore GLOB` is repeatable and
matches relative paths. `--verbose` includes evidence and verified families.
`--json` emits an object with `findings` and `verified_claims` arrays. Findings
contain `rule`, `severity`, `family`, `claims`, and `evidence`; claim records
contain a path, line, sentence, and family. Evidence records contain a path,
line, and kind, except the `no-source` sentinel. Verified records contain a
family, claims, and evidence. Exit 0 means PASS; exit 2 means findings at or
above the selected threshold; exit 1 means a usage, reading, or parsing error.
The default threshold is MEDIUM. HIGH leaves MEDIUM findings visible while
allowing exit 0.

## Claims

Only root `README.md` and `docs/**/*.md` supply claims. Fenced code blocks are
excluded; inline code counts as prose. Sentences are lowercased, `n't` becomes
` not`, hyphens become spaces, and whitespace collapses. Exact phrases, with
word boundaries, are matched; there is no optional-word expansion.

The complete normalized phrase vocabulary is below:

```text
THREADSAFE:
thread safe
safe for concurrent use
safe to call from multiple threads
safe to use from multiple threads
safe to use concurrently
safe across threads
safe for multithreaded use
safe for multi threaded use
thread safe by design

NOTSAFE:
not thread safe
not safe for concurrent use
not safe to call from multiple threads
not safe to use from multiple threads
not safe to use concurrently
not safe across threads
not safe for multithreaded use
not safe for multi threaded use
no thread safety
must not be called from multiple threads
must not be used from multiple threads
```

NOTSAFE spans are matched and masked first to prevent substring overlap.
Negation words (`no`, `not`, `never`, `without`, including normalized
contractions) suppress THREADSAFE for the whole sentence; listed NOTSAFE
phrases still count. This asymmetry means the following examples behave as
shown:

```text
is not thread-safe       => notsafe only
isn't thread-safe        => notsafe only
never thread safe       => no claim
does not claim to be thread safe => no claim
thread safety           => no claim (bare noun)
threadsafe              => no claim (one word)
```

## Rules

Each family is evaluated independently, including contradictory documentation.
Each finding lists all family claim sentences and all applicable evidence.

```text
| Rule | Severity | Trigger |
| --- | --- | --- |
| `documented-thread-safe-claim-false` | HIGH | THREADSAFE with no production evidence |
| `documented-thread-safe-claim-false-test-only` | MEDIUM | THREADSAFE with evidence only in tests/fixtures |
| `documented-not-thread-safe-claim-false` | HIGH | NOTSAFE with production evidence |
| `documented-not-thread-safe-claim-false-test-only` | MEDIUM | NOTSAFE with evidence only in tests/fixtures |

```

No production Python source produces one HIGH finding per claimed family with
`no-source` evidence. THREADSAFE with production evidence is verified; NOTSAFE
with no evidence anywhere is verified. No claim yields PASS regardless of
source evidence. There are no other reverse rules.

## Named evidence

| Kind | Recognized syntax |
| --- | --- |
| `lock-constructor` | Calls to threading Lock, RLock, Semaphore, BoundedSemaphore, Condition; multiprocessing Lock or RLock |
| `thread-local` | Call to threading.local |
| `acquire` | Attribute call `.acquire(...)` on any receiver |
| `with-lock` | With context Name or Attribute whose name contains `lock`, case-insensitively |

Module and from-import aliases are tracked per file. Bare constructor calls
need a matching import from threading or multiprocessing (multiprocessing
bare imports count only for Lock and RLock, matching the attribute form).
Evidence is deduplicated by file, line, and kind.
Production excludes tests directories, test filename patterns, fixtures,
docs, examples, and symlinked files. Test filename patterns and tests/fixtures
are collected separately; docs and examples never supply evidence. Symlinked
directories are not traversed. Build outputs, virtual environments, and Git
metadata are skipped. Ignored files supply neither claims nor evidence.

## Differentiation

- concurrency-claim-audit checks existence of parallel execution, not synchronisation.
- determinism-claim-audit checks run-to-run stability, not concurrent safety.
- security-claim-audit checks eval, shell, and pickle hazards, not locks.
- bandit and ruff inspect code issues without comparing documentation promises.
- The two PyPI neighbours named below are multi-language concurrency issue
  linters. This tool compares docs promises with source evidence.

```text
thread-safety / thread-safe-check
```
- Python threading documentation describes primitives rather than auditing
  documentation against implementation.

## Honest limitations

This is a static heuristic, not a race detector or proof. Dead code counts:
constructors in uncalled code still supply evidence. Receiver types, import
shadowing, guard coverage, correct lock use, and actual shared-state mutation
are not inferred. Even `.acquire` on a clearly unrelated object counts.
A constructor does not prove that every shared access is guarded.

Bare threading imports and Thread imports provide no evidence. Thread, Pool,
Executor creation and concurrent.futures use concern execution, not protection.
asyncio.Lock is excluded because it is a single-threaded coroutine primitive.
queue.Queue is excluded because its internal protection says nothing about the
surrounding object. A bare Lock name, import, annotation, or attribute access
without a call does not count. threading.Barrier and threading.Event are
coordination primitives rather than mutual exclusion and are excluded.
Dynamic imports and custom guard wrappers may be missed. Exact-phrase claims
miss paraphrases. Markdown and sentence parsing are deliberately lightweight;
claim line numbers identify the start of the containing prose paragraph.
Invalid source or unreadable input results in exit 1 rather than a silent PASS.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

The sdist includes the regression fixtures. Runtime dependencies: none.
