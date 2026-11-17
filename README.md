# HQBD v0.1.0 评测包

本目录是 HQBD v0.1.0 的独立可运行包，按“数据、评测器、编排、运行产物”分层。
除 CVDP heavy 任务的 Git bundle 外，其余内容自包含：

```text
HQBD_v0.1.0/
├── datasets/                 # 所有数据集，一个数据集一个子目录
│   ├── hqbd_v0.1.0/          # 发布数据：CVDP 872 + HWE-bench 417
│   └── hqbd_v0.0.1/
│       └── ...heavy_payload/ # 可选：heavy 任务用的 40 个 Git bundle（当前为空）
├── evaluators/               # 所有评测脚本，一个后端一个子目录
│   ├── cvdp/                 # CVDP runner（原 rtl-benchmark）
│   └── hwe/                  # HWE-bench harness（原 hwe-bench-main）
├── benchmarks/               # 数据集 manifest：连接 datasets 与 evaluators
├── hqbd_bench/               # 统一编排入口（只编排，不重写打分）
├── tools/                    # 只读数据工具：打印、统计、抽子集、格式转换
├── docs/                     # 使用文档
├── tests/                    # 35 个合成数据单元测试，不需要 Docker
├── runs/                     # 运行产物，运行时创建，已 gitignore
├── 使用说明书.md             # 安装、配置、运行和排障手册
├── README.md
├── LICENSE
└── NOTICE
```

| 路径 | 内容 |
|---|---|
| `datasets/hqbd_v0.1.0/` | 数据集发布版：统一索引、ID 映射、构建清单、许可证 |
| `datasets/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/` | **可选依赖，当前为空**。放入 40 个 heavy Git bundle（约 1.3 GB）后，100 条 heavy 用例才可运行。**该目录名不能改，它只是 `BUNDLE_MAPPING.csv` 的相对路径落点，不是旧版数据集** |
| `evaluators/cvdp/` | CVDP 上游评测器（已剔除 `.git`、`.env`、本地虚拟环境与运行产物） |
| `evaluators/hwe/` | HWE-bench 上游评测器，含自带的 Harbor 快照 `deps/harbor` |
| `benchmarks/cvdp.json`、`benchmarks/hwe.json` | 两个数据集的 manifest，路径已指向包内 |
| `tools/` | 只读数据工具：打印单题、统计、抽子集、JSONL/YAML 转换、按 ID 合并 |
| `docs/ORCHESTRATOR.md` | 选样、续跑、结果契约与维护命令 |

数据集默认只跑可运行用例：CVDP 749 条（6 组），HWE-bench 172 条（5 组）。
OpenTitan 的 245 条本地构建用例默认排除，需要显式加 `--allow-local-build`。

CVDP 的 749 条中有 100 条 heavy 用例（2 组）需要上述 bundle。当前包未附带
bundle，这 100 条只有在放入 bundle 或手动设置 `CVDP_HEAVY_REPOS_PATH` 后才能
运行；其余 649 条不受影响。需要时按 `datasets/README.md` 恢复。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)（HWE 后端与 Harbor 安装使用）
- Docker（CVDP 仿真与 HWE 每 PR 镜像）
- 运行 HWE 需要目标 agent 的凭据；运行 CVDP 需要模型 API 凭据

如果本机的 `python` 不可用（例如 Windows 应用商店别名），把下面的
`python -m hqbd_bench` 换成 `uv run --no-project python -m hqbd_bench`。

## 首次配置

在本目录下执行：

```bash
cd <HQBD_v0.1.0_ROOT>

# 1. 安装包内自带的 Harbor
uv tool install --editable ./evaluators/hwe/deps/harbor --force

# 2. 准备 CVDP 环境（.env 未随包提供，必须自己创建）
cp evaluators/cvdp/.env.example evaluators/cvdp/.env
#    然后填入 OSS_SIM_IMAGE 和模型 API 键

# 3. 安装 CVDP Python 依赖（或者运行时用 --backend-python 指定解释器）
python -m pip install -r evaluators/cvdp/requirements.txt

# 4. 预拉 HWE 每 PR 镜像（按 repo，可选；数据用包内 per-repo 文件）
./evaluators/hwe/scripts/pull_images.sh ibex \
  --dataset datasets/hqbd_v0.1.0/hqbd_v0.1.0_hwe_bench_repair_by_repo/lowRISC__ibex.jsonl
```

`.env` 含密钥，已被 `.gitignore` 忽略；不要提交或分发。

## 验证

```bash
python -m hqbd_bench dataset list
python -m hqbd_bench dataset validate --all --check-hashes
python -m hqbd_bench doctor --all

# 只打印计划，不写文件
python -m hqbd_bench run cvdp --runnable --dry-run
python -m hqbd_bench run hwe  --runnable --dry-run
```

预期：`dataset validate` 校验 872 + 417 = 1289 条；dry-run 为 CVDP 6 组/749 条、
HWE 5 组/172 条。

未放入 heavy bundle 时，`cvdp.heavy_bundles` 会报 warn。加 `--strict` 会把该
warn 升级为 error，所以基础包用不带 `--strict` 的校验；恢复 bundle 后再用
`--strict` 作为发布门槛。

## 运行

```bash
# CVDP：模式 llm 或 agent
python -m hqbd_bench run cvdp \
  --ids hqbd_agentic_64b66b_codec_0001 \
  --mode llm --model <model> \
  --run-id smoke-cvdp

# HWE：agent 凭据只传变量名，值从当前进程环境读取
python -m hqbd_bench run hwe \
  --ids hqbd_hwe_ibex_2232 \
  --agent claude-code --model <model> \
  --agent-env ANTHROPIC_API_KEY \
  --run-id smoke-hwe

# 续跑、重新汇总、查看报告
python -m hqbd_bench run hwe --run-id smoke-hwe --resume
python -m hqbd_bench collect smoke-hwe
python -m hqbd_bench report smoke-hwe --format md
python -m hqbd_bench runs show smoke-hwe
```

运行产物在 `runs/<run-id>/`：`run.json`、`selection.jsonl`、`events.jsonl`、
`groups/`、`results.jsonl`、`summary.json`、`report.md`。`runs/` 已被忽略。

跑 heavy 用例（例如 `hqbd_agentic_heavy_*`）前，需要先恢复 bundle，或者用
`--env CVDP_HEAVY_REPOS_PATH=<已有 payload 目录>` 指向外部副本。未提供时这些
用例会在阶段执行中失败，不会静默计为通过。

### 恢复 heavy bundle（可选）

```bash
# 从外部 HQBD 开发仓库拷贝（仅 bundle，不涉及旧版数据集）
cp -a <HQBD_DEV_ROOT>/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/. \
      datasets/hqbd_v0.0.1/hqbd_v0.0.1_agentic_heavy_code_generation_public/
```

## 加新数据集

固定三步，互不混放：

1. 数据放到 `datasets/<name>/`；
2. 评测脚本放到 `evaluators/<name>/`（复用已有后端则跳过）；
3. 在 `benchmarks/<name>.json` 写 manifest，声明 `catalog`、`backend.root`、
   `select` 和 `expect`。

命令只依赖 manifest，核心代码不用动：

```bash
python -m hqbd_bench dataset new mybench --adapter generic
python -m hqbd_bench dataset validate mybench --strict
python -m hqbd_bench run mybench --dry-run
```

复杂后端用 `--adapter python` 生成插件骨架。详见 `docs/ORCHESTRATOR.md` 与
`benchmarks/README.md`。

## 安全与完整性

- `datasets/hqbd_v0.1.0/` 中的发布文件与 `BUILD_MANIFEST.json` 的哈希保持一致，不要改写；
- `datasets/hqbd_v0.0.1/` 子目录仅存放 heavy bundle（可选），路径不能改名，否则 `BUNDLE_MAPPING.csv` 失效；
- HWE 的 `fix_patch`（金标补丁）与 `tb_script`（隐藏测试）属于评测材料，不得暴露给被测 agent；
  编排层的 `results.jsonl`、`summary.json`、`report.md` 和日志会自动剔除这些字段；
- 本包未对上游许可证做新的结论，CVDP 与 HWE-bench 的许可证见 `LICENSE`、`NOTICE`
  以及 `datasets/hqbd_v0.1.0/`、`evaluators/` 中的原始许可证文件；
- 完整的安装、配置、运行、结果和故障排查步骤见
  [`使用说明书.md`](使用说明书.md)，编排设计细节见
  [`docs/ORCHESTRATOR.md`](docs/ORCHESTRATOR.md)；
- `LICENSE` / `NOTICE` 里写的 `hqbd_v0.1.0/`、`hqbd_v0.0.1/` 是数据集目录名，
  在本包中位于 `datasets/` 下，许可证正文未作改动。
