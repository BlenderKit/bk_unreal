"""Dev utility: register / unregister this repo as an Unreal plugin.

This is the Unreal counterpart of bk_maya's ``.vscode/maya_module.py``. It wires
the working copy into an Unreal **project** so edits are picked up live, using
either (or both) of two mechanisms:

    python .vscode/unreal_plugin.py junction --project "D:/UE/MyGame/MyGame.uproject"
        Create a directory junction (Windows) / symlink (macOS, Linux) at
        <Project>/Plugins/Blendkit -> this repo, so Unreal loads it as a real
        plugin. Also vendors deps and builds the local client.

    python .vscode/unreal_plugin.py pythonpath --project "..."
        Add <repo>/Content/Python to the project's Python AdditionalPaths in
        Config/DefaultEngine.ini and enable Developer Mode, so Unreal runs
        init_unreal.py without the plugin being installed.

    python .vscode/unreal_plugin.py remove --project "..."
    python .vscode/unreal_plugin.py unpythonpath --project "..."
        Undo the above.

``--project`` accepts a .uproject file or the directory that contains one.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
PYTHON_DIR = os.path.join(REPO_ROOT, "Content", "Python")
PLUGIN_LINK_NAME = "Blendkit"

INI_SECTION = "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]"


# ── Project resolution ────────────────────────────────────────────────────────


def _resolve_project_dir(project: str) -> str:
    """Return the project directory from a .uproject path or a folder path."""
    project = os.path.abspath(project)
    if os.path.isfile(project) and project.endswith(".uproject"):
        return os.path.dirname(project)
    if os.path.isdir(project):
        return project
    raise SystemExit(f"error: project not found: {project}")


# ── Junction / symlink ────────────────────────────────────────────────────────


def _make_link(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        print(f"  Link already exists: {dst}")
        return
    if sys.platform == "win32":
        # Directory junction — no admin rights required, unlike symlinks.
        subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], check=True)
    else:
        os.symlink(src, dst)
    print(f"  Linked {dst} -> {src}")


def _remove_link(dst: str) -> None:
    if not os.path.lexists(dst):
        print(f"  No link at: {dst}")
        return
    if os.path.islink(dst):
        os.unlink(dst)
    elif sys.platform == "win32":
        # Junctions are directories; rmdir removes the reparse point only.
        os.rmdir(dst)
    else:
        os.unlink(dst)
    print(f"  Removed link: {dst}")


def _vendor_and_build() -> None:
    dev_script = os.path.join(REPO_ROOT, "dev.py")
    print("Vendoring dependencies into Content/Python/bk_unreal/lib/ ...")
    subprocess.run([sys.executable, dev_script, "vendor"], cwd=REPO_ROOT, check=True)


def cmd_junction(project: str) -> None:
    project_dir = _resolve_project_dir(project)
    _vendor_and_build()
    dst = os.path.join(project_dir, "Plugins", PLUGIN_LINK_NAME)
    _make_link(REPO_ROOT, dst)
    print("\nDone. Restart the Unreal editor for this project and enable the "
          "Blendkit plugin (Edit > Plugins) if prompted.")


def cmd_remove(project: str) -> None:
    project_dir = _resolve_project_dir(project)
    dst = os.path.join(project_dir, "Plugins", PLUGIN_LINK_NAME)
    _remove_link(dst)


# ── Python AdditionalPaths in DefaultEngine.ini ───────────────────────────────


def _ini_path(project_dir: str) -> str:
    config_dir = os.path.join(project_dir, "Config")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "DefaultEngine.ini")


def _ini_entry() -> str:
    path = PYTHON_DIR.replace("\\", "/")
    return f'+AdditionalPaths=(Path="{path}")'


def cmd_pythonpath(project: str) -> None:
    project_dir = _resolve_project_dir(project)
    _vendor_and_build()
    ini = _ini_path(project_dir)
    entry = _ini_entry()

    text = ""
    if os.path.isfile(ini):
        with open(ini, encoding="utf-8") as fh:
            text = fh.read()

    if entry in text:
        print(f"  AdditionalPaths entry already present in {ini}")
        return

    block = f"{INI_SECTION}\nbDeveloperMode=True\n{entry}\n"
    if INI_SECTION in text:
        text = text.replace(INI_SECTION, f"{INI_SECTION}\nbDeveloperMode=True\n{entry}", 1)
    else:
        text = text.rstrip() + "\n\n" + block

    with open(ini, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"  Added Python AdditionalPaths + Developer Mode to {ini}")
    print("\nDone. Restart the Unreal editor for this project.")


def cmd_unpythonpath(project: str) -> None:
    project_dir = _resolve_project_dir(project)
    ini = _ini_path(project_dir)
    if not os.path.isfile(ini):
        print(f"  No {ini}")
        return
    with open(ini, encoding="utf-8") as fh:
        text = fh.read()
    entry = re.escape(_ini_entry())
    new_text = re.sub(rf"^{entry}\n?", "", text, flags=re.MULTILINE)
    with open(ini, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    print(f"  Removed AdditionalPaths entry from {ini}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Register this repo as an Unreal plugin for live dev.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("junction", "remove", "pythonpath", "unpythonpath"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True, help="Path to the .uproject file or its folder.")

    args = parser.parse_args()
    {
        "junction": cmd_junction,
        "remove": cmd_remove,
        "pythonpath": cmd_pythonpath,
        "unpythonpath": cmd_unpythonpath,
    }[args.command](args.project)


if __name__ == "__main__":
    main()
