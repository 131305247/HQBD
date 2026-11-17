# Upstream repositories in the HWE-bench portion

The HWE-bench dataset is licensed Apache-2.0 at the dataset level (see
`../LICENSE-HWE-bench`). It is built from pull requests in the repositories
below. The license of each repository, and of the code in each patch and
context, was **not verified** while producing this package.

| org/repo | Cases | Repository |
|---|---:|---|
| `lowRISC/opentitan` | 245 | https://github.com/lowRISC/opentitan |
| `OpenXiangShan/XiangShan` | 54 | https://github.com/OpenXiangShan/XiangShan |
| `lowRISC/ibex` | 35 | https://github.com/lowRISC/ibex |
| `openhwgroup/cva6` | 35 | https://github.com/openhwgroup/cva6 |
| `chipsalliance/rocket-chip` | 32 | https://github.com/chipsalliance/rocket-chip |
| `chipsalliance/caliptra-rtl` | 16 | https://github.com/chipsalliance/caliptra-rtl |

Each record carries the upstream base commit in `base.sha`. To verify a
repository's terms, fetch it at that commit and read its top-level LICENSE or
COPYING file. Record the result before using the content commercially.

## Status

| Item | Status |
|---|---|
| HWE-bench dataset license | Apache-2.0 (LICENSE-HWE-bench) |
| Upstream repository licenses | Not verified in this package |
| Upstream NOTICE files | Not collected |
| Redistribution review | Pending for each repository |
| Commercial use of upstream code | Not established by the dataset-level license |

If you rely on this release for commercial work, perform your own license
review. The `upstream_unverified` value in the unified index marks these cases.
