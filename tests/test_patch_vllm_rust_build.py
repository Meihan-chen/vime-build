import unittest
from pathlib import Path
import subprocess
import tempfile

from scripts.patch_vllm_rust_build import patch_dockerfile_text


ROOT = Path(__file__).resolve().parents[1]


class PatchVllmRustBuildTests(unittest.TestCase):
    def test_adds_a_runtime_fallback_for_the_old_root_script_path(self):
        source = "    ./build_rust.sh\n"

        patched, changed = patch_dockerfile_text(source)

        self.assertTrue(changed)
        self.assertIn("if [ -f ./tools/build_rust.sh ]; then", patched)
        self.assertIn("elif [ -f ./build_rust.sh ]; then", patched)
        self.assertIn("./tools/build_rust.sh; \\", patched)
        self.assertIn("./build_rust.sh; \\", patched)

    def test_leaves_the_fixed_tools_path_unchanged(self):
        source = "    ./tools/build_rust.sh\n"

        patched, changed = patch_dockerfile_text(source)

        self.assertFalse(changed)
        self.assertEqual(patched, source)

    def test_leaves_the_current_upstream_bash_install_step_unchanged(self):
        source = r"""RUN cd /vllm-workspace/vllm && \
    export PROTOC_INCLUDE=/usr/include && \
    python3 -m pip install -r requirements/build/rust.txt && \
    bash tools/build_rust.sh
"""
        patched, changed = patch_dockerfile_text(source)

        self.assertFalse(changed)
        self.assertEqual(patched, source)

    def test_legacy_bash_invocation_runs_either_script_without_executable_bit(self):
        for script_path in ("tools/build_rust.sh", "build_rust.sh"):
            with self.subTest(script_path=script_path), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                script = root / script_path
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text("printf 'rust build ran'\n")
                script.chmod(0o644)
                patched, changed = patch_dockerfile_text("    bash ./build_rust.sh\n")

                result = subprocess.run(
                    ["/bin/bash", "-c", patched], cwd=root,
                    capture_output=True, text=True, check=False,
                )

                self.assertTrue(changed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "rust build ran")

    def test_is_idempotent_after_adding_the_runtime_fallback(self):
        source = "    ./build_rust.sh\n"
        patched, _ = patch_dockerfile_text(source)

        patched_again, changed_again = patch_dockerfile_text(patched)

        self.assertFalse(changed_again)
        self.assertEqual(patched_again, patched)

    def test_fails_closed_when_the_build_script_invocation_is_unknown(self):
        with self.assertRaisesRegex(ValueError, "expected exactly one"):
            patch_dockerfile_text("RUN echo no rust build command\n")

    def test_fails_closed_when_both_paths_are_present(self):
        source = "    ./build_rust.sh\n    bash tools/build_rust.sh\n"

        with self.assertRaisesRegex(ValueError, "expected exactly one"):
            patch_dockerfile_text(source)

    def test_workflow_patches_the_selected_dockerfile_before_building(self):
        workflow = (ROOT / ".github/workflows/build-vllm-ascend.yml").read_text()
        patch_step = workflow.index("- name: Apply temporary vLLM Rust script path patch")
        build_step = workflow.index("- name: Build pinned source image")

        self.assertLess(patch_step, build_step)
        self.assertIn(
            'python3 scripts/patch_vllm_rust_build.py "ascend/${DOCKERFILE}"',
            workflow[patch_step:build_step],
        )
        self.assertIn("'scripts/patch_vllm_rust_build.py'", workflow)


if __name__ == "__main__":
    unittest.main()
