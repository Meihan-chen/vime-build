#!/usr/bin/env python3
"""Read image config from a local Buildx OCI export without loading its layers."""

import argparse
import hashlib
import json
from pathlib import Path
import re


def read_config(layout, manifest_digest):
    def blob(digest):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid OCI digest: {digest}")
        data = (Path(layout) / "blobs" / "sha256" / digest.split(":")[1]).read_bytes()
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"OCI blob digest mismatch: {digest}")
        return json.loads(data)

    manifest = blob(manifest_digest)
    return blob(manifest["config"]["digest"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--digest", required=True)
    args = parser.parse_args()
    try:
        config = read_config(args.layout, args.digest)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(config))


if __name__ == "__main__":
    main()
