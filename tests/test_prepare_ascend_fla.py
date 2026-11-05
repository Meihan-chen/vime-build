import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.prepare_ascend_fla import prepare_dockerfile


ROOT = Path(__file__).resolve().parents[1]
# The failing bf074627 FLA block, with its surrounding RUN shortened so the
# checksum behavior can also be executed without a container.
SOURCE = r'''ARG FLA_VERSION="26.9.1"
ARG FLA_RELEASE_TAG="v26.9.1-release"
RUN FLA_WHL="flash_linear_attention_npu_a2-${FLA_VERSION}-py3-none-manylinux_2_34_aarch64.whl" && \
    URL="https://mirrors.huaweicloud.com/ascend/repos/pypi/flash_linear_attention_npu_a2/${FLA_WHL}" && \
    if [[ -n "${GIT_PROXY}" ]]; then \
      wget --quiet --proxy="${GIT_PROXY}" "${URL}" -O "/tmp/${FLA_WHL}"; \
    else \
      wget --quiet "${URL}" -O "/tmp/${FLA_WHL}"; \
    fi && \
    # echo "${FLA_SHA256}  /tmp/${FLA_WHL}" | sha256sum --check && \
    python3 -m pip install --force-reinstall --no-deps /tmp/${FLA_WHL}
'''


class PrepareAscendFlaTests(unittest.TestCase):
    def test_prepares_both_arm64_variants_from_the_official_release(self):
        for variant in ("a2", "a3"):
            with self.subTest(variant=variant):
                source = SOURCE.replace("_a2", f"_{variant}")
                patched, url = prepare_dockerfile(source, variant)

                self.assertTrue(url.startswith(
                    "https://github.com/flashserve/flash-linear-attention-npu/releases/download/v26.9.1-release/"
                ))
                self.assertTrue(url.endswith(
                    f"flash_linear_attention_npu_{variant}-26.9.1-py3-none-manylinux_2_34_aarch64.whl"
                ))
                self.assertNotIn("mirrors.huaweicloud.com", patched)
                self.assertNotIn("wget --quiet", patched)
                self.assertIn('echo "${FLA_SHA256}  /tmp/${FLA_WHL}" | sha256sum -c', patched)

    def test_does_not_change_the_old_successful_github_download(self):
        source = r'''ARG FLA_VERSION="26.9.1+dev5cc6990"
RUN wget --quiet "${GIT_PROXY:-}https://github.com/flashserve/flash-linear-attention-npu/releases/download/${FLA_RELEASE_TAG}/${FLA_WHL}" -O /tmp/${FLA_WHL}
'''
        self.assertEqual(prepare_dockerfile(source, "a2"), (source, None))

    def test_does_not_patch_twice(self):
        patched, _ = prepare_dockerfile(SOURCE, "a2")
        self.assertEqual(prepare_dockerfile(patched, "a2"), (patched, None))

    def test_rejects_an_unpinned_mirror_version(self):
        with self.assertRaisesRegex(ValueError, "26.9.1"):
            prepare_dockerfile(SOURCE.replace('FLA_VERSION="26.9.1"', 'FLA_VERSION="26.9.2"'), "a2")

    def test_rejects_an_unexpected_download_block(self):
        with self.assertRaisesRegex(ValueError, "download block"):
            prepare_dockerfile(SOURCE.replace("wget --quiet", "curl"), "a2")

    def test_rejects_a_hardware_variant_mismatch(self):
        with self.assertRaisesRegex(ValueError, "variant"):
            prepare_dockerfile(SOURCE, "a3")

    def test_checksum_failure_prevents_installing_a_corrupt_wheel(self):
        patched, _ = prepare_dockerfile(SOURCE, "a2")
        script = patched.split("RUN ", 1)[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wget = root / "wget"
            wget.write_text(
                '#!/bin/bash\nwhile [[ "$1" != "-O" ]]; do shift; done\n'
                'printf "corrupt download" > "$2"\n'
            )
            wget.chmod(0o755)
            installer = root / "python3"
            installer.write_text('#!/bin/bash\ntouch "$INSTALL_MARKER"\n')
            installer.chmod(0o755)
            if sys.platform == "darwin":
                # macOS sha256sum does not implement GNU's check-from-stdin;
                # shasum provides the same real SHA256 verification here.
                checker = root / "sha256sum"
                checker.write_text('#!/bin/sh\nexec /usr/bin/shasum -a 256 "$@"\n')
                checker.chmod(0o755)
            marker = root / "installed"
            result = subprocess.run(
                ["/bin/bash", "-c", script.replace("/tmp/", f"{root}/")],
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                     "FLA_VERSION": "26.9.1", "FLA_RELEASE_TAG": "v26.9.1-release",
                     "INSTALL_MARKER": str(marker)},
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAILED", result.stdout, result.stderr)
            self.assertFalse(marker.exists())

    def test_workflow_checks_download_before_expensive_builds(self):
        workflow = (ROOT / ".github/workflows/build-vllm-ascend.yml").read_text()
        step = workflow.index("- name: Prepare and preflight FLA download")
        self.assertLess(step, workflow.index("- name: Reclaim hosted runner disk space"))
        self.assertIn("--check-download", workflow[step:workflow.index("- name: Build pinned source image")])
        self.assertIn("'scripts/prepare_ascend_fla.py'", workflow)


if __name__ == "__main__":
    unittest.main()
