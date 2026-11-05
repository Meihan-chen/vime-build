#!/usr/bin/env python3
"""Repair the known FLA 26.9.1 mirror download for native ARM64 image builds."""

import argparse
import os
from pathlib import Path
import re
import subprocess


RELEASE_BASE = (
    "https://github.com/flashserve/flash-linear-attention-npu/"
    "releases/download/v26.9.1-release"
)
# Official release asset digests, not the old development-wheel hashes still
# declared in the upstream Dockerfiles.
SHA256 = {
    "a2": "aefe29fe3f021db45276b289b1f9882f85a34ce1f9900ba94251bda490af4b6e",
    "a3": "0e0febfe0b524df54c02dead985b787cf51f276ef904e4c2f88c5d43707a370b",
}
MIRROR_PREFIX = "https://mirrors.huaweicloud.com/ascend/repos/pypi/flash_linear_attention_npu_"
OLD_BLOCK = r'''    URL="https://mirrors.huaweicloud.com/ascend/repos/pypi/flash_linear_attention_npu_@VARIANT@/${FLA_WHL}" && \
    if [[ -n "${GIT_PROXY}" ]]; then \
      wget --quiet --proxy="${GIT_PROXY}" "${URL}" -O "/tmp/${FLA_WHL}"; \
    else \
      wget --quiet "${URL}" -O "/tmp/${FLA_WHL}"; \
    fi && \
    # echo "${FLA_SHA256}  /tmp/${FLA_WHL}" | sha256sum --check && \
    python3 -m pip install --force-reinstall --no-deps /tmp/${FLA_WHL}'''
NEW_BLOCK = r'''    FLA_SHA256="@SHA256@" && \
    URL="https://github.com/flashserve/flash-linear-attention-npu/releases/download/${FLA_RELEASE_TAG}/${FLA_WHL}" && \
    echo "Downloading FLA: ${URL}" && \
    wget --server-response --tries=3 --timeout=60 "${URL}" -O "/tmp/${FLA_WHL}" && \
    echo "${FLA_SHA256}  /tmp/${FLA_WHL}" | sha256sum -c && \
    python3 -m pip install --force-reinstall --no-deps /tmp/${FLA_WHL}'''


def prepare_dockerfile(source: str, variant: str) -> tuple[str, str | None]:
    """Adapt only the known broken block; preserve legacy GitHub downloads."""
    if variant not in SHA256:
        raise ValueError(f"Unsupported FLA hardware variant: {variant}")
    if MIRROR_PREFIX not in source:
        return source, None

    mirrors = re.findall(re.escape(MIRROR_PREFIX) + r'(a[23])/\$\{FLA_WHL\}', source)
    if mirrors != [variant]:
        raise ValueError("FLA mirror variant does not match the build matrix")
    if not re.search(r'^ARG FLA_VERSION="?26\.9\.1"?[ \t]*$', source, re.MULTILINE):
        raise ValueError("The mirror adapter only pins official FLA 26.9.1 assets")
    if not re.search(r'^ARG FLA_RELEASE_TAG="?v26\.9\.1-release"?[ \t]*$', source, re.MULTILINE):
        raise ValueError("Expected FLA 26.9.1 release tag v26.9.1-release")
    wheel_expression = f"flash_linear_attention_npu_{variant}-" + (
        "${FLA_VERSION}-py3-none-manylinux_2_34_aarch64.whl"
    )
    if wheel_expression not in source:
        raise ValueError("Expected the ARM64 FLA 26.9.1 wheel definition")

    old = OLD_BLOCK.replace("@VARIANT@", variant)
    if source.count(old) != 1:
        raise ValueError("Unexpected FLA download block; update the compatibility adapter")
    patched = source.replace(old, NEW_BLOCK.replace("@SHA256@", SHA256[variant]), 1)
    wheel = f"flash_linear_attention_npu_{variant}-26.9.1-py3-none-manylinux_2_34_aarch64.whl"
    return patched, f"{RELEASE_BASE}/{wheel}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfile", type=Path)
    parser.add_argument("--variant", choices=("a2", "a3"), required=True)
    parser.add_argument("--check-download", action="store_true")
    args = parser.parse_args()
    source = args.dockerfile.read_text(encoding="utf-8")
    try:
        patched, url = prepare_dockerfile(source, args.variant)
    except ValueError as error:
        parser.error(str(error))
    if url is None:
        print(f"No FLA mirror download patch needed for {args.dockerfile}")
        return

    if args.check_download:
        print(f"Checking official FLA asset before compilation: {url}", flush=True)
        # A real GET follows release redirects. Range keeps the check small
        # when supported; a server ignoring Range may send the whole wheel.
        subprocess.run([
            "curl", "--fail", "--location", "--silent", "--show-error",
            "--retry", "2", "--retry-all-errors", "--retry-delay", "2", "--connect-timeout", "15",
            "--max-time", "120", "--range", "0-0", "--output", os.devnull,
            "--write-out", "FLA preflight HTTP %{http_code}\n", url,
        ], check=True)

    args.dockerfile.write_text(patched, encoding="utf-8")
    print(f"Prepared official FLA 26.9.1 download and SHA256 check: {args.dockerfile}")


if __name__ == "__main__":
    main()
