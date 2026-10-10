#!/usr/bin/env python3
"""Assemble the single-file OrcaSlicer plugin.

    python3 build.py              # writes dist/indx_cold_pull.py
    python3 build.py --install    # ...and copies it into OrcaSlicer's orca_plugins folder
    python3 build.py --preview    # ...and writes dist/preview.html, the window with a mock plugin

"Install local plugin" in OrcaSlicer copies only the selected .py, so the page and the core module
are inlined into src/plugin.py rather than shipped beside it.
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")
PLUGIN_NAME = "indx_cold_pull"


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def build():
    plugin = read(SRC, "plugin.py")
    core = read(SRC, "cold_pull_core.py")
    page = read(SRC, "page.html")
    for marker in ("# @@CORE@@\n", "PAGE = None  # @@PAGE@@\n"):
        if plugin.count(marker) != 1:
            sys.exit(f"build: expected exactly one {marker.strip()!r} in src/plugin.py")
    if '"""' in page or page.endswith("\\"):
        sys.exit('build: page.html must not contain """ or end with a backslash (it becomes a raw string)')
    core_block = ("# ---- begin src/cold_pull_core.py " + "-" * 60 + "\n" + core.rstrip("\n") + "\n"
                  "# ---- end src/cold_pull_core.py " + "-" * 62 + "\n")
    out = plugin.replace("# @@CORE@@\n", core_block).replace("PAGE = None  # @@PAGE@@\n", 'PAGE = r"""' + page + '"""\n')
    compile(out, PLUGIN_NAME + ".py", "exec")
    os.makedirs(DIST, exist_ok=True)
    path = os.path.join(DIST, PLUGIN_NAME + ".py")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(out)
    return path


def orca_data_dir():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/OrcaSlicer")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "OrcaSlicer")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "OrcaSlicer")


def install(built):
    data_dir = orca_data_dir()
    if not os.path.isdir(data_dir):
        sys.exit(f"install: OrcaSlicer's data folder {data_dir} does not exist")
    folder = os.path.join(data_dir, "orca_plugins", PLUGIN_NAME)
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, PLUGIN_NAME + ".py")
    shutil.copyfile(built, target)
    return target


def preview():
    page = read(SRC, "page.html")
    head = read(ROOT, "tests", "preview_head.html")
    mock = read(ROOT, "tests", "preview_mock.js")
    out = page.replace("<head>\n", "<head>\n" + head + "\n<script>\n" + mock + "\n</script>\n", 1)
    path = os.path.join(DIST, "preview.html")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(out)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install", action="store_true", help="copy the plugin into OrcaSlicer's orca_plugins folder")
    parser.add_argument("--preview", action="store_true", help="also write dist/preview.html with a mock plugin")
    args = parser.parse_args()
    built = build()
    print(f"built     {built}")
    if args.preview:
        print(f"preview   {preview()}")
    if args.install:
        print(f"installed {install(built)}")


if __name__ == "__main__":
    main()
