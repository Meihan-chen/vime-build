#!/usr/bin/env python3
"""Give current Vime latest images commit tags without rebuilding or moving latest."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


IMAGE = "quay.io/ascend/vime"
SOURCE = "https://github.com/vllm-project/vime"
VARIANTS = {
    "a2": ("vime-a2-latest", "ascend910b1"),
    "a3": ("vime-latest", "ascend910_9391"),
}


class Registry:
    def _run(self, arguments, *, optional=False):
        result = subprocess.run(
            ["docker", "buildx", "imagetools", *arguments],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            error = result.stderr.strip()
            if optional and re.search(r"manifest unknown|manifest_unknown|: not found", error, re.I):
                return None
            raise RuntimeError(f"Registry operation failed: {error}")
        return result.stdout

    def digest(self, reference, *, optional=False):
        output = self._run(["inspect", reference, "--format", "{{json .Manifest}}"], optional=optional)
        if output is None:
            return None
        digest = json.loads(output)["digest"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid manifest digest for {reference}: {digest}")
        return digest

    def config(self, reference):
        return json.loads(self._run(["inspect", reference, "--format", "{{json .Image}}"]))

    def tag(self, source, destination):
        self._run(["create", "--prefer-index=false", "--tag", destination, source])


def check_destination(destination, expected_digest, registry):
    digest = registry.digest(destination, optional=True)
    if digest is not None and digest != expected_digest:
        raise ValueError(
            f"{destination} already points to a different digest ({digest}); "
            f"refusing to overwrite it with {expected_digest}. "
            "This can be a short-SHA collision or a rebuild of the same commit."
        )
    return digest is not None


def plan_variant(variant, registry):
    source_tag, soc = VARIANTS[variant]
    source = f"{IMAGE}:{source_tag}"
    digest = registry.digest(source)
    # Read the config by the captured digest, never by the mutable latest tag.
    pinned_source = f"{IMAGE}@{digest}"
    config = registry.config(pinned_source)
    labels = config.get("config", {}).get("Labels") or {}
    commit = labels.get("org.opencontainers.image.revision")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError(f"{source} has no valid full Vime revision label")
    if labels.get("org.opencontainers.image.source") != SOURCE:
        raise ValueError(f"{source} does not identify {SOURCE} as its source")
    if config.get("architecture") != "arm64" or config.get("os") != "linux":
        raise ValueError(f"{source} must be a single linux/arm64 image")
    if (
        labels.get("io.vime.soc-version") != soc
        or f"SOC_VERSION={soc}" not in (config.get("config", {}).get("Env") or [])
    ):
        raise ValueError(f"{source} does not match the expected {variant} SOC {soc}")
    commit = commit.lower()
    destination = f"{IMAGE}:vime-{commit[:7]}-{variant}"
    exists = check_destination(destination, digest, registry)
    return {
        "variant": variant, "source": source, "pinned_source": pinned_source,
        "digest": digest, "commit": commit, "destination": destination,
        "status": "already_exists" if exists else "ready",
    }


def retag(variant, *, publish=False, registry=None):
    if variant not in ("all", *VARIANTS):
        raise ValueError(f"Unsupported variant: {variant}")
    if registry is None:
        registry = Registry()
    variants = list(VARIANTS) if variant == "all" else [variant]
    # Preflight all selected variants before adding any tag.
    plans = [plan_variant(name, registry) for name in variants]
    if publish:
        for plan in plans:
            # Recheck in case another publisher added a tag during the preflight.
            if check_destination(plan["destination"], plan["digest"], registry):
                plan["status"] = "already_exists"
                continue
            registry.tag(plan["pinned_source"], plan["destination"])
            if registry.digest(plan["destination"]) != plan["digest"]:
                raise RuntimeError(f"Published digest verification failed for {plan['destination']}")
            plan["status"] = "published"
    return plans


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("all", "a2", "a3"), default="all")
    parser.add_argument("--publish", action="store_true", help="Create commit tags; default is read-only preview")
    args = parser.parse_args()
    try:
        plans = retag(args.variant, publish=args.publish)
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(plans, indent=2))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("## Vime commit tags\n\n")
            if not args.publish:
                stream.write("Read-only preview: no tags were created.\n\n")
            for plan in plans:
                stream.write(
                    f"### {plan['variant'].upper()}: {plan['status']}\n\n"
                    f"- Source tag: `{plan['source']}`\n"
                    f"- Captured image: `{plan['pinned_source']}`\n"
                    f"- Vime commit: `{plan['commit']}`\n"
                    f"- Destination: `{plan['destination']}`\n\n"
                )
            stream.write("Latest tags and the image contents are unchanged.\n")


if __name__ == "__main__":
    main()
