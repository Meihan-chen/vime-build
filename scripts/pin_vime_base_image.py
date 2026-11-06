#!/usr/bin/env python3
"""Make every Vime vLLM-Ascend base stage use one global BASE_IMAGE_REF."""

import argparse
from pathlib import Path
import re


FROM = re.compile(
    r"^(?P<prefix>[ \t]*FROM[ \t]+(?:--[\w-]+=\S+[ \t]+)*)"
    r"(?P<image>\S+)(?P<suffix>[^\r\n]*)$",
    re.MULTILINE | re.IGNORECASE,
)
LEGACY_IMAGE = re.compile(r"(?:\$\{BASE_IMAGE\}|\$BASE_IMAGE):(?:\$\{BASE_IMAGE_TAG\}|\$BASE_IMAGE_TAG)")
PINNED_IMAGE = re.compile(r"(?:\$\{BASE_IMAGE_REF\}|\$BASE_IMAGE_REF)")
BASE_VARIABLE = re.compile(r"\$(?:\{)?BASE_IMAGE(?:_REF|_TAG)?\b")


def pin_base_image(source: str) -> str:
    """Preserve independent stages and aliases while pinning all base uses."""
    stages = list(FROM.finditer(source))
    if not stages:
        raise ValueError("Dockerfile has no FROM or supported Vime base stage")
    supported = 0
    pieces = []
    cursor = 0
    for stage in stages:
        image = stage.group("image")
        if image == "\\" or stage.group("suffix").rstrip().endswith("\\"):
            raise ValueError("Unsupported continued FROM instruction")
        if LEGACY_IMAGE.fullmatch(image):
            image = "${BASE_IMAGE_REF}"
            supported += 1
        elif PINNED_IMAGE.fullmatch(image):
            supported += 1
        elif BASE_VARIABLE.search(image):
            raise ValueError(f"Unsupported Vime base FROM expression: {image}")
        pieces.extend([
            source[cursor:stage.start()],
            stage.group("prefix") + image + stage.group("suffix"),
        ])
        cursor = stage.end()
    if not supported:
        raise ValueError("Dockerfile has no supported Vime base stage")
    pieces.append(source[cursor:])
    pinned = "".join(pieces)

    first_from = stages[0].start()
    global_args = re.findall(
        r"^[ \t]*ARG[ \t]+(\w+)\b", source[:first_from],
        re.MULTILINE | re.IGNORECASE,
    )
    if not {"BASE_IMAGE", "BASE_IMAGE_TAG"} <= set(global_args):
        raise ValueError("BASE_IMAGE and BASE_IMAGE_TAG must be global ARGs before the first FROM")
    if global_args.count("BASE_IMAGE_REF") > 1:
        raise ValueError("Duplicate global BASE_IMAGE_REF declarations")
    if "BASE_IMAGE_REF" not in global_args:
        declaration = "ARG BASE_IMAGE_REF=${BASE_IMAGE}:${BASE_IMAGE_TAG}\n"
        pinned = pinned[:first_from] + declaration + pinned[first_from:]
    return pinned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfile", type=Path)
    args = parser.parse_args()
    source = args.dockerfile.read_text(encoding="utf-8")
    try:
        pinned = pin_base_image(source)
    except ValueError as error:
        parser.error(str(error))
    if pinned != source:
        args.dockerfile.write_text(pinned, encoding="utf-8")
        print("Prepared all Vime base stages for digest-pinned BASE_IMAGE_REF")
    else:
        print("All Vime base stages already support global BASE_IMAGE_REF")


if __name__ == "__main__":
    main()
