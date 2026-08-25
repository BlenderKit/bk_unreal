<div align="center">
  <h3 align="center">Blendkit for Unreal Engine</h3>

  Asset search, download and drag&drop directly inside Unreal Engine 5.6.
</div>

> **Status:** early scaffold / **pre-alpha**. This repository is a fresh port of
> the Blendkit Blender add-on and the [Maya plugin (`bk_maya`)](../bk_maya) to
> Unreal Engine's editor Python API. The plumbing (plugin discovery, menu,
> Qt-in-editor host, Go-client integration, dev tooling) is in place; the asset
> bar UI is intentionally minimal and grows from here.

## About

The plugin connects Unreal Engine to the [Blendkit service](https://www.blendkit.com/)
— search the library, browse thumbnails, and (soon) drag&drop assets straight
into the level. It reuses the same account / Full plan as the Blender add-on.

It is built on:

- **Unreal Engine 5.6** editor Python API (Python 3.11), `unreal.ToolMenus` for
  menus, and a Slate post-tick pump so a **Qt** (PySide6 via `qtpy`) asset bar
  can live inside the editor without blocking it.
- The shared Go **`blendkit-client`** for search, auth, thumbnails and
  downloads (embedded as the `bk_client` git submodule).
- Vendored `qtpy` / `requests` / `packaging` under
  [Content/Python/bk_unreal/lib](Content/Python/bk_unreal/lib) (git-ignored,
  populated by `python dev.py vendor`).

## Repository layout

```
Blendkit.uplugin                     Unreal plugin descriptor (repo root = plugin root)
bk_client/                             Go `blendkit-client` (git submodule, shared with the Blender add-on)
Content/Python/
  init_unreal.py                       UE auto-run entry point → bootstrap + register
  bk_unreal/                           the Python package
    _version.py                        single source of truth for the version
    unreal_plugin.py                   register()/unregister(): logging + ToolMenus
    api/client.py                      direct Blendkit REST calls (OAuth, profile)
    core/                              engine-agnostic logic (no `unreal` import)
      global_vars.py  log.py  prefs.py
      client_lib.py                    Go-client spawn / port discovery / search / report poll
      search.py                        query construction
      qt_host.py                       QApplication + Slate tick pump
    ui/asset_bar.py                    Qt search window (hosted in the editor)
    bk_proxor/                         mesh-preview submodule (.prx / .prxc), shared with bk_maya
    lib/                               vendored qtpy/requests (git-ignored)
dev.py                                 vendor / build / stamp tasks
.vscode/unreal_plugin.py               junction + Python-path registration for live dev
.vscode/{settings,launch,extensions}.json, pdm_setup_project.{bat,sh}
tests/                                 pure-Python tests (no Unreal/Qt needed)
```

## Getting started (developers)

```powershell
# 1. clone with submodules (bk_client)
git clone --recursive https://github.com/BlenderKit/bk_unreal.git
cd bk_unreal

# 2. one-shot setup: venv + dev tools + vendor Qt + pre-commit
#    (VS Code: Run and Debug ▸ "Run Project Setup", or:)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"   # or: pdm install --group dev
python dev.py vendor
pre-commit install

# 3. run the tests
python -m pytest
```

### Live-code testing inside Unreal

Two mechanisms (use either or both) point a real UE project at this working copy
so your edits are live. Both run through `.vscode/unreal_plugin.py`, and both are
available as VS Code launch configs (they prompt for the `.uproject` path):

1. **Junction (real plugin):** links this repo into
   `<Project>/Plugins/Blendkit`, so Unreal loads it as a plugin.

   ```powershell
   python .vscode/unreal_plugin.py junction --project "D:/UE/MyGame/MyGame.uproject"
   ```

2. **Python path (no install):** adds `Content/Python` to the project's
   `Config/DefaultEngine.ini` `AdditionalPaths` and enables Developer Mode.

   ```powershell
   python .vscode/unreal_plugin.py pythonpath --project "D:/UE/MyGame/MyGame.uproject"
   ```

Restart the editor. Unreal runs `Content/Python/init_unreal.py`, which builds the
**Blendkit** menu. **Blendkit ▸ Open Asset Bar** opens the Qt search window.

> During dev, re-run logic without restarting the editor with Unreal's Python
> console:
> ```python
> import importlib, bk_unreal.unreal_plugin as p
> p.unregister(); importlib.reload(p); p.register()
> ```

To undo: `python .vscode/unreal_plugin.py remove --project "..."` (or
`unpythonpath`).

## Building a distributable

```powershell
python dev.py build          # dev channel  -> out/Blendkit-0.1.<stamp>.zip
python dev.py build --channel alpha
python dev.py build --channel stable
```

The zip contains the plugin under a top-level `Blendkit/` folder; drop it into
a project's `Plugins/` directory.

## The `bk_client` and `bk_proxor` submodules

Two Blendkit repos are embedded as git submodules, matching `bk_maya`:

- **`bk_client/`** — the Go `blendkit-client`, shared with the Blender add-on.
- **`Content/Python/bk_unreal/bk_proxor/`** — the `.prx` / `.prxc` mesh-preview
  library, shared with `bk_maya`.

They are already wired into `.gitmodules`. After cloning, pull their contents:

```powershell
git submodule update --init --recursive
```

`python dev.py build` (and the junction/pythonpath dev commands) build the local
unsigned client from the `bk_client` submodule into `client/vX.Y.Z/`, the layout
[core/client_lib.py](Content/Python/bk_unreal/core/client_lib.py) scans at runtime.

## Quality

| Check      | Local                              |
|------------|------------------------------------|
| Lint       | `ruff check .`                     |
| Format     | `ruff format --check .`            |
| Docstrings | `pydoclint .`                      |
| Security   | `bandit -c _bandit.yaml -r .`      |
| Tests      | `python -m pytest`                 |

All are wired as [pre-commit](https://pre-commit.com) hooks — see
[.pre-commit-config.yaml](.pre-commit-config.yaml).
