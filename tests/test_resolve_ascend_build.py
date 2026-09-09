"""Behavior tests for selecting an image prefix from historical releases."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASCEND = "f8481287a07cd7c407a3142f5d4188d71d964467"
VLLM = "b2f685834a6456197e7033966fdef52a23f1abcd"
CUTOFF = "2026-08-29T04:07:28Z"


def release(tag, published, **changes):
    return {
        "tag_name": tag,
        "published_at": published,
        "draft": False,
        "prerelease": False,
        **changes,
    }


class ResolverTests(unittest.TestCase):
    def setUp(self):
        script = ROOT / "scripts" / "resolve_ascend_build.py"
        self.assertTrue(script.is_file(), "release resolver has not been implemented")
        spec = importlib.util.spec_from_file_location("ascend_resolver", script)
        self.resolver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.resolver)

    def select(self, releases, targets=None):
        targets = targets or {}
        return self.resolver.select_previous_release(
            releases, VLLM, CUTOFF, lambda tag: targets.get(tag, "a" * 40)
        )

    def test_uses_latest_publication_before_commit_not_largest_version(self):
        result = self.select([
            release("v0.27.1", "2026-08-11T10:47:49Z"),
            release("v0.29.0", "2026-09-09T00:00:00Z"),
            release("v0.28.0", "2026-08-26T09:46:30Z"),
            release("v0.27.2", "2026-08-28T09:00:00Z"),
        ])
        self.assertEqual(result["tag_name"], "v0.27.2")

    def test_example_pair_uses_v0280(self):
        result = self.select([
            release("v0.28.0", "2026-08-26T09:46:30Z"),
            release("v0.27.1", "2026-08-11T10:47:49Z"),
        ])
        self.assertEqual(result["tag_name"], "v0.28.0")

    def test_excludes_current_commit_even_if_release_timestamp_is_earlier(self):
        result = self.select([
            release("v0.28.0", "2026-08-28T00:00:00Z"),
            release("v0.27.1", "2026-08-11T10:47:49Z"),
        ], {"v0.28.0": VLLM})
        self.assertEqual(result["tag_name"], "v0.27.1")

    def test_excludes_rc_dev_drafts_and_flagged_prereleases(self):
        releases = [release("v0.27.1", "2026-08-11T10:47:49Z")]
        releases += [
            release("v0.28.0rc1", "2026-08-28T00:00:00Z"),
            release("v0.28.0.dev1", "2026-08-28T00:00:00Z"),
            release("v0.28.0", "2026-08-28T00:00:00Z", draft=True),
            release("v0.29.0", "2026-08-28T00:00:00Z", prerelease=True),
            release("v0.30.0", None),
        ]
        self.assertEqual(self.select(releases)["tag_name"], "v0.27.1")

    def test_equal_timestamp_is_not_a_previous_release(self):
        with self.assertRaisesRegex(ValueError, "No stable release"):
            self.select([release("v0.28.0", CUTOFF)])

    def test_date_comparison_honors_timezone(self):
        result = self.select([
            release("v0.28.0", "2026-08-29T11:00:00+08:00"),
            release("v0.27.1", "2026-08-29T02:00:00Z"),
        ])
        self.assertEqual(result["tag_name"], "v0.28.0")

    def test_date_without_timezone_is_rejected(self):
        with self.assertRaises(ValueError):
            self.select([release("v0.28.0", "2026-08-28T10:00:00")])

    def test_no_release_does_not_fall_back_to_latest_or_main(self):
        with self.assertRaisesRegex(ValueError, "No stable release"):
            self.select([release("v0.29.0", "2026-09-09T00:00:00Z")])

    def test_failed_tag_lookup_is_not_silently_skipped(self):
        def missing_tag(tag):
            raise RuntimeError("cannot resolve release tag")

        with self.assertRaisesRegex(RuntimeError, "cannot resolve"):
            self.resolver.select_previous_release(
                [release("v0.28.0", "2026-08-26T09:46:30Z")],
                VLLM, CUTOFF, missing_tag,
            )

    def test_verified_file_accepts_trailing_whitespace_and_normalizes_case(self):
        self.assertEqual(self.resolver.parse_commit(VLLM.upper() + "\r\n"), VLLM)

    def test_rejects_branch_short_sha_empty_or_multiple_verified_commits(self):
        for value in ["", "main", VLLM[:7], VLLM + "\n" + ASCEND, "g" * 40]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.resolver.parse_commit(value)

    def test_resolves_verified_file_at_ascend_sha_and_commit_committer_date(self):
        import base64

        # This fixture is a network boundary; selection and normalization run unchanged.
        responses = {
            f"repos/vllm-project/vllm-ascend/commits/{ASCEND}": {"sha": ASCEND},
            f"repos/vllm-project/vllm-ascend/contents/.github/vllm-main-verified.commit?ref={ASCEND}": {
                "encoding": "base64", "content": base64.b64encode((VLLM + "\n").encode()).decode(),
            },
            f"repos/vllm-project/vllm/commits/{VLLM}": {
                "sha": VLLM,
                "commit": {"committer": {"date": CUTOFF}, "author": {"date": "2026-01-01T00:00:00Z"}},
            },
            "repos/vllm-project/vllm/releases?per_page=100": [
                [release("v0.29.0", "2026-09-09T00:00:00Z")],
                [release("v0.28.0", "2026-08-26T09:46:30Z")],
            ],
            "repos/vllm-project/vllm/commits/v0.28.0": {"sha": "a" * 40},
        }

        def api(endpoint, *, paginate=False):
            self.assertEqual(paginate, "/releases?" in endpoint)
            return responses[endpoint]

        result = self.resolver.resolve(ASCEND, "all", api=api)
        self.assertEqual(result["vllm_commit"], VLLM)
        self.assertEqual(result["vllm_commit_date"], CUTOFF)
        self.assertEqual(result["version_prefix"], "v0.28.0")
        self.assertEqual(result["ascend_commit"], ASCEND)
        self.assertEqual(
            [entry["image_tag"] for entry in result["matrix"]["include"]],
            ["v0.28.0-f848128-a2", "v0.28.0-f848128-a3"],
        )

    def test_selecting_one_hardware_variant_does_not_build_the_other(self):
        result = self.resolver.build_matrix("a3", "v0.28.0", ASCEND)
        self.assertEqual(result, {"include": [{
            "variant": "a3", "dockerfile": "Dockerfile.a3",
            "soc_version": "ascend910_9391", "image_tag": "v0.28.0-f848128-a3",
        }]})

    def test_unknown_hardware_variant_is_rejected(self):
        with self.assertRaises(ValueError):
            self.resolver.build_matrix("a5", "v0.28.0", ASCEND)


if __name__ == "__main__":
    unittest.main()
