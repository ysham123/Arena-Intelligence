"""Install Arena in a fresh local directory without changing other environments.

Example: python3 install_local.py --wheel arena_intelligence-0.2.0-py3-none-any.whl \
    --destination ./arena-local
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


class InstallError(RuntimeError):
    pass


def executable(environment: Path, name: str) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / (name + ".exe")
    return environment / "bin" / name


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    artifact = parser.add_mutually_exclusive_group(required=True)
    artifact.add_argument("--wheel", type=Path, help="downloaded Arena .whl")
    artifact.add_argument("--source", type=Path, help="extracted source directory")
    parser.add_argument("--destination", type=Path, required=True, help="new installation folder")
    parser.add_argument("--python", default="3.13", help="Python 3.11+ path/version for uv")
    parser.add_argument("--uv", type=Path, help="uv executable if it is not on PATH")
    parser.add_argument(
        "--with-native", action="store_true", help="also compile the optional C++ engine"
    )
    parser.add_argument(
        "--native-source", type=Path, help="source tree for --with-native with a wheel"
    )
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--start", action="store_true", help="start in the foreground after installation"
    )
    return parser.parse_args(argv)


def run(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode:
        raise InstallError(f"installation step exited with code {result.returncode}")


def write_launcher(directory: Path, python: Path, port: int, engine: Path | None = None) -> Path:
    command = [
        str(python),
        "-m",
        "arena_intelligence",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--data-dir",
        str(directory / "workspace"),
    ]
    if engine is not None:
        command.extend(["--engine", str(engine)])
    launcher = directory / "start.py"
    launcher.write_text(
        '#!/usr/bin/env python3\n"""Start the installed local Arena workspace."""\n'
        "import os\nimport sys\n"
        f"command = {command!r}\n"
        f"os.chdir({str(directory)!r})\n"
        "os.execv(command[0], command + sys.argv[1:])\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    return launcher


def install(args) -> dict:
    artifact = (args.wheel or args.source).expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    if args.wheel and (not artifact.is_file() or artifact.suffix != ".whl"):
        raise InstallError("--wheel must point to an existing .whl file")
    if args.source and (not artifact.is_dir() or not (artifact / "pyproject.toml").is_file()):
        raise InstallError("--source must contain pyproject.toml")
    if destination.exists():
        raise InstallError("destination already exists; choose a new installation folder")
    if not 1 <= args.port <= 65535:
        raise InstallError("port must be between 1 and 65535")
    uv = str(args.uv.expanduser().resolve()) if args.uv else shutil.which("uv")
    if not uv or not Path(uv).is_file():
        raise InstallError(
            "uv is required; see https://docs.astral.sh/uv/getting-started/installation/"
        )
    native_source = args.native_source or (artifact if args.source else None)
    native_source = native_source.expanduser().resolve() if native_source else None
    if args.with_native and (
        native_source is None or not (native_source / "CMakeLists.txt").is_file()
    ):
        raise InstallError("--with-native requires --source or --native-source with CMakeLists.txt")
    if args.with_native and os.name == "nt":
        raise InstallError("the optional native engine currently supports macOS and Linux")
    cmake = shutil.which("cmake") if args.with_native else None
    if args.with_native and not cmake:
        raise InstallError("CMake 3.24+ is required for --with-native")
    destination.mkdir(parents=True, mode=0o700)
    environment = destination / "venv"
    python = executable(environment, "python")
    receipt = {
        "schema_version": 1,
        "installed_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - bootstrap supports Python 3.9
        "artifact": str(artifact),
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
        if args.wheel
        else None,
        "installation_directory": str(destination),
        "python_environment": str(environment),
        "workspace_directory": str(destination / "workspace"),
        "url": f"http://127.0.0.1:{args.port}",
        "native_engine": None,
        "status": "installing",
    }
    receipt_path = destination / "installation.json"
    try:
        run([uv, "venv", "--python", args.python, str(environment)])
        run([uv, "pip", "install", "--python", str(python), str(artifact)])
        run(
            [
                str(python),
                "-c",
                "from arena_intelligence import __version__; print('Arena', __version__)",
            ]
        )
        (destination / "workspace").mkdir(mode=0o700)
        launcher = write_launcher(destination, python, args.port)
        # The adapter examples remain usable independently of Python package installation.
        integrations = destination / "integrations"
        integrations.mkdir()
        for name in ("simulation_adapter.py", "arena_adapter.py"):
            example = Path(__file__).resolve().parent / name
            if example.is_file():
                shutil.copy2(example, integrations / name)
        if args.with_native:
            build = destination / "build" / "release"
            run(
                [
                    cmake,
                    "-S",
                    str(native_source),
                    "-B",
                    str(build),
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_TESTING=OFF",
                ]
            )
            run([cmake, "--build", str(build), "--parallel", "2"])
            engine = build / ("arena-sim.exe" if os.name == "nt" else "arena-sim")
            if not engine.is_file():
                raise InstallError("native build did not produce arena-sim")
            receipt["native_engine"] = str(engine)
            launcher = write_launcher(destination, python, args.port, engine)
        receipt.update(status="installed", launcher=str(launcher))
    except (OSError, InstallError) as error:
        receipt.update(status="failed", failure=type(error).__name__)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        raise
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None) -> None:
    parser_args = options(argv)
    try:
        receipt = install(parser_args)
    except (OSError, InstallError) as error:
        raise SystemExit(f"Installation stopped: {error}") from None
    print(json.dumps(receipt, indent=2))
    start_command = [sys.executable, receipt["launcher"]]
    printable = (
        subprocess.list2cmdline(start_command) if os.name == "nt" else shlex.join(start_command)
    )
    print(f"Start: {printable}", flush=True)
    if parser_args.start:
        os.execv(sys.executable, [sys.executable, receipt["launcher"]])


if __name__ == "__main__":
    main()
