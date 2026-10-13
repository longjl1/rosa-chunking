"""Inspect artifacts and test wheel + rebuilt sdist in fresh, dependency-free venvs."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


def inspect_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "rosa_chunking/py.typed" in names
        assert "rosa_chunking/__init__.py" in names
        assert any(name.endswith("/licenses/LICENSE") for name in names)
        assert not any(name.endswith(".pyc") or "/.env" in name for name in names)


def extract_sdist(path: Path, target: Path) -> Path:
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            destination = (target / member.name).resolve()
            if not destination.is_relative_to(target.resolve()) or not (
                member.isfile() or member.isdir()
            ):
                raise ValueError("Unsafe source archive member")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                assert source is not None
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
    roots = list(target.iterdir())
    assert len(roots) == 1 and (roots[0] / "pyproject.toml").is_file()
    for required in [
        "README.md",
        "LICENSE",
        "src/rosa_chunking/py.typed",
        "tests/test_local.py",
        "docs/publishing.md",
    ]:
        assert (roots[0] / required).is_file(), required
    return roots[0]


def smoke(wheel: Path, workspace: Path, smoke_script: Path, label: str) -> None:
    environment = workspace / label
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
        cwd=workspace,
        env=env,
        check=True,
    )
    copied = workspace / f"smoke_{label}.py"
    shutil.copyfile(smoke_script, copied)
    subprocess.run([str(python), "-I", str(copied)], cwd=workspace, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    dist = args.dist.resolve()
    wheels, sources = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error(
            "Expected exactly one wheel and one sdist in dist; move old releases elsewhere"
        )
    inspect_wheel(wheels[0])
    smoke_script = Path(__file__).resolve().with_name("smoke_install.py")
    with tempfile.TemporaryDirectory(prefix="rosa-distribution-") as directory:
        workspace = Path(directory)
        smoke(wheels[0], workspace, smoke_script, "wheel")
        source_root = extract_sdist(sources[0], workspace / "source")
        rebuilt = workspace / "rebuilt"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(rebuilt),
                str(source_root),
            ],
            cwd=workspace,
            check=True,
        )
        rebuilt_wheel = next(rebuilt.glob("*.whl"))
        inspect_wheel(rebuilt_wheel)
        smoke(rebuilt_wheel, workspace, smoke_script, "sdist")
    print(
        json.dumps(
            {
                "status": "passed",
                "artifacts": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [*wheels, *sources]
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
