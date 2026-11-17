# evaluators/

所有评测脚本都放在本目录下，一个后端一个子目录：

```text
evaluators/
├── cvdp/    # CVDP runner（上游 rtl-benchmark）
└── hwe/     # HWE-bench harness（上游 hwe-bench-main）
```

`hqbd_bench` 只通过 manifest 的 `backend.root` 找到它们，不把评测逻辑写进
编排层。manifest 位于 `benchmarks/`。

## cvdp/

CVDP 原生 runner，入口是 `run_benchmark.py`。本地配置放在
`evaluators/cvdp/.env`（从 `.env.example` 复制，包含 `OSS_SIM_IMAGE` 与模型
凭据），不要提交。依赖见 `requirements.txt`。

`docker/_sources` 是预置的 iverilog / yosys / verilator 源码快照，供
`docker/Dockerfile.sim.local` 离线构建仿真镜像。快照不带 `.git`；如需更新版本，
重新 clone 后运行 `docker/prepare_sources.sh`。

## hwe/

HWE-bench harness，包含四段流程：

```text
hwe_bench.harness.harbor.adapter
  -> harbor run
  -> hwe_bench.harness.harbor.verify_bridge
  -> hwe_bench.harness.evaluator
```

`deps/harbor` 是随包携带的 Harbor 快照，用下面的命令安装：

```bash
uv tool install --editable ./evaluators/hwe/deps/harbor --force
```

镜像预拉脚本在 `scripts/pull_images.sh`，数据集路径通过 `--dataset` 指向
`datasets/hqbd_v0.1.0/` 下的 per-repo 文件。

## 加新评测器

1. 新建 `evaluators/<name>/`，放入可执行入口或 Python 包；
2. 在对应 manifest 中把 `backend.root` 指向 `evaluators/<name>`，
   `backend.python` / `backend.harbor` 写 argv 列表；
3. 如果后端产物格式特殊，再在 `benchmarks/plugins/` 加一个 adapter。

评测器自己生成的 `.venv/`、`__pycache__/` 和运行产物都被 `.gitignore` 忽略。
