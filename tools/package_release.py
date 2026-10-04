"""Package source/demo and completed experiment records with hashes and a secret scan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {
    ".github",
    "assets",
    "configs",
    "docs",
    "examples",
    "frontend",
    "native",
    "reports",
    "src",
    "tests",
    "tools",
}
TOP_FILES = {
    ".clang-format",
    ".gitattributes",
    ".env.example",
    ".gitignore",
    "CMakeLists.txt",
    "README.md",
    "pyproject.toml",
    "uv.lock",
}
IGNORED = {
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".DS_Store",
    ".playwright-cli",
    "node_modules",
    ".vite",
    ".cache",
}
SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".wal", ".shm"}


def private_key() -> bytes | None:
    value = os.environ.get("ANTHROPIC_API_KEY")
    if not value and (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.partition("=")[2].strip().strip("\"'")
    return value.encode() if value else None


def permitted(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return (
        not any(part in IGNORED for part in relative.parts)
        and path.suffix not in SUFFIXES
        and not (path.name.startswith(".env") and path.name != ".env.example")
        and not path.is_symlink()
    )


def source_paths() -> list[Path]:
    paths = [ROOT / name for name in TOP_FILES]
    for name in DIRECTORIES:
        for directory, children, files in os.walk(ROOT / name):
            children[:] = [child for child in children if child not in IGNORED]
            paths.extend(Path(directory) / filename for filename in files)
    return sorted(
        path
        for path in paths
        if path.is_file()
        and permitted(path)
        and path.name not in {"release-manifest.json", "experiment-records-manifest.json"}
    )


def record_paths() -> list[Path]:
    roots = [
        ROOT / "runs" / name
        for name in (
            "heldout-final",
            "heldout-claude",
            "matched-final",
            "claude-pilot",
            "compact-pilot-single",
            "compact-pilot-multi",
        )
    ]
    latency_roots = sorted((ROOT / "runs").glob("latency-*"))
    if latency_roots:
        roots.append(latency_roots[-1])
    paths = []
    for root in roots:
        if not root.is_dir():
            raise ValueError("expected experiment directory is missing")
        paths.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and permitted(path) and path.name != "replay.html"
        )
    return sorted(paths)


def write_manifest(files: list[Path], name: str, key: bytes | None) -> Path:
    records = []
    for path in files:
        data = path.read_bytes()
        if key and key in data:
            raise ValueError("credential detected; packaging stopped")
        records.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    value = {
        "file_count": len(records),
        "total_bytes": sum(r["bytes"] for r in records),
        "known_credential_scan": "passed" if key else "no credential available to scan",
        "self_hash": "manifest excludes its own hash; archive hash covers the manifest",
        "files": records,
    }
    path = ROOT / "reports" / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)
    return path


def archive(files: list[Path], destination: Path) -> dict:
    temporary = destination.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in files:
            output.write(path, "arena-intelligence/" + str(path.relative_to(ROOT)))
    with zipfile.ZipFile(temporary) as result:
        if result.testzip() is not None:
            raise ValueError("archive integrity check failed")
    temporary.replace(destination)
    return {
        "file": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "files": len(files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiments", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT.parent)
    args = parser.parse_args()
    if args.experiments:
        matched = json.loads((ROOT / "reports" / "matched-final.json").read_text())
        if not matched["full_4_seed_3_policy_2_side_matrix"]:
            raise ValueError("full matched benchmark must finish before packaging")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    key = private_key()
    outputs = []
    if args.experiments:
        records = record_paths()
        manifest = write_manifest(records, "experiment-records-manifest.json", key)
        outputs.append(
            archive(records + [manifest], args.output_dir / "arena-intelligence-experiments.zip")
        )
    files = source_paths()
    experiment_manifest = ROOT / "reports" / "experiment-records-manifest.json"
    if experiment_manifest.is_file():
        files.append(experiment_manifest)
    manifest = write_manifest(files, "release-manifest.json", key)
    outputs.append(archive(files + [manifest], args.output_dir / "arena-intelligence-source.zip"))
    receipt = {"archives": outputs, "credentials_included": False}
    (args.output_dir / "arena-intelligence-release.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
