#!/usr/bin/env python3
"""Temporarily adapt vLLM-Ascend Dockerfiles to vLLM Rust script path changes."""

import argparse
from pathlib import Path
import re


SCRIPT_INVOCATION = re.compile(
    r"(?m)^([ \t]*)(?:bash[ \t]+(?:\./)?|\./)"
    r"(?P<script_path>(?:tools/)?build_rust\.sh)[ \t]*$"
)
PATCH_MARKER = "# codex-temporary-rust-build-path-compat-v1"


def patch_dockerfile_text(source: str) -> tuple[str, bool]:
    """Adapt legacy root calls and leave upstream's fixed tools calls alone."""
    matches = list(SCRIPT_INVOCATION.finditer(source))
    if not matches and source.count(PATCH_MARKER) == 1:
        return source, False
    if len(matches) != 1 or PATCH_MARKER in source:
        raise ValueError(
            "expected exactly one standalone vLLM Rust script invocation "
            "and no prior compatibility marker"
        )
    if matches[0].group("script_path") == "tools/build_rust.sh":
        return source, False

    indent = matches[0].group(1)
    replacement = "\n".join([
        f"{indent}if [ -f ./tools/build_rust.sh ]; then \\",
        f"{indent}    bash ./tools/build_rust.sh; \\",
        f"{indent}elif [ -f ./build_rust.sh ]; then \\",
        f"{indent}    bash ./build_rust.sh; \\",
        f"{indent}else \\",
        f'{indent}    echo "No supported vLLM Rust build script found" >&2; exit 1; \\',
        f"{indent}fi {PATCH_MARKER}",
    ])
    start, end = matches[0].span()
    return source[:start] + replacement + source[end:], True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfile", type=Path)
    args = parser.parse_args()

    source = args.dockerfile.read_text(encoding="utf-8")
    patched, changed = patch_dockerfile_text(source)
    if changed:
        args.dockerfile.write_text(patched, encoding="utf-8")
        print(f"Applied temporary Rust script compatibility patch to {args.dockerfile}")
    else:
        print(f"No Rust script path patch needed for {args.dockerfile}")


if __name__ == "__main__":
    main()
