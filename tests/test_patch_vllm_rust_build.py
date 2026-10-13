import unittest
from pathlib import Path

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

    def test_adds_the_same_runtime_fallback_when_upstream_uses_tools_path(self):
        source = "    ./tools/build_rust.sh\n"

        patched, changed = patch_dockerfile_text(source)

        self.assertTrue(changed)
        self.assertIn("if [ -f ./tools/build_rust.sh ]; then", patched)
        self.assertIn("elif [ -f ./build_rust.sh ]; then", patched)

    def test_is_idempotent_after_adding_the_runtime_fallback(self):
        source = "    ./tools/build_rust.sh\n"
        patched, _ = patch_dockerfile_text(source)

        patched_again, changed_again = patch_dockerfile_text(patched)

        self.assertFalse(changed_again)
        self.assertEqual(patched_again, patched)

    def test_fails_closed_when_the_build_script_invocation_is_unknown(self):
        with self.assertRaisesRegex(ValueError, "expected exactly one"):
            patch_dockerfile_text("RUN echo no rust build command\n")

    def test_fails_closed_when_both_paths_are_present(self):
        source = "    ./build_rust.sh\n    ./tools/build_rust.sh\n"

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
