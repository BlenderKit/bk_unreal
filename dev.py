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
    python dev.py build       Vendor + build the local (unsigned) client +
                              write a version stamp + zip the plugin into out/.
    python dev.py client      Build only the local client binaries (add
                              ``--update`` to pull the latest submodule first).
    python dev.py stamp       Write Content/Python/bk_unreal/_build_version.py.
    python dev.py release     Vendor + download the *signed* bk_client release
                              (or unpack --client-build <bk_client.zip>) +
                              stamp + zip. Used by the GitHub Release workflow.

The Go ``blendkit-client`` lives in its own repo, embedded here as the
``bk_client`` git submodule (see .gitmodules). ``build`` delegates the compile
to ``bk_client/dev.py`` and unpacks the resulting bundle into ``client/`` — the
exact layout the runtime scans (see core/client_lib.py). ``release`` instead
downloads the code-signed/notarized binaries published on the bk_client
GitHub releases (signing happens in that repo's CI, never locally). Mirrors
bk_maya/dev.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.join(REPO_ROOT, "Content", "Python", "bk_unreal")
LIB_DIR = os.path.join(PKG_DIR, "lib")
OUT_DIR = os.path.join(REPO_ROOT, "out")
CLIENT_DIR = os.path.join(REPO_ROOT, "client")

CLIENT_SUBMODULE_DIR = os.path.join(REPO_ROOT, "bk_client")
CLIENT_SRC_DIR = os.path.join(CLIENT_SUBMODULE_DIR, "client")

# The bk_client GitHub release the ``release`` command pulls signed binaries
# from (code-signing/notarization happens in that repo's CI, so we never sign
# locally). Pass --client-build <bk_client.zip> to use a locally downloaded
# signed bundle instead of hitting the network. Mirrors bk_maya/dev.py.
CLIENT_RELEASE_REPO = "BlenderKit/bk_client"
CLIENT_RELEASE_ASSET = "bk_client.zip"

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


# ── Signed client release download ────────────────────────────────────────────
# ``build`` compiles the client locally (unsigned, fast dev loop). ``release``
# instead downloads the *signed* bk_client.zip published on the bk_client
# GitHub releases, because code-signing/notarization happens in that repo's
# CI. Mirrors bk_maya/dev.py.


def _github_headers() -> dict:
    """Headers for GitHub API/download requests (honours ``$GITHUB_TOKEN``)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bk_unreal-dev"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_client_version_pin() -> str:
    """Read the pinned Client minor series (e.g. ``v1.12``) from global_vars.py."""
    global_vars_py = os.path.join(PKG_DIR, "core", "global_vars.py")
    with open(global_vars_py, encoding="utf-8") as fh:
        match = re.search(r'^CLIENT_VERSION\s*[:=].*?"([^"]+)"', fh.read(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find CLIENT_VERSION in {global_vars_py}")
    return match.group(1)


def resolve_client_release_tag(pin: str) -> str:
    """Resolve a version pin to an exact published bk_client release tag.

    - ``vX.Y``   -> the newest published ``vX.Y.Z`` release (bk_client auto-bumps
      the patch on each merge, so this tracks the latest patch of the series).
    - ``vX.Y.Z`` -> that exact tag.
    """
    parts = pin.lstrip("v").split(".")
    if len(parts) >= 3:
        return f"v{'.'.join(parts[:3])}"

    major, minor = parts[0], parts[1]
    pattern = re.compile(rf"^v{re.escape(major)}\.{re.escape(minor)}\.(\d+)$")
    api_url = f"https://api.github.com/repos/{CLIENT_RELEASE_REPO}/releases?per_page=100"
    request = urllib.request.Request(api_url, headers=_github_headers())
    with urllib.request.urlopen(request) as response:
        releases = json.load(response)

    matches = []
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        m = pattern.match(rel.get("tag_name", ""))
        if m:
            matches.append((int(m.group(1)), rel["tag_name"]))
    if not matches:
        raise RuntimeError(f"No published {CLIENT_RELEASE_REPO} release found for series v{major}.{minor}.*")
    matches.sort()
    return matches[-1][1]


def _unpack_release_bundle(zip_path: str, client_dir: str) -> str:
    """Unpack a downloaded ``bk_client.zip`` into ``client/vX.Y.Z/`` (same
    layout ``_extract_client_bundles`` produces from a local build).
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        version = None
        for candidate in ("client/VERSION", "VERSION"):
            if candidate in names:
                version = "v" + zf.read(candidate).decode("utf-8").strip()
                break
        if version is None:
            raise RuntimeError(f"{CLIENT_RELEASE_ASSET} is missing a VERSION file")

        version_dir = os.path.join(client_dir, version)
        root = os.path.abspath(version_dir)
        os.makedirs(version_dir, exist_ok=True)
        for member in zf.infolist():
            if member.is_dir():
                continue
            rel = member.filename
            if rel.startswith("client/"):
                rel = rel[len("client/") :]
            if not rel:
                continue
            dest = os.path.join(version_dir, *rel.split("/"))
            if not os.path.abspath(dest).startswith(root + os.sep):
                print(f"  Skipping suspicious archive member: {member.filename}")
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            if sys.platform != "win32" and os.path.basename(dest).startswith("bk_client-"):
                os.chmod(dest, 0o755)  # noqa: S103  # nosec B103
    return version


def download_client_release(client_dir: str, tag: str | None = None) -> str:
    """Download the signed ``bk_client.zip`` bundle from the bk_client GitHub
    releases and unpack it into ``client/vX.Y.Z/``. Returns the client version.
    """
    if not tag:
        pin = read_client_version_pin()
        tag = resolve_client_release_tag(pin)
        print(f"Client pin {pin} resolved to release {tag}")
    api_url = f"https://api.github.com/repos/{CLIENT_RELEASE_REPO}/releases/tags/{tag}"

    print(f"Fetching bk_client release metadata: {api_url}")
    request = urllib.request.Request(api_url, headers=_github_headers())
    with urllib.request.urlopen(request) as response:
        release_data = json.load(response)

    asset_url = None
    for asset in release_data.get("assets", []):
        if asset.get("name") == CLIENT_RELEASE_ASSET:
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        published = release_data.get("tag_name", tag or "latest")
        print(
            f"error: bk_client release '{published}' has no {CLIENT_RELEASE_ASSET} asset yet.\n"
            "       Publish a zipped release, or pass --client-build <bk_client.zip> "
            "with a locally downloaded signed bundle."
        )
        sys.exit(1)

    os.makedirs(client_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, CLIENT_RELEASE_ASSET)
        print(f"Downloading {asset_url}")
        request = urllib.request.Request(asset_url, headers=_github_headers())
        with urllib.request.urlopen(request) as response, open(zip_path, "wb") as fh:
            shutil.copyfileobj(response, fh)
        version = _unpack_release_bundle(zip_path, client_dir)
    print(f"Blendkit-Client {version} downloaded and unpacked into {client_dir}")
    return version


def install_local_client_bundle(bundle_path: str, client_dir: str) -> str:
    """Unpack a locally downloaded signed ``bk_client.zip`` into *client_dir*.

    *bundle_path* may point at the ``bk_client.zip`` file itself or a directory
    containing it.
    """
    if os.path.isdir(bundle_path):
        candidate = os.path.join(bundle_path, CLIENT_RELEASE_ASSET)
        if os.path.isfile(candidate):
            bundle_path = candidate
    if not os.path.isfile(bundle_path):
        print(
            f"error: local client bundle {bundle_path} not found "
            f"(expected a {CLIENT_RELEASE_ASSET} file or a directory containing it)."
        )
        sys.exit(1)
    os.makedirs(client_dir, exist_ok=True)
    version = _unpack_release_bundle(bundle_path, client_dir)
    print(f"Blendkit-Client {version} installed from {bundle_path}")
    return version


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


def release(channel: str, version: str | None, client_build: str | None) -> None:
    """Vendor + fetch signed client binaries + stamp + zip.

    Unlike ``build`` (which compiles the client locally and is unsigned), this
    ships the code-signed/notarized binaries published by the bk_client repo's
    own CI — either the pinned release (default) or a locally supplied
    ``--client-build <bk_client.zip>`` bundle.
    """
    vendor_packages()
    if client_build:
        install_local_client_bundle(client_build, CLIENT_DIR)
    else:
        download_client_release(CLIENT_DIR)
    full_version = write_version_stamp(channel, version)
    build_zip(full_version)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Blendkit Unreal dev tasks")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("vendor", help="Vendor pure-Python deps into the plugin lib/")

    p_build = sub.add_parser("build", help="Vendor + local (unsigned) client + stamp + zip")
    p_build.add_argument("--channel", default=CHANNEL_DEV, choices=[CHANNEL_STABLE, CHANNEL_ALPHA, CHANNEL_DEV])
    p_build.add_argument("--version", default=None)

    p_client = sub.add_parser("client", help="Build only the local client binaries (fast dev loop)")
    p_client.add_argument("--update", action="store_true", help="Pull the latest bk_client submodule commit first")

    p_stamp = sub.add_parser("stamp", help="Write _build_version.py only")
    p_stamp.add_argument("--channel", default=CHANNEL_DEV, choices=[CHANNEL_STABLE, CHANNEL_ALPHA, CHANNEL_DEV])
    p_stamp.add_argument("--version", default=None)

    p_release = sub.add_parser("release", help="Vendor + signed client release + stamp + zip (for CI/publishing)")
    p_release.add_argument("--channel", default=CHANNEL_STABLE, choices=[CHANNEL_STABLE, CHANNEL_ALPHA, CHANNEL_DEV])
    p_release.add_argument("--version", default=None)
    p_release.add_argument(
        "--client-build", default=None, help="Path to a locally downloaded signed bk_client.zip (or its directory)"
    )

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
    elif args.command == "release":
        release(args.channel, args.version, args.client_build)


if __name__ == "__main__":
    main()
