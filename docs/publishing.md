# Publishing rosa-chunking

No account, repository, Trusted Publisher or upload is created by installing this source tree.
The release workflow is manual and requires a tagged commit plus your account configuration.

## Before the first upload

1. Push the reviewed source and `.github/workflows/` to
   [longjl1/rosa-chunking](https://github.com/longjl1/rosa-chunking).
   This repository is recorded in the package metadata.
2. Create/verify your accounts on both [TestPyPI](https://test.pypi.org/account/register/) and
   [PyPI](https://pypi.org/account/register/), including their required account security setup.
3. Check `rosa-chunking` on both indexes. Availability is not reservation. If registration is
   rejected, choose a new distribution name and update metadata, workflow commands and docs together.
4. Create GitHub environments named **testpypi** and **pypi**. Set reviewers for the pypi environment
   if you want an explicit approval before each production upload. Restrict deployments to release tags.
5. On each index configure a pending Trusted Publisher for a new project (or a normal publisher for
   a project you own). Enter your actual GitHub owner, repository, workflow filename **publish.yml**,
   and corresponding environment **testpypi** or **pypi**. Both project names are `rosa-chunking`.
   TestPyPI and PyPI are separate accounts/projects/publisher configurations.

For this repository, the first-release publisher fields are:

| Field | TestPyPI | PyPI |
| --- | --- | --- |
| Project name | `rosa-chunking` | `rosa-chunking` |
| Owner | `longjl1` | `longjl1` |
| Repository | `rosa-chunking` | `rosa-chunking` |
| Workflow filename | `publish.yml` | `publish.yml` |
| Environment | `testpypi` | `pypi` |

Configure new-project publishers under each account's **Publishing** page.

Official instructions: [Trusted Publishers](https://docs.pypi.org/trusted-publishers/)
and [GitHub publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
OIDC uses short-lived credentials; the workflow does not require a long-lived PyPI upload secret.

## Validate locally

Use Python 3.11+ in a venv:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check .
mypy
python examples/six_strategies.py
python benchmarks/run.py --repeat 3 --output benchmark-results.json
python -m build
python -m twine check dist/*
python scripts/verify_distributions.py
```

Use a dist directory containing only this release. `verify_distributions.py` inspects packaged code,
type markers and license, installs the wheel into a clean venv without dependencies, rebuilds a wheel
from the extracted sdist, and independently installs/tests that wheel. It runs outside the checkout.

The build follows the [PyPA packaging tutorial](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

## Release through GitHub Actions

1. Keep the single version source in `pyproject.toml`; initial alpha is `0.1.0a1`.
   Update CHANGELOG's unreleased entry when making an actual release.
2. Commit and tag the exact reviewed source, for example `v0.1.0a1`, and push that tag.
3. In Actions run **Publish**, select tag `v0.1.0a1` under **Use workflow from**, and enter version
   `0.1.0a1`. Choose `test_only=true` for a rehearsal;
   choose `test_only=false` for the full TestPyPI → verification → PyPI sequence.
4. The workflow checks that tag and package versions agree, runs tests/lint/types, builds artifacts
   once, and verifies core/provider wheel installations on Python 3.11–3.14.
5. It uploads to TestPyPI, compares both remote artifact hashes with the verified build, installs
   the test wheel using `--no-deps`, and runs the installed-package smoke test.
6. For a full release, it publishes the same immutable workflow artifacts to PyPI, checks hashes
   and performs another independent installed-package smoke test. Downloadable Actions artifacts
   are retained for review. Local builds do not count as evidence of these remote checks passing.

The workflow uses `skip-existing` for resuming an interrupted run; hash verification still fails
if an index contains different bytes for that filename. Run again from the **same immutable tag**;
never move a release tag. A full release can follow a test-only rehearsal from that same tag.
If a rebuilt artifact differs from an already uploaded file, bump the package version and create
a new tag. PyPI does not support replacing a previously uploaded distribution filename.

## Manual TestPyPI alternative

If Trusted Publishing is unavailable, upload with Twine using credentials managed by your own
environment/keyring. Never put upload credentials in the repository:

```bash
python -m twine upload --repository testpypi dist/*
python scripts/verify_index.py --index testpypi --version 0.1.0a1
python -m venv /tmp/rosa-test-install
/tmp/rosa-test-install/bin/python -m pip install httpx
/tmp/rosa-test-install/bin/python -m pip install --index-url https://test.pypi.org/simple/ --no-deps 'rosa-chunking==0.1.0a1'
/tmp/rosa-test-install/bin/python -I scripts/smoke_install.py
```

Dependencies come from the normal PyPI index first. Do not mix indexes with `--extra-index-url` for
this validation. Only after successful checks, upload the **same dist files** to production:

```bash
python -m twine upload dist/*
python scripts/verify_index.py --index pypi --version 0.1.0a1
```

Create another clean venv and install the exact production version for the final smoke test.

## Later versions

Alpha users install with `--pre` or an exact prerelease pin. After all six strategies pass acceptance
and the API is stable, release `0.1.0` through the same process. Publish new versions for fixes;
document changes to serialized chunk contracts and defaults. No release is triggered by a normal
commit or pull request.
