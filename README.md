# vime-build

Builds the Vime Ascend NPU image from the
[`vllm-project/vime`](https://github.com/vllm-project/vime/tree/ascend)
`ascend` branch and publishes two ARM64 variants:

```text
Atlas A3: quay.io/ascend/vime:vime-latest
Atlas A2: quay.io/ascend/vime:vime-a2-latest
```

The daily Vime build publishes only latest tags. Commit tags can be added to
existing images using the separate manual workflow described below.

## Triggering

[The workflow](.github/workflows/build-vime-npu.yml) polls the upstream branch
once per day at 18:17 UTC (02:17 Asia/Shanghai) and can also be started manually
from the Actions page. Each variant compares the upstream HEAD, base-image tag,
and SOC version with labels on its published image and skips an unchanged build
configuration.

Because GitHub Actions cannot subscribe to a push event in an unrelated
repository, polling is used instead of an upstream `push` trigger.

## Required secrets

Configure these repository Actions secrets:

- `QUAY_USERNAME`: the Quay user or robot name, for example
  `ascend+ascend_bot`.
- `QUAY_TOKEN`: the corresponding Quay password/token with push access to
  `ascend/vime`.

The workflow logs in through `--password-stdin`; credentials are never stored
in the workflow file.

## Publishing behavior

The A3 and A2 matrix jobs run natively on GitHub's `ubuntu-24.04-arm` runners
and produce only `linux/arm64`. Each job pushes its completed result by digest
without a visible temporary tag, then rechecks the upstream HEAD before moving
its latest tag to that digest. A build that became stale while running is not
promoted.

Until the upstream Dockerfile natively supports the required build arguments,
the job applies compatibility patches so `/root/vime` is checked out at the
exact revision recorded in the image label and `SOC_VERSION` is persisted. The
patches are skipped automatically once upstream has those capabilities. The
Dockerfile's import check runs during the build; the hosted runner has no
Ascend NPU, so the workflow does not run hardware tests.

The image is large, so the workflow reclaims preinstalled SDK space before
building. If the standard runner still runs out of disk, switch the job to an
ARM64 runner with a larger disk.

## Manually tag current Vime latest images with their commit

[`tag-vime-commit.yml`](.github/workflows/tag-vime-commit.yml), **Tag Vime latest
with commit**, runs only through `workflow_dispatch`. Select `variant=all`,
`a2`, or `a3`. The default `publish=false` previews the operation; set
`publish=true` to create tags using the existing `QUAY_USERNAME` / `QUAY_TOKEN`
Secrets for `quay.io/ascend/vime`.

| Variant | Source tag | New tag |
| --- | --- | --- |
| A2 | `quay.io/ascend/vime:vime-a2-latest` | `quay.io/ascend/vime:vime-<Vime-first-7-SHA>-a2` |
| A3 | `quay.io/ascend/vime:vime-latest` | `quay.io/ascend/vime:vime-<Vime-first-7-SHA>-a3` |

The SHA comes from the published image's full `org.opencontainers.image.revision`
label, not the current GitHub branch HEAD or the vLLM/Ascend dependency versions.
The script captures the source digest first, then reads metadata and creates the
new tag from that immutable digest. It does not build, pull image layers, change
the contents/labels, or move/delete either latest tag. Both references identify
the same image; a later daily build may move latest while the commit tag remains.

All selected variants are checked before any write. A missing/invalid revision,
unexpected source/platform/SOC, or a destination pointing to another digest
fails explicitly. An already matching destination is a successful no-op. A
different digest is never overwritten, even when the commit is unchanged but
dependencies were rebuilt. If A2/A3 latest images contain different commits,
each gets its own correctly derived tag, shown in the Actions summary.

Preview the current latest images:

```bash
gh workflow run tag-vime-commit.yml --repo Meihan-chen/vime-build \
  -f variant=all -f publish=false
```

Publish the commit tags manually:

```bash
gh workflow run tag-vime-commit.yml --repo Meihan-chen/vime-build \
  -f variant=all -f publish=true
```

Each run snapshots latest afresh. A preview does not freeze a later run's
source. Inspect the summary for the full source SHA, digest and destination.

## Independent pinned vLLM Ascend builds

[`build-vllm-ascend.yml`](.github/workflows/build-vllm-ascend.yml) is a separate
workflow with **only a manual `workflow_dispatch` trigger**. It does not call or
update the Vime workflow and does not run on a schedule, push, or PR event.

It builds the upstream vLLM Ascend Dockerfiles from a full Ascend commit SHA.
The matching vLLM SHA is read from `.github/vllm-main-verified.commit` at that
exact Ascend commit. The build checks out Ascend (including its submodules),
rechecks the verified file, and passes the full vLLM SHA as `VLLM_COMMIT`.
Missing/invalid commits or unsupported Dockerfiles cause an error instead of
falling back to the upstream default release tag.

| Input | Required | Meaning |
| --- | --- | --- |
| `ascend_commit` | Yes | Full 40-character SHA in `vllm-project/vllm-ascend` |
| `variant` | Yes; default `all` | `a2`, `a3`, or `all`; both build natively for `linux/arm64` |
| `publish` | Yes; default `false` | `false`: full local build and validation; `true`: publish to atlas-ci |

| Variant | Dockerfile from the pinned commit | SOC_VERSION |
| --- | --- | --- |
| A2 | `Dockerfile` | `ascend910b1` |
| A3 | `Dockerfile.a3` | `ascend910_9391` |

### Separate atlas-ci credentials

For `publish=true`, add both as **repository Secrets**, not Variables, in Settings → Secrets and
variables → Actions:

- `ATLAS_CI_QUAY_USERNAME`: the Quay user/robot name with access to the target.
- `ATLAS_CI_QUAY_TOKEN`: its token with write access to
  `quay.io/atlas-ci/vllm-ascend`.

These credentials are separate from the existing Vime `QUAY_*` secrets.
Validation with `publish=false` does not use Quay credentials or inspect the
destination repository; public build dependencies are still downloaded.

### Historical release prefix

The resolver uses the vLLM commit's **committer timestamp** as the cutoff. It
reads all pages of GitHub Releases, keeps non-draft, non-prerelease releases
whose tags exactly match `vMAJOR.MINOR.PATCH`, and selects the most recently
published release **strictly before** that cutoff. It resolves each candidate
tag to its commit (including annotated tags) and excludes tags pointing to the
vLLM commit itself. Release `created_at`, `target_commitish`, today's latest
tag, version-number ordering, and `git describe` are not used for selection.

If two eligible releases have the same publication timestamp, the higher
numeric version wins. If no eligible release exists, resolution fails with an
explanation. The prefix describes release timing, not Git ancestry or a claim
that the image contains the release's exact code.

Only the **image tag** uses this release prefix. The actual vLLM code remains
pinned to the SHA from the verified file:

```text
quay.io/atlas-ci/vllm-ascend:<previous-release>-<Ascend-first-7-SHA>-a2
quay.io/atlas-ci/vllm-ascend:<previous-release>-<Ascend-first-7-SHA>-a3
```

For example, Ascend `f8481287a07cd7c407a3142f5d4188d71d964467` records vLLM
`b2f685834a6456197e7033966fdef52a23f1abcd` (2026-08-29). Its prefix is `v0.28.0`
(published 2026-08-26), giving `v0.28.0-f848128-a2` and `v0.28.0-f848128-a3`.

### Run and inspect

```bash
gh workflow run build-vllm-ascend.yml --repo Meihan-chen/vime-build \
  -f ascend_commit=f8481287a07cd7c407a3142f5d4188d71d964467 \
  -f variant=all -f publish=true
```

For a full build without uploading images (no Quay credentials needed):

```bash
gh workflow run build-vllm-ascend.yml --repo Meihan-chen/vime-build \
  -f ascend_commit=fd815467c221ee600137f6bdd53fe354d5e7c999 \
  -f variant=all -f publish=false
```

To preview resolution locally without building or pushing an image:

```bash
python3 scripts/resolve_ascend_build.py \
  --ascend-commit f8481287a07cd7c407a3142f5d4188d71d964467 --variant all
```

The resolver requires Python 3.10+ and an authenticated `gh` CLI (Actions uses
its built-in `GITHUB_TOKEN`). Its JSON output and Actions summary show both full
SHAs, the cutoff timestamp, selected release/publication time, and image tags.

Each image records these labels:

| Label | Value |
| --- | --- |
| `org.opencontainers.image.source` | Upstream vLLM Ascend repository URL |
| `org.opencontainers.image.revision` | Full Ascend SHA |
| `org.opencontainers.image.version` | Complete generated image tag |
| `org.opencontainers.image.created` | UTC build timestamp |
| `io.vllm-ascend.vllm-revision` | Full verified vLLM SHA |
| `io.vllm-ascend.soc-version` | A2/A3 SOC version |
| `io.vllm-ascend.build-url` | GitHub Actions Run URL |
| `io.vllm-ascend.build-revision` | Build-configuration repository SHA |
| `io.vllm-ascend.build-recipe` | Hash of this workflow and resolver |

Matching source commits, platform, SOC, and build recipe reuse an existing
image. A tag that has different or missing source/platform metadata is rejected
instead of overwritten, including a seven-character SHA collision. Changing
the build recipe can rebuild the same source tag, so consumers needing the
exact artifact should use its digest, reported in the Actions summary.

Builds use an inline registry cache, upload by digest, verify the architecture,
source labels and SOC environment, then publish the final tag and verify its
digest. They do not publish a rolling `main` or `latest` tag. A2/A3 failures are
independent. GitHub-hosted CPU runners do not validate NPU execution; source
pinning also does not freeze mutable CANN base tags or unpinned APT/pip packages.

With `publish=false`, the job always builds, exports a local OCI layout on the
ephemeral runner, validates the manifest/config hashes and the same source/SOC
metadata, and reports the local digest. It skips login, destination lookup,
digest upload, and tag publication. No image or image archive is uploaded. This
mode validates the build/export path but cannot validate Quay write permissions.

Resolver tests and workflow linting:

```bash
python3 -m unittest discover -v
actionlint
```
