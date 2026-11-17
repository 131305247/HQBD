# CVDP Benchmark 运行命令清单

> 环境：Windows 11 + Git Bash + Docker Desktop（确保 `docker` 在 PATH 中）
> venv：在 `evaluators/cvdp/` 下创建本地 venv（Python 3.12）
> 模型：tenyunc GLM-5.2（已写 `tenyunc_factory.py`，`.env` 已配 `CUSTOM_MODEL_FACTORY`/`TENYUNC_*`/`DEFAULT_MODEL`）
> 镜像：`nvidia/cvdp-sim:v1.0.0`（`docker/Dockerfile.sim.local` 构建，含 iverilog/yosys/verilator）

---

## 0. 每次开新 shell 的前置（PATH + 激活 venv）

```bash
cd <HQBD_v0.1.0_ROOT>/evaluators/cvdp
source .venv/Scripts/activate
```

---

## 1. 确认镜像已就绪（构建完成后先跑这个）

```bash
docker images nvidia/cvdp-sim
# 期望看到：nvidia/cvdp-sim   v1.0.0   <imageid>   ...
```

---

## 2. 黄金解冒烟测试（不要 API key，验 Docker+仿真链路，应 100% pass）

用带 `_with_solutions` 的示例数据集，跑非商业的代码生成题（lfsr，1 道）：

```bash
./run_benchmark.py \
  -f example_dataset/cvdp_v1.1.0_example_nonagentic_code_generation_no_commercial_with_solutions.jsonl

# 看结果
cat work/report.txt
```

> 不带 `-l` = 黄金模式：框架用数据集里的 `output`（golden RTL）+ `harness`（cocotb 测试）在 Docker 里跑仿真。
> 期望：1/1 pass。若 pass 说明 Docker→镜像→cocotb→iverilog 整条链路通。

如果失败，单题调试：
```bash
./run_benchmark.py \
  -f example_dataset/cvdp_v1.1.0_example_nonagentic_code_generation_no_commercial_with_solutions.jsonl \
  -i cvdp_copilot_lfsr_0001 \
  -p work_debug
# 进 work_debug/cvdp_copilot_lfsr_0001/harness/1/ 看 docker 日志
```

---

## 3. GLM 评测（用 tenyunc GLM-5.2 跑同一道 lfsr 题）

```bash
./run_benchmark.py \
  -f example_dataset/cvdp_v1.1.0_example_nonagentic_code_generation_no_commercial_with_solutions.jsonl \
  -l -m GLM-5.2 \
  -p work_glm

# 看结果
cat work_glm/report.txt
```

> `-l` = LLM 模式；`-m GLM-5.2` 经 `tenyunc_factory.py`（前缀 `glm` 命中）路由到 tenyunc 端点。
> factory 路径已在 `.env` 的 `CUSTOM_MODEL_FACTORY` 里，所以不必加 `-c`。若想显式指定：
> ```bash
>   -c tenyunc_factory.py
> ```

---

## 4. 多采样统计（Pass@k，更可靠；示例集只有 1 道题，n 次采样）

```bash
./run_samples.py \
  -f example_dataset/cvdp_v1.1.0_example_nonagentic_code_generation_no_commercial_with_solutions.jsonl \
  -l -m GLM-5.2 -n 5 -k 1 \
  -p work_glm_composite

cat work_glm_composite/composite_report.txt
```

> `-n 5` = 每题采 5 次，`-k 1` = 算 Pass@1。

---

## 5. 代码理解题（可选，需 LLM 主观评分，会多耗 token）

```bash
./run_benchmark.py \
  -f example_dataset/cvdp_v1.1.0_example_nonagentic_code_comprehension_with_solutions.jsonl \
  -l -m GLM-5.2 \
  -p work_glm_comp

cat work_glm_comp/report.txt
```

> 主观评分会再调一次 LLM（`sbj_score`），也走 tenyunc。注意 token 消耗翻倍。

---

## 6. Agentic 流程（可选，需先 build agent 镜像）

```bash
# 构建示例 agent 镜像（仓库自带的是占位 agent，会失败，仅验流程）
cp -r examples/agent ./my-agent
cd my-agent && ./build_agent.sh && cd ..

# 黄金解冒烟（验 agentic harness）
./run_benchmark.py \
  -f example_dataset/cvdp_v1.1.0_example_agentic_code_generation_no_commercial_with_solutions.jsonl

# 用 agent 跑（注意：示例 agent 是故意写坏的，预期低通过率）
./run_benchmark.py \
  -f example_dataset/cvdp_v1.1.0_example_agentic_code_generation_no_commercial_with_solutions.jsonl \
  -l -g cvdp-example-agent \
  -p work_agent
```

---

## 常见坑速查

| 现象 | 原因 | 解决 |
|---|---|---|
| `docker: command not found` | 新 shell 没加 PATH | 见第 0 步 export PATH |
| 拉 docker hub 镜像超时 | 国内网络 | `~/.docker/daemon.json` 已配 registry-mirrors |
| `autoconf.sh: : not found` | git 把脚本转成 CRLF | `git -c core.autocrlf=false clone` 重拉（已处理） |
| 容器内 git clone 超时 | 容器访问 github 不可达 | 用 `Dockerfile.sim.local`（COPY 本地源码） |
| `Unable to create Chat Model` | OPENAI_USER_KEY 没设 | `.env` 已设 tenyunc key |
| `Unsupported model type: GLM-5.2` | factory 没加载 | 确认 `.env` 的 `CUSTOM_MODEL_FACTORY` 路径正确 |

---

## 退出 venv

```bash
deactivate
```
