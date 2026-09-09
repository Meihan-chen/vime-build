"""Run the actual workflow inspection steps against registry response fixtures."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASCEND = "f8481287a07cd7c407a3142f5d4188d71d964467"
VLLM = "b2f685834a6456197e7033966fdef52a23f1abcd"
DIGEST = "sha256:" + "c" * 64


def workflow_script(name):
    lines = (ROOT / ".github/workflows/build-vllm-ascend.yml").read_text().splitlines()
    start = lines.index(f"      - name: {name}")
    run = lines.index("        run: |", start) + 1
    body = []
    for line in lines[run:]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:])
    return "\n".join(body)


def image_config():
    return {
        "architecture": "arm64", "os": "linux",
        "config": {
            "Labels": {
                "org.opencontainers.image.revision": ASCEND,
                "io.vllm-ascend.vllm-revision": VLLM,
                "io.vllm-ascend.soc-version": "ascend910_9391",
                "io.vllm-ascend.build-recipe": "recipe-1",
            },
            "Env": ["SOC_VERSION=ascend910_9391"],
        },
    }


class ImageChecksTests(unittest.TestCase):
    def run_step(self, response, error="", step="Check existing image and protect tag identity", publish=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            # Only the external registry response is substituted. Bash, jq, and
            # the unmodified workflow's decision/validation logic execute for real.
            docker.write_text(
                '#!/bin/sh\n'
                'if [ "$1 $2 $3" != "buildx imagetools inspect" ]; then exit 99; fi\n'
                'if [ -n "$REGISTRY_ERROR" ]; then printf "%s" "$REGISTRY_ERROR" >&2; exit 1; fi\n'
                'printf "%s" "$REGISTRY_RESPONSE"\n'
            )
            docker.chmod(0o755)
            output = root / "output"
            env = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "REGISTRY_RESPONSE": json.dumps(response), "REGISTRY_ERROR": error,
                "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(root / "summary"),
                "ASCEND_COMMIT": ASCEND, "VLLM_COMMIT": VLLM,
                "SOC_VERSION": "ascend910_9391", "BUILD_RECIPE": "recipe-1",
                "BUILD_DIGEST": DIGEST, "IMAGE": "quay.io/atlas-ci/vllm-ascend",
                "IMAGE_TAG": "v0.28.0-f848128-a3",
                "PUBLISH_IMAGE": str(publish).lower(),
            }
            result = subprocess.run(
                ["/bin/bash", "-c", workflow_script(step)], env=env,
                capture_output=True, text=True, check=False,
            )
            return result, output.read_text() if output.exists() else ""

    def test_matching_image_is_reused(self):
        result, outputs = self.run_step({"image": image_config(), "manifest": {"digest": DIGEST}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("should_build=false", outputs)

    def test_validation_always_builds_without_contacting_target_registry(self):
        result, outputs = self.run_step({}, error="registry must not be contacted", publish=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("should_build=true", outputs)

    def test_seven_character_collision_is_rejected(self):
        config = image_config()
        config["config"]["Labels"]["org.opencontainers.image.revision"] = ASCEND[:7] + "0" * 33
        result, outputs = self.run_step({"image": config, "manifest": {"digest": DIGEST}})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("should_build=true", outputs)

    def test_changed_recipe_rebuilds_same_sources(self):
        config = image_config()
        config["config"]["Labels"]["io.vllm-ascend.build-recipe"] = "older-recipe"
        result, outputs = self.run_step({"image": config, "manifest": {"digest": DIGEST}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("should_build=true", outputs)

    def test_missing_tag_builds(self):
        result, outputs = self.run_step({}, error="ERROR: quay.io/atlas-ci/vllm-ascend:tag: not found")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("should_build=true", outputs)

    def test_authentication_and_network_errors_abort(self):
        for error in ["401 Unauthorized", "403 Forbidden", "TLS handshake timeout"]:
            with self.subTest(error=error):
                result, outputs = self.run_step({}, error=error)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("should_build=true", outputs)

    def test_config_is_validated_before_tag_publication(self):
        result, _ = self.run_step(image_config(), step="Verify built image before publishing tag")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_runtime_soc_is_rejected_even_if_labels_match(self):
        config = image_config()
        config["config"]["Env"] = ["SOC_VERSION=ascend910b1"]
        result, _ = self.run_step(config, step="Verify built image before publishing tag")
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_cpu_architecture_is_rejected(self):
        config = image_config()
        config["architecture"] = "amd64"
        result, _ = self.run_step(config, step="Verify built image before publishing tag")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
