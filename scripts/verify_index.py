"""Check the index advertises the exact uploaded artifact hashes. Never publishes anything."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", choices=["testpypi", "pypi"], required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    host = "test.pypi.org" if args.index == "testpypi" else "pypi.org"
    url = f"https://{host}/pypi/rosa-chunking/{quote(args.version, safe='')}/json"
    for attempt in range(6):
        try:
            with urlopen(url, timeout=30) as response:
                data = json.load(response)
            break
        except HTTPError as error:
            if error.code != 404 or attempt == 5:
                raise
            time.sleep(5)
    remote = {entry["filename"]: entry["digests"]["sha256"] for entry in data["urls"]}
    artifacts = list(args.dist.glob("*.whl")) + list(args.dist.glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise ValueError("Expected one wheel and one sdist")
    for path in artifacts:
        if remote.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"Remote artifact mismatch: {path.name}")
    print(f"{host}: both artifact hashes match the locally verified build")


if __name__ == "__main__":
    main()
