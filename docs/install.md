# Install the local workspace

Arena runs on your computer and opens in a browser at `http://127.0.0.1:8767`. Recorded sessions and external simulation connections work without the C++ compiler. The native arena is an optional source build.

Download `arena-intelligence-local-0.2.0.zip` and extract it. This local install kit contains the release wheel, installer, adapter examples, and these docs. Install [uv using its official instructions](https://docs.astral.sh/uv/getting-started/installation/). The installer can start with Python 3.9+ and asks uv for Python 3.13; the application requires Python 3.11+. uv can select an existing interpreter or download a managed one.

From the extracted install kit folder:

```sh
python3 install_local.py \
  --wheel ./arena_intelligence-0.2.0-py3-none-any.whl \
  --destination ../arena-local \
  --start
```

The kit's `START-HERE.md` has the same launch command. The wheel includes the browser application, fonts, and recorded reference sessions; you do not need the source or experiment archives for this installation.

Open `http://127.0.0.1:8767`. The foreground terminal runs the server; press Ctrl+C there to stop it. Start it again from any folder with:

```sh
python3 /path/to/arena-local/start.py
```

The installer creates its own Python environment and a writable workspace in the selected directory. It does not change your existing environments or shell profile. It refuses an existing destination, including one from a failed install, so a retry should use a new folder. `installation.json` records the wheel hash, installed locations, launcher, and completion status. If you need another port, pass `--port 8770` to the installer.

The folder contains:

| Path | Purpose |
| --- | --- |
| `venv/` | Application and its Python dependencies |
| `workspace/` | Local connections, sessions, and records |
| `start.py` | Foreground server launcher |
| `integrations/` | Standard-library adapter client and synthetic sample |
| `installation.json` | Installation receipt |
| `build/release/arena-sim` | Optional native engine, if requested |

Package downloads require network access during installation. Opening recorded sessions and receiving local synthetic telemetry make no model calls. The server binds to a loopback address.

## Connect a simulation

Keep the workspace running, then use another terminal:

```sh
python3 /path/to/arena-local/integrations/simulation_adapter.py
```

The sample creates a simulation connection and streams 24 seconds of scripted positions, delayed evidence, explicit synthetic assessments, and metrics. Open the resulting session in the workspace. The sample finishes its session automatically and prints the session ID without printing its integration token. See [the integration contract](integration.md) to connect your own simulation.

## Wheel and source alternatives

If you downloaded a standalone wheel, run the installer from the source toolkit with `--wheel /path/to/arena_intelligence-0.2.0-py3-none-any.whl`. The toolkit supplies the adapter examples that the installer copies into `integrations/`.

From the extracted source directory:

```sh
python3 tools/install_local.py --source . --destination ../arena-local --start
```

This installs a regular package copy in a new environment. Source changes require a fresh installation; the installed application does not depend on your current terminal directory.

To also enable the native game on macOS or Linux, install a C++20 compiler and CMake 3.24+, then use:

```sh
python3 tools/install_local.py \
  --source . --destination ../arena-native --with-native --start
```

The installer compiles the engine into the installation folder and passes its absolute path to the server. CMake fetches the pinned native JSON dependency during configuration. The wheel does not contain a platform-specific C++ executable. With a wheel, `--with-native` also requires `--native-source /path/to/source`.

To use an existing Python interpreter or a uv executable outside PATH:

```sh
python3 install_local.py \
  --wheel ./arena_intelligence-0.2.0-py3-none-any.whl \
  --destination ./arena-local \
  --python /path/to/python3.13 \
  --uv /path/to/uv
```

If the browser cannot reach the workspace, check the foreground server terminal and the port in `installation.json`. `GET /api/health` returns readiness once the server is listening. Keep the installation directory when upgrading if you want to retain its workspace: install the new release into a separate folder, then run `python3 /path/to/new-arena-local/start.py --data-dir /path/to/old/arena-local/workspace` to select the existing workspace explicitly. Copying a workspace while the old server is stopped preserves its local files.

uv's [environment documentation](https://docs.astral.sh/uv/pip/environments/) explains the isolated Python environment used by the installer.
