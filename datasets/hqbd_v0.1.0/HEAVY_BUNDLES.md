# Heavy Agentic Bundles

The 100 CVDP heavy agentic cases (75 no-commercial + 25 commercial) refer to
external git repositories. The 40 bundle files total about 1.37 GB and are
**not duplicated** in `hqbd_v0.1.0/`.

## Location

```text
../hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/
```

Relative to this release directory, that is the `hqbd_v0.0.1` directory that
sits alongside `hqbd_v0.1.0` in the HQBD repository. Each bundle is named after
its `context.repo` value plus `.bundle`; see `BUNDLE_MAPPING.csv`.

## Using the bundles

```bash
export CVDP_HEAVY_REPOS_PATH=/path/to/HQBD/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public
```

Then run the heavy JSONL files through the CVDP runner as usual.

## Verifying bundles

```bash
git bundle verify "$CVDP_HEAVY_REPOS_PATH/hqbd_agentic_heavy_rvx.bundle"
```

A real bundle begins with:

```text
# v2 git bundle
```

A Git LFS pointer begins with:

```text
version https://git-lfs.github.com/spec/v1
```

If a checkout materialized pointers instead of bundles, run `git lfs pull` in
the HQBD repository and verify again. The release validator rejects pointer
files.

## Portability note

A copy of `hqbd_v0.1.0/` alone is not sufficient for the heavy split. Keep
`hqbd_v0.0.1/` available, or copy the bundle directory next to the new release
and update your `CVDP_HEAVY_REPOS_PATH` accordingly.
