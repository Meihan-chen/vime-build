#!/usr/bin/env python3
"""Resolve immutable source commits and a historical vLLM release prefix.

Only the image name uses the selected release. The build always uses the exact
vLLM commit from the pinned Ascend checkout's verified-commit file.
"""

import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote


ASCEND_REPO = "vllm-project/vllm-ascend"
VLLM_REPO = "vllm-project/vllm"
IMAGE = "quay.io/atlas-ci/vllm-ascend"


def parse_commit(value):
    commit = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected exactly one full 40-character commit SHA")
    return commit


def parse_timestamp(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return timestamp


def gh_json(endpoint, *, paginate=False):
    command = [
        "gh", "api", "--hostname", "github.com", endpoint,
        "-H", "X-GitHub-Api-Version: 2022-11-28",
    ]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"GitHub request failed ({endpoint}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def select_previous_release(releases, vllm_commit, commit_date, resolve_tag):
    """Select by publication time, not version order or Git ancestry.

    resolve_tag must return the peeled commit SHA, including for annotated tags.
    Lookup failures abort the selection; they must not silently choose an older tag.
    """
    cutoff = parse_timestamp(commit_date)
    candidates = []
    for release in releases:
        tag = release.get("tag_name", "")
        if (
            release.get("draft") is not False
            or release.get("prerelease") is not False
            or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)
            or not release.get("published_at")
        ):
            continue
        published = parse_timestamp(release["published_at"])
        if published < cutoff:
            version = tuple(int(part) for part in tag[1:].split("."))
            candidates.append((published, version, release))

    for _, _, release in sorted(candidates, key=lambda item: item[:2], reverse=True):
        if parse_commit(resolve_tag(release["tag_name"])) != vllm_commit:
            return release
    raise ValueError(
        f"No stable release published before {commit_date} remains after excluding "
        f"tags pointing to vLLM {vllm_commit}"
    )


def build_matrix(variant, version_prefix, ascend_commit):
    if variant not in ("all", "a2", "a3"):
        raise ValueError(f"Unsupported hardware variant: {variant}")
    return {"include": [
        {
            "variant": name,
            "dockerfile": dockerfile,
            "soc_version": soc,
            "image_tag": f"{version_prefix}-{ascend_commit[:7]}-{name}",
        }
        for name, dockerfile, soc in [
            ("a2", "Dockerfile", "ascend910b1"),
            ("a3", "Dockerfile.a3", "ascend910_9391"),
        ]
        if variant in ("all", name)
    ]}


def resolve(ascend_commit, variant, *, api=gh_json):
    ascend_commit = parse_commit(ascend_commit)
    if parse_commit(api(f"repos/{ASCEND_REPO}/commits/{ascend_commit}")["sha"]) != ascend_commit:
        raise ValueError("GitHub resolved a different Ascend commit")
    verified = api(
        f"repos/{ASCEND_REPO}/contents/.github/vllm-main-verified.commit?ref={ascend_commit}"
    )
    if verified["encoding"] != "base64":
        raise ValueError("Unexpected encoding for the verified-commit file")
    raw = base64.b64decode("".join(verified["content"].split()), validate=True).decode("ascii")
    vllm_commit = parse_commit(raw)
    commit = api(f"repos/{VLLM_REPO}/commits/{vllm_commit}")
    if parse_commit(commit["sha"]) != vllm_commit:
        raise ValueError("GitHub resolved a different vLLM commit")
    commit_date = commit["commit"]["committer"]["date"]

    pages = api(f"repos/{VLLM_REPO}/releases?per_page=100", paginate=True)
    releases = [release for page in pages for release in page]
    previous = select_previous_release(
        releases, vllm_commit, commit_date,
        # The commits endpoint peels annotated tags. target_commitish is often
        # just 'main' and is not evidence of the commit the release tag names.
        lambda tag: api(f"repos/{VLLM_REPO}/commits/{quote(tag, safe='')}")["sha"],
    )
    return {
        "ascend_commit": ascend_commit,
        "vllm_commit": vllm_commit,
        "vllm_commit_date": commit_date,
        "version_prefix": previous["tag_name"],
        "release_published_at": previous["published_at"],
        "matrix": build_matrix(variant, previous["tag_name"], ascend_commit),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ascend-commit", required=True)
    parser.add_argument("--variant", choices=("all", "a2", "a3"), default="all")
    args = parser.parse_args()
    try:
        result = resolve(args.ascend_commit, args.variant)
    except (ValueError, RuntimeError, KeyError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            for key, value in result.items():
                if isinstance(value, dict):
                    value = json.dumps(value, separators=(",", ":"))
                stream.write(f"{key}={value}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(
                "## Resolved source versions\n\n"
                f"- vLLM Ascend: `{result['ascend_commit']}`\n"
                f"- vLLM: `{result['vllm_commit']}`\n"
                f"- vLLM committer timestamp: `{result['vllm_commit_date']}`\n"
                f"- Prefix: `{result['version_prefix']}` "
                f"(published `{result['release_published_at']}`)\n\n"
                "The release is a naming prefix only, not the source version built.\n\n"
            )
            for entry in result["matrix"]["include"]:
                stream.write(f"- `{IMAGE}:{entry['image_tag']}` (`linux/arm64`)\n")


if __name__ == "__main__":
    main()
