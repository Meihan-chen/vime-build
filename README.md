# vime-build

Builds the Vime Ascend NPU image from the
[`vllm-project/vime`](https://github.com/vllm-project/vime/tree/ascend)
`ascend` branch and publishes two ARM64 variants:

```text
Atlas A3: quay.io/ascend/vime:vime-latest
Atlas A2: quay.io/ascend/vime:vime-a2-latest
```

No commit-specific image tags are created.

## Triggering

[The workflow](.github/workflows/build-vime-npu.yml) polls the upstream branch
once per hour (at minute 17) and can also be started manually from the Actions
page. Each variant compares the upstream HEAD, base-image tag, and SOC version
with labels on its published image and skips an unchanged build configuration.

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
