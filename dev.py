# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####
# type: ignore

"""Developer tasks for the Blendkit Unreal plugin.

Commands
--------
    python dev.py vendor      Download pure-Python deps (qtpy, packaging,
                              requests) into Content/Python/bk_unreal/lib.
    python dev.py build       Vendor + build the local client + write a
                              version stamp + zip the plugin into out/.
    python dev.py client      Build only the local client binaries (add
                              ``--update`` to pull the latest submodule first).
    python dev.py stamp       Write Content/Python/bk_unreal/_build_version.py.

The Go ``blendkit-client`` lives in its own repo, embedded here as the
``bk_client`` git submodule (see .gitmodules). ``build`` delegates the compile
to ``bk_client/dev.py`` and unpacks the resulting bundle into ``client/`` — the
exact layout the runtime scans (see core/client_lib.py). Mirrors bk_maya/dev.py.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.join(REPO_ROOT, "Content", "Python", "bk_unreal")
LIB_DIR = os.path.join(PKG_DIR, "lib")
OUT_DIR = os.path.join(REPO_ROOT, "out")

CLIENT_SUBMODULE_DIR = os.path.join(REPO_ROOT, "bk_client")
CLIENT_SRC_DIR = os.path.join(CLIENT_SUBMODULE_DIR, "client")

CHANNEL_STABLE = "stable"
CHANNEL_ALPHA = "alpha"
CHANNEL_DEV = "dev"

VENDOR_PACKAGES = ["qtpy", "packaging", "requests"]

# Unreal's embedded Python ships no Qt binding (unlike Maya), so the PySide6
# binding for the asset-bar UI must be vendored too. These are abi3 wheels
# (cp310+), so a single download works for UE 5.4/5.8's Python 3.11.
QT_PACKAGES = ["PySide6-Essentials", "shiboken6"]


# ── Vendoring ─────────────────────────────────────────────────────


def vendor_packages(lib_dir: str = LIB_DIR, packages: list[str] | None = None) -> None:
    """Download dependency wheels (incl. the PySide6 Qt binding) into *lib_dir*."""
    packages = packages or [*VENDOR_PACKAGES, *QT_PACKAGES]
    print(f"Vendoring {packages} into {lib_dir} ...")
    os.makedirs(lib_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "--dest", tmp, *packages],
            check=True,
        )
        for whl_name in sorted(os.listdir(tmp)):
            if not whl_name.endswith(".whl"):
                continue
            with zipfile.ZipFile(os.path.join(tmp, whl_name)) as zf:
                for member in zf.infolist():
                    name = member.filename
                    if ".dist-info/" in name or name.endswith((".dist-info", "/")):
                        continue
                    target = os.path.join(lib_dir, *name.split("/"))
                    # Skip files already vendored with the same size — avoids
                    # overwriting binaries (e.g. PySide6 DLLs) that a running
                    # Unreal editor has locked in memory.
                    if os.path.isfile(target) and os.path.getsize(target) == member.file_size:
                        continue
                    try:
                        zf.extract(member, lib_dir)
                    except PermissionError as exc:
                        raise SystemExit(
                            f"error: cannot write {target}: {exc}\n"
                            "The file is in use — close the Unreal editor (and any Python "
                            "process using PySide6), then re-run. To force a clean re-vendor, "
                            "delete Content/Python/bk_unreal/lib first."
                        ) from exc
            print(f"  Extracted {whl_name}")
    print(f"Vendoring complete: {lib_dir}")


# ── Version stamp ─────────────────────────────────────────────────────────────


def read_base_version() -> str:
    """Read BASE_VERSION from the package _version.py without importing it."""
    version_py = os.path.join(PKG_DIR, "_version.py")
    with open(version_py, encoding="utf-8") as fh:
        for line in fh:
            if line.strip().startswith("BASE_VERSION"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.1"


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()  # nosec B607
    except Exception:
        return ""


def write_version_stamp(channel: str = CHANNEL_DEV, version: str | None = None) -> str:
    """Write the generated _build_version.py and return the full version."""
    base = read_base_version()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%y%m%d%H%M")
    if version is None:
        version = f"{base}.{stamp}" + ("-alpha" if channel == CHANNEL_ALPHA else "")
    content = (
        '"""Generated at build time by dev.py — do not edit or commit."""\n\n'
        f'VERSION = "{version}"\n'
        f'CHANNEL = "{channel}"\n'
        f'BUILD_TIME = "{now.isoformat()}"\n'
        f'GIT_COMMIT = "{_git_commit()}"\n'
    )
    with open(os.path.join(PKG_DIR, "_build_version.py"), "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"Wrote _build_version.py: {version} ({channel})")
    return version


# ── Client build ──────────────────────────────────────────────────────────────


def build_client() -> None:
    """Build the local (unsigned) client via the bk_client submodule."""
    dev_script = os.path.join(CLIENT_SUBMODULE_DIR, "dev.py")
    if not os.path.isfile(dev_script):
        print("bk_client submodule not found; skipping client build (run `git submodule update --init --recursive`).")
        return
    client_dir = os.path.join(REPO_ROOT, "client")
    result = subprocess.run([sys.executable, "dev.py", "build", "--out", client_dir], cwd=CLIENT_SUBMODULE_DIR)
    if result.returncode != 0:
        print("warning: client build failed; the plugin will report the client as missing.")
        return
    # bk_client's build ships only bk_client.zip (it removes the loose binaries).
    # The runtime scans for the loose per-platform binary, so unpack in place.
    _extract_client_bundles(client_dir)


def update_client_submodule() -> None:
    """Fast-forward the bk_client submodule to the latest remote commit."""
    if not os.path.isdir(os.path.join(CLIENT_SUBMODULE_DIR, ".git")) and not os.path.isfile(
        os.path.join(CLIENT_SUBMODULE_DIR, ".git")
    ):
        print("bk_client submodule not initialised; run `git submodule update --init --recursive` first.")
        return
    print("Updating bk_client submodule to latest remote commit ...")
    subprocess.run(  # nosec B607
        ["git", "submodule", "update", "--remote", "--recursive", "bk_client"],
        cwd=REPO_ROOT,
        check=False,
    )


def _extract_client_bundles(client_dir: str) -> None:
    """Unpack each ``v<ver>/bk_client.zip`` next to itself so the loose binaries
    the runtime looks for exist locally.
    """
    if not os.path.isdir(client_dir):
        return
    for entry in sorted(os.listdir(client_dir)):
        version_dir = os.path.join(client_dir, entry)
        bundle = os.path.join(version_dir, "bk_client.zip")
        if not os.path.isfile(bundle):
            continue
        root = os.path.abspath(version_dir)
        with zipfile.ZipFile(bundle) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                dest = os.path.join(version_dir, *member.split("/"))
                # Guard against zip-slip: skip members escaping the target dir.
                if not os.path.abspath(dest).startswith(root + os.sep):
                    print(f"  Skipping suspicious archive member: {member}")
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                if sys.platform != "win32" and os.path.basename(dest).startswith("bk_client-"):
                    # client binary must be executable
                    os.chmod(dest, 0o755)  # noqa: S103  # nosec B103
        print(f"  Extracted client binaries into {version_dir}")


# ── Package ───────────────────────────────────────────────────────────────────


# bk_client holds the Go sources; only the built `client/` binaries ship.
_EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", ".ruff_cache", ".pytest_cache", "out", "_debug", "tests", "bk_client"}


def build_zip(version: str) -> str:
    """Zip the plugin folder into out/Blendkit-<version>.zip."""
    os.makedirs(OUT_DIR, exist_ok=True)
    zip_path = os.path.join(OUT_DIR, f"Blendkit-{version}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS and not d.startswith(".git")]
            for name in files:
                # Ship only the loose per-platform binaries, not the redundant
                # bk_client.zip release bundle they were unpacked from.
                if name == "bk_client.zip":
                    continue
                abs_path = os.path.join(root, name)
                rel = os.path.relpath(abs_path, REPO_ROOT)
                zf.write(abs_path, os.path.join("Blendkit", rel))
    print(f"Built {zip_path}")
    return zip_path


def build(channel: str, version: str | None) -> None:
    vendor_packages()
    build_client()
    full_version = write_version_stamp(channel, version)
    build_zip(full_version)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Blendkit Unreal dev tasks")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("vendor", help="Vendor pure-Python deps into the plugin lib/")

    p_build = sub.add_parser("build", help="Vendor + client + stamp + zip")
    p_build.add_argument("--channel", default=CHANNEL_DEV, choices=[CHANNEL_STABLE, CHANNEL_ALPHA, CHANNEL_DEV])
    p_build.add_argument("--version", default=None)

    p_client = sub.add_parser("client", help="Build only the local client binaries (fast dev loop)")
    p_client.add_argument("--update", action="store_true", help="Pull the latest bk_client submodule commit first")

    p_stamp = sub.add_parser("stamp", help="Write _build_version.py only")
    p_stamp.add_argument("--channel", default=CHANNEL_DEV, choices=[CHANNEL_STABLE, CHANNEL_ALPHA, CHANNEL_DEV])
    p_stamp.add_argument("--version", default=None)

    args = parser.parse_args()
    if args.command == "vendor":
        vendor_packages()
    elif args.command == "build":
        build(args.channel, args.version)
    elif args.command == "client":
        if args.update:
            update_client_submodule()
        build_client()
    elif args.command == "stamp":
        write_version_stamp(args.channel, args.version)


if __name__ == "__main__":
    main()
