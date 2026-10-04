"""Create the local-install kit from an already-built Arena wheel.

The kit contains no runtime workspace or provider credentials. Dependencies are
resolved by uv during installation; the app itself uses bundled local UI assets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package(wheel: Path, destination: Path) -> dict:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError("a built .whl file is required")
    files = {
        wheel.name: wheel,
        "install_local.py": ROOT / "tools/install_local.py",
        "simulation_adapter.py": ROOT / "tools/simulation_adapter.py",
        "arena_adapter.py": ROOT / "tools/arena_adapter.py",
        "docs/install.md": ROOT / "docs/install.md",
        "docs/integration.md": ROOT / "docs/integration.md",
        "docs/design.md": ROOT / "docs/design.md",
    }
    content = {name: path.read_bytes() for name, path in files.items()}
    content["START-HERE.md"] = f"""# Install Arena locally

Arena is a local workspace for synthetic simulation sessions. This kit includes
the application wheel, a Python installer, and an example integration client.
The app bundles its browser UI, fonts, and two recorded reference sessions.

Install Python 3.11+ and uv (https://docs.astral.sh/uv/), extract this kit, and run:

```sh
python3 install_local.py --wheel {wheel.name} --destination ./arena-local
python3 arena-local/start.py
```

Open http://127.0.0.1:8767. No model key, Node build, or native compiler is needed
for the reference sessions and external simulation adapters. The first install
needs internet access to obtain Python dependencies. Subsequent local use does
not fetch UI assets from external services.

In Connections, create a receiver. The one-time token belongs in the adapter,
not in a model prompt. Use the integration example to send a synthetic stream:

```sh
python3 arena-local/integrations/simulation_adapter.py --help
```

Read docs/install.md for workspace, upgrade, and optional C++ instructions.
Read docs/integration.md for the event contract and supported boundaries.
macOS is the tested release platform; see the validation report in the source
release for exact checks. This is a local single-user tool, not a hosted service.

MANIFEST.json contains SHA-256 hashes of every file in this kit except itself.
""".encode()
    manifest = {
        "format": "arena-local-install-kit-v1",
        "files": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(content.items())
        ],
    }
    content["MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(content.items()):
            archive.writestr("arena-local/" + name, data)
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError("kit archive failed its integrity check")
    temporary.replace(destination)
    receipt = {
        "file": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "files": len(content),
    }
    destination.with_suffix(".json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.wheel, args.output)))


if __name__ == "__main__":
    main()
