"""Validate run paths without writes; optionally reserve a new run directory.

Requires Python 3.8+. This prevents configuration mistakes and existing symlink
escapes, not hostile concurrent filesystem changes or writes by the model.
"""

import argparse
from pathlib import Path
import sys


def contains(parent, child):
    return parent == child or parent in child.parents


def prepare(source, output, name, create=False):
    if any(ord(char) < 32 or ord(char) == 127 for value in (source, output, name) for char in value):
        raise ValueError("paths and run names must not contain control characters")
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("run name must be a single directory name (no slashes, . or ..)")
    src = Path(source).resolve(strict=True)
    out = Path(output).resolve()
    if any(ord(char) < 32 or ord(char) == 127 for path in (src, out) for char in str(path)):
        raise ValueError("resolved paths must not contain control characters")
    candidate = out / name
    if contains(src, out):
        raise ValueError("output directory must not be inside the source directory")
    # Reject every pre-existing run, including dangling symlinks, before resolving it.
    if candidate.exists() or candidate.is_symlink():
        raise ValueError("run path already exists; choose a new --name (resume is not supported)")
    run = candidate.resolve()
    if run.parent != out:
        raise ValueError("run directory must remain directly inside the output directory")
    if contains(src, run) or contains(run, src):
        raise ValueError("source and run directories must not overlap")
    if create:
        out.mkdir(parents=True, exist_ok=True)
        # Exclusive mkdir also prevents simultaneous runs from reusing the same name.
        run.mkdir()
        (run / "federal_documents").mkdir()
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("name")
    args = parser.parse_args()
    try:
        print(prepare(args.source, args.output, args.name, args.create))
    except (OSError, ValueError, RuntimeError) as error:
        print("niw-guru: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
