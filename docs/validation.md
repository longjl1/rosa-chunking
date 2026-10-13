# Local validation — 2026-10-13

Validated on macOS arm64 with Python **3.12.14** in an isolated project venv.

| Check | Result |
| --- | --- |
| Unit/property-style and HTTP mock tests | 149 passed |
| Real-provider integration test | 1 skipped; no real provider requests made |
| Ruff lint and formatting | Passed |
| Mypy strict check | Passed, 10 library source files |
| Six-strategy offline example | Passed |
| Bilingual mechanics benchmark | All six strategies retained complete coverage |
| Isolated `python -m build` | Wheel and sdist generated |
| `twine check` | Both distributions passed |
| Clean wheel install, outside checkout | Six strategies and serialization passed; no core HTTP dependency |
| Wheel rebuilt from sdist and independently installed | Passed |
| GitHub workflow validation | actionlint 1.7.12 passed for CI and Publish |

The benchmark uses original MIT sample text: 15,020 characters (20 copies of the fixture),
three elapsed-time samples per strategy, local deterministic embedding/LLM stubs.
It measures mechanics and local overhead; it does not measure live model performance or retrieval accuracy.
Re-run `python benchmarks/run.py --repeat 3 --output benchmark-results.json` for the JSON report.

Python 3.11, 3.13 and 3.14 are included in the GitHub installation/test matrix but were **not run locally**.
Both core and providers installation variants are configured. Actual GitHub CI awaits a pushed repository.

**External release status:** no GitHub remote, tag, release, PyPI project, Trusted Publisher or upload
was created in this session. The distribution is a local `0.1.0a1` alpha. Account/repository binding
and real TestPyPI/PyPI installation verification remain release steps in [publishing.md](publishing.md).
