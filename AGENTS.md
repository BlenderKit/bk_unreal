# AGENTS.md — bk_unreal

Guidance for AI coding agents working in this repository. (VS Code Copilot also
reads [.github/copilot-instructions.md](.github/copilot-instructions.md), which
contains the full detail; this file is the short version for other agents.)

## What this is

A port of the **Blendkit** Blender add-on and the Maya plugin (`../bk_maya`) to
**Unreal Engine 5.6** using the editor **Python** API. The repo root *is* the
Unreal plugin (`Blendkit.uplugin`). Reference `../bk_maya` and
`../blenderkit_addon` for how any feature is solved upstream.

## Where things live

- `Content/Python/init_unreal.py` — Unreal auto-runs this at editor startup;
  it bootstraps `sys.path` and calls `bk_unreal.unreal_plugin.register()`.
- `Content/Python/bk_unreal/` — the package: `unreal_plugin.py` (ToolMenus menu),
  `core/` (engine-agnostic: `client_lib`, `search`, `editor_thread`, `global_vars`,
  `log`, `prefs`), `api/client.py` (REST), `ui_native/` (native Slate UI glue).
- `bk_client/` — Go client (submodule). `Content/Python/bk_unreal/bk_proxor/` —
  mesh-preview lib (submodule). Run `git submodule update --init --recursive`.
- `dev.py` — `vendor` / `build` / `stamp`. `.vscode/unreal_plugin.py` — live-dev
  registration into a UE project (`junction` or `pythonpath`).

## Rules

1. `from __future__ import annotations` in every module.
2. `core/` + `api/` never import `unreal` at top level (keeps tests
  headless). Import lazily. UI is native Slate; do not add Qt dependencies.
3. Search/thumbnail/download → the Go client (`core/client_lib.py`), not direct
  REST. Never block the editor thread; use `editor_thread.run_on_editor_thread`.
4. Version only in `_version.py`.

## Build & test

- Setup: `python -m pip install -e ".[dev]"` then `python dev.py vendor` then
  `pre-commit install`.
- Tests (headless): `python -m pytest`.
- Live in Unreal: `python .vscode/unreal_plugin.py junction --project <uproject>`,
  restart the editor, then **Blendkit ▸ Open Asset Bar**.
- Quality gates: `ruff check .`, `ruff format --check .`, `pydoclint .`,
  `bandit -c _bandit.yaml -r .`.

## Roadmap

Thumbnail-grid polish → OAuth login → download+import to the Content Browser with
drag&drop placement → prefs surface → CI. See `.github/copilot-instructions.md`.
