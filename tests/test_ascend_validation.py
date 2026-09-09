"""Validate no-push export arguments and local OCI config inspection."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_ascend_image_checks import ROOT, ASCEND, VLLM, DIGEST, image_config, workflow_script


class ValidationBuildTests(unittest.TestCase):
    def build_args(self, publish):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text(
                f"#!{sys.executable}\n"
                "import json, os, pathlib, sys\n"
                'assert sys.argv[1:3] == ["buildx", "build"]\n'
                'pathlib.Path(os.environ["CAPTURE_ARGS"]).write_text(json.dumps(sys.argv[1:]))\n'
                'pathlib.Path(sys.argv[sys.argv.index("--metadata-file") + 1]).write_text('
                'json.dumps({"containerimage.digest": os.environ["BUILD_DIGEST"]}))\n'
            )
            docker.chmod(0o755)
            env = {
                **os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                "PUBLISH_IMAGE": str(publish).lower(), "RUNNER_TEMP": str(root),
                "GITHUB_OUTPUT": str(root / "output"), "CAPTURE_ARGS": str(root / "args"),
                "BUILD_DIGEST": DIGEST, "BUILD_RECIPE": "recipe-1", "BUILD_REVISION": "b" * 40,
                "BUILD_URL": "https://github.com/example/runs/1", "CACHE_FROM": "",
                "ASCEND_COMMIT": ASCEND, "VLLM_COMMIT": VLLM, "SOC_VERSION": "ascend910_9391",
                "IMAGE": "quay.io/atlas-ci/vllm-ascend", "IMAGE_TAG": "v0.28.0-f848128-a3",
                "DOCKERFILE": "Dockerfile.a3",
            }
            result = subprocess.run(
                ["/bin/bash", "-c", workflow_script("Build pinned source image")],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads((root / "args").read_text())

    def test_validation_export_never_pushes(self):
        args = self.build_args(False)
        output = args[args.index("--output") + 1]
        self.assertTrue(output.startswith("type=oci,"), output)
        self.assertNotIn("push=true", output)
        self.assertNotIn("--push", args)
        self.assertIn("tar=false", output)

    def test_publish_export_still_pushes_by_digest(self):
        args = self.build_args(True)
        self.assertIn("push-by-digest=true", args[args.index("--output") + 1])

    def test_credentials_and_tag_publication_are_opt_in(self):
        workflow = (ROOT / ".github/workflows/build-vllm-ascend.yml").read_text()
        for name in ["Log in to atlas-ci on Quay", "Publish and verify destination tag", "Log out of Quay"]:
            step = workflow.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]
            conditions = [line for line in step.splitlines() if line.startswith("        if:")]
            self.assertTrue(conditions and "inputs.publish" in conditions[0], name)


class LocalOciTests(unittest.TestCase):
    def setUp(self):
        script = ROOT / "scripts/read_oci_config.py"
        self.assertTrue(script.is_file(), "local OCI validation not implemented")
        spec = importlib.util.spec_from_file_location("read_oci_config", script)
        self.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.reader)

    def create_layout(self, root):
        directory = root / "blobs/sha256"
        directory.mkdir(parents=True)

        def blob(value):
            data = json.dumps(value).encode()
            digest = hashlib.sha256(data).hexdigest()
            (directory / digest).write_bytes(data)
            return {"digest": f"sha256:{digest}", "size": len(data)}

        config = blob(image_config())
        manifest = blob({"schemaVersion": 2, "config": config, "layers": []})
        (root / "index.json").write_text(json.dumps({"manifests": [manifest]}))
        return manifest["digest"], config["digest"]

    def test_reads_config_from_built_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest, _ = self.create_layout(root)
            self.assertEqual(self.reader.read_config(root, digest), image_config())

    def test_rejects_corrupt_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest, config = self.create_layout(root)
            (root / "blobs/sha256" / config.split(":")[1]).write_text("{}")
            with self.assertRaisesRegex(ValueError, "digest"):
                self.reader.read_config(root, digest)


if __name__ == "__main__":
    unittest.main()
