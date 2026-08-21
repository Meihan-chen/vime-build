from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-vime-npu.yml"
DOCKERFILE_PATCH = ROOT / "patches" / "pin-vime-commit.patch"
SOC_VERSION_PATCH = ROOT / "patches" / "persist-soc-version.patch"


class WorkflowContractTests(unittest.TestCase):
    def workflow(self) -> str:
        self.assertTrue(WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}")
        return WORKFLOW.read_text()

    def dockerfile_patch(self) -> str:
        self.assertTrue(
            DOCKERFILE_PATCH.is_file(),
            f"missing Dockerfile patch: {DOCKERFILE_PATCH}",
        )
        return DOCKERFILE_PATCH.read_text()

    def soc_version_patch(self) -> str:
        self.assertTrue(
            SOC_VERSION_PATCH.is_file(),
            f"missing SOC_VERSION patch: {SOC_VERSION_PATCH}",
        )
        return SOC_VERSION_PATCH.read_text()

    def test_supports_daily_polling_and_manual_runs(self) -> None:
        workflow = self.workflow()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertRegex(workflow, r"cron:\s*['\"]?17 18 \* \* \*")
        self.assertNotIn('cron: "17 * * * *"', workflow)
        self.assertNotIn("*/15", workflow)

    def test_builds_natively_for_arm64(self) -> None:
        workflow = self.workflow()

        self.assertIn("runs-on: ubuntu-24.04-arm", workflow)
        self.assertIn("--platform linux/arm64", workflow)

    def test_defines_a3_and_a2_build_variants(self) -> None:
        workflow = self.workflow()

        self.assertIn("target_tag: vime-latest", workflow)
        self.assertIn("base_image_tag: v0.22.1rc1-a3", workflow)
        self.assertIn("soc_version: ascend910_9391", workflow)
        self.assertIn("target_tag: vime-a2-latest", workflow)
        self.assertIn("base_image_tag: v0.22.1rc1", workflow)
        self.assertIn("soc_version: ascend910b1", workflow)

    def test_publishes_only_the_a3_and_a2_latest_tags(self) -> None:
        workflow = self.workflow()

        self.assertIn("IMAGE: quay.io/ascend/vime", workflow)
        self.assertIn('${IMAGE}:${TARGET_TAG}', workflow)
        self.assertIn("push-by-digest=true", workflow)
        self.assertIn("docker buildx imagetools create", workflow)
        self.assertNotRegex(workflow, r"ascend-.*sha")
        self.assertNotRegex(workflow, r"vime-[0-9a-f]{7,40}")

    def test_uses_the_upstream_revision_as_the_image_identity(self) -> None:
        workflow = self.workflow()
        dockerfile_patch = self.dockerfile_patch()
        soc_version_patch = self.soc_version_patch()

        self.assertIn("org.opencontainers.image.revision", workflow)
        self.assertIn("io.vime.base-image-tag", workflow)
        self.assertIn("io.vime.soc-version", workflow)
        self.assertIn("VIME_COMMIT", workflow)
        self.assertIn("BASE_IMAGE_TAG", workflow)
        self.assertIn("SOC_VERSION", workflow)
        self.assertIn("ARG VIME_COMMIT", dockerfile_patch)
        self.assertIn("ENV SOC_VERSION=$SOC_VERSION", soc_version_patch)
        self.assertRegex(
            dockerfile_patch,
            r"git(?: -C \S+)? fetch --depth 1 origin.*VIME_COMMIT",
        )

    def test_only_applies_compatibility_patches_when_upstream_needs_them(self) -> None:
        workflow = self.workflow()

        self.assertRegex(workflow, r"if grep -Eq .*ARG VIME_COMMIT")
        self.assertRegex(workflow, r"if grep -Eq .*ENV SOC_VERSION")
        self.assertNotIn("if rg -q", workflow)

    def test_rechecks_upstream_head_before_promoting_latest(self) -> None:
        workflow = self.workflow()

        self.assertIn("Verify upstream HEAD before promotion", workflow)
        self.assertRegex(workflow, r"if .*current_sha.*source_sha")
        self.assertIn("Skip stale promotion", workflow)

    def test_logs_in_with_repository_secrets_and_password_stdin(self) -> None:
        workflow = self.workflow()

        self.assertIn("secrets.QUAY_USERNAME", workflow)
        self.assertIn("secrets.QUAY_TOKEN", workflow)
        self.assertIn("--password-stdin", workflow)
        self.assertNotRegex(workflow, r"docker login[^\n]*(?:\s-p\s|--password\s)")


if __name__ == "__main__":
    unittest.main()
