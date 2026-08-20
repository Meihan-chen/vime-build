# vime-build

Builds the Vime Ascend NPU image from the
[`vllm-project/vime`](https://github.com/vllm-project/vime/tree/ascend)
`ascend` branch and publishes it as:

```text
quay.io/ascend/vime:vime-latest
```

No commit-specific image tags are created.

## Triggering

[The workflow](.github/workflows/build-vime-npu.yml) polls the upstream branch
every 15 minutes and can also be started manually from the Actions page. It
compares the upstream HEAD with the image's
`org.opencontainers.image.revision` label and skips an unchanged revision.

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

The build runs natively on GitHub's `ubuntu-24.04-arm` runner and produces only
`linux/arm64`. It pushes the completed result by digest without a visible tag,
then rechecks the upstream HEAD before moving `vime-latest` to that digest. A
build that became stale while running is not promoted.

The upstream Dockerfile is patched during the job so `/root/vime` is checked
out at the exact revision recorded in the image label. The Dockerfile's import
check runs during the build; the hosted runner has no Ascend NPU, so the
workflow does not run hardware tests.

The image is large, so the workflow reclaims preinstalled SDK space before
building. If the standard runner still runs out of disk, switch the job to an
ARM64 runner with a larger disk.
