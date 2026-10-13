# Contributing to Blendkit for Unreal

Thanks for helping port Blendkit to Unreal Engine! This project mirrors the
conventions of the Maya plugin ([`bk_maya`](../bk_maya)) so the two ports stay
easy to cross-reference.

## Ground rules

- **Keep `core/` engine-agnostic.** Modules under
  `Content/Python/bk_unreal/core/` (and `api/`) must not `import unreal` or
  depend on a UI toolkit, so they stay unit-testable and reusable.
  Do Unreal imports lazily inside functions (see `editor_thread.py`,
  `unreal_plugin.py`).
- **Everything routes through the Go client for heavy work** (search,
  thumbnails, downloads). Do not add direct blendkit.com calls for those —
  extend `core/client_lib.py` instead. Direct REST is only for OAuth/profile in
  `api/client.py`.
- **Never block the editor thread.** Network work runs on the client's poll
  thread; deliver results to Slate with `editor_thread.run_on_editor_thread`.
- **One version source of truth:** `Content/Python/bk_unreal/_version.py`
  (`BASE_VERSION`). Everything else is generated at build time.

## Dev setup

See the "Getting started" and "Live-code testing" sections in
[README.md](README.md). In short:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python dev.py vendor
pre-commit install
```

## Before you push

```powershell
ruff format .
ruff check .
pydoclint .
bandit -c _bandit.yaml -r .
python -m pytest
```

Or just let `pre-commit` run them on commit.

## Coding style

- Python ≥ 3.11 target, but `from __future__ import annotations` at the top of
  every module (Ruff `FA` rules enforce it) so annotations stay string-based.
- Google-style docstrings on public modules/classes/functions where they add
  value (see `pyproject.toml` ignores for what's relaxed).
- 120-char lines, formatted by Ruff.

## Reporting issues

Use the [issue tracker](https://github.com/BlenderKit/bk_unreal/issues).
