# Release Process

Vaner releases should fail before a tag exists, not while a release tag is publishing.

## Preflight

Run the local preflight before opening or merging a release PR:

```bash
make release-preflight VERSION=0.8.9
```

Then run the GitHub `Release Preflight` workflow on the exact commit that will be tagged. The preflight workflow builds artifacts, validates release manifests, checks required status names, and scans public release material without publishing package, image, extension, or GitHub Release assets.

## Tagging

Create and push a release tag only after:

- normal PR checks are green;
- local `make release-preflight VERSION=<version>` is green;
- the GitHub `Release Preflight` workflow is green on the target commit;
- benchmark evidence required by the current benchmark policy is attached or referenced;
- release notes and public reports have passed leak scanning.

## After Tag

The tag workflows publish artifacts and verify signatures, attestations, provenance, package assets, image assets, and extension assets. Do not announce a release until the tag workflows are green and the GitHub Release asset list matches the expected manifest.
