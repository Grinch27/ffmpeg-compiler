# ffmpeg-compiler

GitHub Actions workflows for building FFmpeg from upstream `master` and running AV1 compression tests.

## What this project does

- Pull FFmpeg from upstream source (git.ffmpeg.org).
- Auto-normalize gitweb/shortlog URLs into a cloneable Git URL.
- Build FFmpeg with common external codec libraries.
- Run an AV1 compression test and collect metrics.
- Upload build artifacts and optional GitHub Release assets.

## Workflows

- `.github/workflows/build-ffmpeg.yml`
  - Main runner-based build workflow.
- `.github/workflows/build-ffmpeg_dev.yml`
  - Dev wrapper around `build-ffmpeg.yml` (defaults to `ubuntu-24.04`).
- `.github/workflows/build-ffmpeg_indocker.yml`
  - Container-based build workflow.
- `.github/workflows/build-ffmpeg_indocker_dev.yml`
  - Dev wrapper around `build-ffmpeg_indocker.yml` (defaults to `ubuntu:devel`).
- `.github/workflows/call-build.yml`
  - Scheduled/matrix caller workflow.

## Important compatibility behavior

The workflows accept inputs like:

- `https://git.ffmpeg.org/gitweb/ffmpeg.git`
- `https://git.ffmpeg.org/gitweb/ffmpeg.git/shortlog/refs/heads/masterd`

They will normalize the URL to a cloneable Git endpoint and then build the `master` branch.
If `masterd` is provided by mistake as the branch, it is auto-corrected to `master`.

## Manual usage

From GitHub Actions, run one of:

- `Build FFmpeg`
- `Build FFmpeg (Dev)`
- `(Docker) Build FFmpeg`
- `(Docker) Build FFmpeg (Dev)`

Key inputs:

- `repo_url`: upstream source URL.
- `ffmpeg_branch`: branch name, default `master`.
- `test_seconds`: AV1 test duration, default `8`.
- `runner_image`: GitHub Actions runner image.
- `container_image`: container image for docker workflow.
- `create_release`: whether to create/update release.

## Output

Each run provides:

- FFmpeg install tarball.
- AV1 test metrics (`av1_metrics.txt`).
- Build logs (`configure.log`, `make.log`, encoder list).
- Optional GitHub Release with assets.
