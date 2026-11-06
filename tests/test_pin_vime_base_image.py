import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.pin_vime_base_image import pin_base_image


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-vime-npu.yml"
SINGLE = """# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=quay.io/atlas-ci/vllm-ascend
ARG BASE_IMAGE_TAG=v0.31.0-007abb8-a3
FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG}
RUN echo single-stage
"""
# Reproduce the c47a216 layout: an independent Rust stage appears first.
MULTI = """# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=quay.io/atlas-ci/vllm-ascend
ARG BASE_IMAGE_TAG=v0.31.0-007abb8-a3
ARG RUST_IMAGE=docker.m.daocloud.io/library/rust
ARG RUST_IMAGE_TAG=1.95-bookworm

FROM ${RUST_IMAGE}:${RUST_IMAGE_TAG} AS rust-toolchain
FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG} AS wheel-builder-base
COPY --from=rust-toolchain /usr/local/cargo/bin /usr/local/cargo/bin
FROM wheel-builder-base AS sunabako-wheel
RUN echo build-wheel
FROM wheel-builder-base AS vllm-router-wheel
RUN echo build-router
FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG}
COPY --from=sunabako-wheel /wheels /wheels
COPY --from=vllm-router-wheel /wheels /wheels
"""


class PinVimeBaseImageTests(unittest.TestCase):
    def test_preserves_single_stage_builds(self):
        pinned = pin_base_image(SINGLE)
        self.assertIn("ARG BASE_IMAGE_REF=${BASE_IMAGE}:${BASE_IMAGE_TAG}\nFROM ${BASE_IMAGE_REF}", pinned)
        self.assertNotIn("FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG}", pinned)
        self.assertIn("RUN echo single-stage", pinned)

    def test_pins_both_multistage_base_references_and_keeps_other_stages(self):
        pinned = pin_base_image(MULTI)
        declaration = "ARG BASE_IMAGE_REF=${BASE_IMAGE}:${BASE_IMAGE_TAG}"
        rust_from = "FROM ${RUST_IMAGE}:${RUST_IMAGE_TAG} AS rust-toolchain"
        self.assertLess(pinned.index(declaration), pinned.index(rust_from))
        self.assertEqual(pinned.count(declaration), 1)
        self.assertEqual(pinned.count("FROM ${BASE_IMAGE_REF}"), 2)
        self.assertIn("FROM ${BASE_IMAGE_REF} AS wheel-builder-base", pinned)
        for line in MULTI.splitlines():
            if "rust-toolchain" in line or "FROM wheel-builder-base" in line or "COPY --from=" in line:
                self.assertIn(line, pinned)

    def test_is_idempotent_when_upstream_already_supports_the_global_argument(self):
        pinned = pin_base_image(MULTI)
        self.assertEqual(pin_base_image(pinned), pinned)

    def test_rewrites_legacy_references_even_if_the_global_argument_exists(self):
        source = SINGLE.replace("FROM ", "ARG BASE_IMAGE_REF\nFROM ", 1)
        pinned = pin_base_image(source)
        self.assertEqual(pinned.count("ARG BASE_IMAGE_REF"), 1)
        self.assertIn("FROM ${BASE_IMAGE_REF}", pinned)

    def test_stage_scoped_argument_does_not_replace_the_required_global_argument(self):
        source = MULTI.replace(
            "AS rust-toolchain\n", "AS rust-toolchain\nARG BASE_IMAGE_REF\n", 1
        )
        pinned = pin_base_image(source)
        self.assertLess(pinned.index("ARG BASE_IMAGE_REF"), pinned.index("FROM "))
        self.assertEqual(pinned.count("ARG BASE_IMAGE_REF"), 2)

    def test_preserves_platform_flags_aliases_and_unbraced_variable_syntax(self):
        source = SINGLE.replace(
            "FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG}",
            "FROM --platform=$TARGETPLATFORM $BASE_IMAGE:$BASE_IMAGE_TAG AS runtime",
        )
        self.assertIn(
            "FROM --platform=$TARGETPLATFORM ${BASE_IMAGE_REF} AS runtime",
            pin_base_image(source),
        )

    def test_rejects_unknown_base_image_expressions_instead_of_partially_pinning(self):
        source = MULTI.replace(
            "FROM ${BASE_IMAGE}:${BASE_IMAGE_TAG}\n",
            "FROM ${BASE_IMAGE}:unexpected-tag\n",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported.*FROM"):
            pin_base_image(source)

    def test_rejects_a_dockerfile_with_no_supported_base_stage(self):
        with self.assertRaisesRegex(ValueError, "base stage"):
            pin_base_image("FROM ubuntu:22.04\n")

    def test_multistage_resolution_still_uses_the_correct_a2_and_a3_image(self):
        lines = WORKFLOW.read_text().splitlines()
        start = lines.index("      - name: Resolve base image from the pinned Dockerfile")
        start = lines.index("        run: |", start) + 1
        body = []
        for line in lines[start:]:
            if line and not line.startswith("          "):
                break
            body.append(line[10:])
        for variant in ("a2", "a3"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                dockerfile = root / "upstream/docker/Dockerfile.npu"
                dockerfile.parent.mkdir(parents=True)
                dockerfile.write_text(pin_base_image(MULTI))
                docker = root / "docker"
                docker.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, pathlib, sys\n"
                    "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
                    "print(json.dumps({'digest': 'sha256:' + 'a' * 64}))\n"
                )
                docker.chmod(0o755)
                output = root / "output"
                result = subprocess.run(
                    ["/bin/bash", "-c", "\n".join(body)], cwd=root,
                    env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                         "VARIANT": variant, "GITHUB_OUTPUT": str(output),
                         "CAPTURE": str(root / "args")},
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads((root / "args").read_text())
                self.assertEqual(args[3], f"quay.io/atlas-ci/vllm-ascend:v0.31.0-007abb8-{variant}")
                self.assertIn("ref=quay.io/atlas-ci/vllm-ascend@sha256:" + "a" * 64, output.read_text())

    def test_workflow_uses_the_multistage_adapter(self):
        self.assertIn(
            "python3 scripts/pin_vime_base_image.py upstream/docker/Dockerfile.npu",
            WORKFLOW.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
