# ffmpeg-compiler

GitHub Actions workflows for building FFmpeg from upstream `master` and running AV1 compression tests.

## What this project does

- Pull FFmpeg from upstream source (git.ffmpeg.org).
- Auto-normalize gitweb/shortlog URLs into a cloneable Git URL.
- Build FFmpeg with common external codec libraries.
- Run an AV1 compression test and collect metrics.
- Upload build artifacts and optional GitHub Release assets.

## Workflows

- `.github/workflows/build-ffmpeg_dev.yml`
  - Main runner-based build workflow (uses `ubuntu-latest`).
- `.github/workflows/build-ffmpeg_indocker_dev.yml`
  - Main container-based build workflow (runner `ubuntu-latest`, container `ubuntu:devel`).
- `.github/workflows/call-build.yml`
  - Scheduled/matrix caller workflow.
- `.github/workflows/ci.yml`
  - Push/PR auto CI workflow that tests both `build-ffmpeg_dev.yml` and `build-ffmpeg_indocker_dev.yml`.

## Important compatibility behavior

The workflows accept inputs like:

- `https://git.ffmpeg.org/gitweb/ffmpeg.git`
- `https://git.ffmpeg.org/gitweb/ffmpeg.git/shortlog/refs/heads/masterd`

They will normalize the URL to a cloneable Git endpoint and then build the `master` branch.
If `masterd` is provided by mistake as the branch, it is auto-corrected to `master`.

## Manual usage

From GitHub Actions, run one of:

- `Build FFmpeg (Dev)`
- `(Docker) Build FFmpeg (Dev)`

Key inputs:

- `repo_url`: upstream source URL.
- `ffmpeg_branch`: branch name, default `master`.
- `test_seconds`: AV1 test duration, default `8`.
- `create_release`: whether to create/update release.

Fixed runtime images in workflow definitions:

- Runner image: `ubuntu-latest`
- Docker container image: `ubuntu:devel`

Note: `create_release` defaults to `false` to keep workflow permissions minimal by default.

## Recommended repository Actions settings

Use least privilege in repository settings:

1. `Settings` -> `Actions` -> `General`.
2. Set `Workflow permissions` to `Read repository contents permission`.
3. Keep `Allow GitHub Actions to create and approve pull requests` disabled unless required.

## VS Code + Copilot failure-fix loop

The `CI Build and Test` workflow runs automatically on push and pull requests.

It runs these two workflows every time:

- `build-ffmpeg_dev.yml`
- `build-ffmpeg_indocker_dev.yml`

On failure each build workflow still uploads logs and artifacts, including `failure-summary.md` when available.

Then you can ask Copilot in VS Code to fix based on that summary and push again to retrigger CI.

## Output

Each run provides:

- FFmpeg install tarball.
- AV1 test metrics (`av1_metrics.txt`).
- Build logs (`configure.log`, `make.log`, encoder list).
- Optional GitHub Release with assets.
