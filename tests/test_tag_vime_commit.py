"""Retagging must pin the published image, not the current upstream Git HEAD."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "quay.io/ascend/vime"
COMMIT = "8a940bf457ee852619c53c1ca9ef812c9503e080"
A3_DIGEST = "sha256:" + "a" * 64
A2_DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64


class RegistryFixture:
    def __init__(self):
        self.tags = {f"{IMAGE}:vime-latest": A3_DIGEST, f"{IMAGE}:vime-a2-latest": A2_DIGEST}
        self.configs = {}
        for digest, soc in [(A3_DIGEST, "ascend910_9391"), (A2_DIGEST, "ascend910b1")]:
            self.configs[f"{IMAGE}@{digest}"] = {
                "architecture": "arm64", "os": "linux",
                "config": {
                    "Labels": {
                        "org.opencontainers.image.revision": COMMIT,
                        "org.opencontainers.image.source": "https://github.com/vllm-project/vime",
                        "io.vime.soc-version": soc,
                    },
                    "Env": [f"SOC_VERSION={soc}"],
                },
            }
        self.writes = []

    def digest(self, ref, *, optional=False):
        if ref not in self.tags and not optional:
            raise RuntimeError(f"Source image missing: {ref}")
        return self.tags.get(ref)

    def config(self, ref):
        return deepcopy(self.configs[ref])

    def tag(self, source, destination):
        self.writes.append((source, destination))
        self.tags[destination] = source.split("@", 1)[1]


class RetagTests(unittest.TestCase):
    def setUp(self):
        script = ROOT / "scripts/tag_vime_commit.py"
        self.assertTrue(script.is_file(), "manual Vime retagging is not implemented")
        spec = importlib.util.spec_from_file_location("tag_vime_commit", script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.registry = RegistryFixture()

    def test_default_preview_has_no_writes(self):
        result = self.module.retag("all", registry=self.registry)
        self.assertEqual([row["destination"] for row in result], [
            f"{IMAGE}:vime-8a940bf-a2", f"{IMAGE}:vime-8a940bf-a3",
        ])
        self.assertEqual(self.registry.writes, [])

    def test_publish_reuses_digest_and_preserves_latest(self):
        result = self.module.retag("a3", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes, [
            (f"{IMAGE}@{A3_DIGEST}", f"{IMAGE}:vime-8a940bf-a3"),
        ])
        self.assertEqual(self.registry.tags[f"{IMAGE}:vime-latest"], A3_DIGEST)
        self.assertEqual(result[0]["status"], "published")
        self.assertEqual(result[0]["commit"], COMMIT)

    def test_latest_can_move_after_digest_is_captured(self):
        original = self.registry.config

        def moving_latest(ref):
            self.registry.tags[f"{IMAGE}:vime-latest"] = OTHER_DIGEST
            return original(ref)

        self.registry.config = moving_latest
        self.module.retag("a3", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes[0][0], f"{IMAGE}@{A3_DIGEST}")
        self.assertEqual(self.registry.tags[f"{IMAGE}:vime-latest"], OTHER_DIGEST)

    def test_same_destination_digest_is_idempotent(self):
        self.registry.tags[f"{IMAGE}:vime-8a940bf-a3"] = A3_DIGEST
        result = self.module.retag("a3", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes, [])
        self.assertEqual(result[0]["status"], "already_exists")

    def test_existing_tag_with_different_digest_is_never_overwritten(self):
        self.registry.tags[f"{IMAGE}:vime-8a940bf-a3"] = OTHER_DIGEST
        with self.assertRaisesRegex(ValueError, "different digest"):
            self.module.retag("a3", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes, [])

    def test_all_variants_are_preflighted_before_any_write(self):
        self.registry.tags[f"{IMAGE}:vime-8a940bf-a3"] = OTHER_DIGEST
        with self.assertRaises(ValueError):
            self.module.retag("all", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes, [])

    def test_each_variant_uses_its_own_recorded_commit(self):
        self.registry.configs[f"{IMAGE}@{A2_DIGEST}"]["config"]["Labels"]["org.opencontainers.image.revision"] = "d" * 40
        result = self.module.retag("all", registry=self.registry)
        self.assertEqual(result[0]["destination"], f"{IMAGE}:vime-ddddddd-a2")
        self.assertEqual(result[1]["destination"], f"{IMAGE}:vime-8a940bf-a3")

    def test_missing_or_invalid_revision_is_rejected(self):
        labels = self.registry.configs[f"{IMAGE}@{A3_DIGEST}"]["config"]["Labels"]
        for revision in [None, "", "main", "8a940bf", "invalid\nvalue"]:
            with self.subTest(revision=revision):
                labels["org.opencontainers.image.revision"] = revision
                with self.assertRaisesRegex(ValueError, "revision"):
                    self.module.retag("a3", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes, [])

    def test_wrong_source_hardware_or_platform_is_rejected(self):
        reference = f"{IMAGE}@{A3_DIGEST}"
        original = deepcopy(self.registry.configs[reference])
        for field in ["source", "soc", "arch"]:
            self.registry.configs[reference] = deepcopy(original)
            config = self.registry.configs[reference]
            if field == "source":
                config["config"]["Labels"]["org.opencontainers.image.source"] = "https://github.com/other/repo"
            elif field == "soc":
                config["config"]["Labels"]["io.vime.soc-version"] = "ascend910b1"
            else:
                config["architecture"] = "amd64"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.retag("a3", publish=True, registry=self.registry)

    def test_registry_error_is_not_treated_as_missing_destination(self):
        original = self.registry.digest

        def denied(ref, *, optional=False):
            if optional:
                raise RuntimeError("401 unauthorized")
            return original(ref)

        self.registry.digest = denied
        with self.assertRaisesRegex(RuntimeError, "401"):
            self.module.retag("a3", publish=True, registry=self.registry)
        self.assertEqual(self.registry.writes, [])

    def test_post_publication_digest_must_match(self):
        def bad_tag(source, destination):
            self.registry.tags[destination] = OTHER_DIGEST

        self.registry.tag = bad_tag
        with self.assertRaisesRegex(RuntimeError, "verification"):
            self.module.retag("a3", publish=True, registry=self.registry)


if __name__ == "__main__":
    unittest.main()
