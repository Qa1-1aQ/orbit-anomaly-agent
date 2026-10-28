# 轨道异常处置智能体（orbit-agent）

## 一、这是什么

一个用 **AGH（Agnes Harness）底座 + Agnes 模型**驱动的航天器轨道异常处置智能体。
用户用自然语言下达任务，由 Agnes 模型做推理调度，调用本项目的 **MCP 工具层**完成真实的轨道力学计算，
形成「异常感知 → 规划机动 → 执行 → 验证 → 异常处理（自愈）」的完整闭环。

> 设计原则：**真计算、可复现、边界诚实**。所有轨道数值由确定性求解器（SGP4 / 二体 / 活力公式 / 火箭方程 / 枚举搜索）算出，不靠大模型编数；固定基线保证评委可复现；该判 FAIL 就判 FAIL，不伪造达成。

## 二、框架

```
用户自然语言
    │
    ▼
AGH（大脑：Agnes 模型 agnès-3.0-flash，OpenAI 兼容 API 内部调用）
    │  MCP (stdio)
    ▼
orbit-agent 工具层（FastMCP server，11 个工具）   ← 本项目
    │
    ▼
确定性求解器（SGP4 轨道传播 / 二体动力学 / 活力公式 / 火箭方程 / 规避枚举搜索）
    │
    ▼
结构化数值（状态、根数、Δv、燃料、最小距离、TCA、PASS/FAIL）
    │
    ▼
Agnes 读取结果 → 决策下一步 → 闭环
```

- **底座硬性要求**：使用 AGH，且模型仅限 Agnes（符合赛事规定）。
- **可复现锚点**：固定 ISS 快照（`FIXED_ISS_TLE`，平均高度 426.558 km）+ 固定历元 + 固定随机种子。换输入（其他卫星 / 实时 TLE / 不同目标高度）仍可重算，但默认钉死快照以保证可复现。

## 三、目录结构

```
orbit-agent/
├── requirements.txt          # 依赖（sgp4 / astropy / numpy / requests / fastmcp）
├── .env.example              # Agnes API Key 模板（仅 AGH 侧需要）
├── .mcp.json.example         # 通用 MCP 客户端（Claude Desktop / VS Code）配置模板
├── run_mcp.sh / run_mcp.bat  # 启动器（路径无关，自动定位项目根与 venv）
├── sync_wsl.sh               # WSL 同步脚本（Windows ↔ WSL 软链）
├── reproduce.py              # 一键复现：测试 + 出图 + 仿真 + 写 REPRODUCE_MANIFEST.json
├── package.py                # 提交打包：生成 组别-编号-队名-队长.zip（≤200MB）
├── 项目说明.pdf / 技术信息.pdf / 成员声明与分工.pdf / 运行证据.pdf  # 提交表单附件（4 份，位于根目录）
├── tools/                    # 仿真工具层（纯函数 + MCP 服务入口）
│   ├── tle.py                # TLE 获取(Celestrak→公开镜像多源回退) + SGP4 解析 + 状态提取
│   ├── kepler.py             # 状态↔轨道根数 / 通用变量法二体传播
│   ├── maneuver.py           # plan_maneuver / apply_maneuver
│   ├── conjunction.py        # 空间目标接近分析 (TCA / 最小距离 / 风险)
│   ├── verify.py             # 闭环验证（量化指标 + PASS/FAIL）
│   ├── mission.py            # run_scenario：把工具串成完整闭环（含复合异常自主处置）
│   ├── analysis.py           # 理论 vs 仿真核对（活力公式 / 火箭方程 / B-star 衰减）
│   ├── simulate.py           # 仿真实验（蒙特卡洛 / 策略对比 / 规避对比 / 故障自愈 / 真实标定）
│   ├── viz.py                # 渲染轨道 / 接近几何 SVG 示意图（纯 Python，无绘图依赖）
│   ├── diagram.py            # 渲染「异常处置闭环(FDIR)」流程图 SVG
│   ├── make_figures.py       # 生成技术验证图表 + 流程图（matplotlib）
│   └── server.py             # FastMCP 服务入口（暴露给 AGH 的 11 个工具）
├── tests/
│   ├── local_loop.py         # 不依赖 AGH/Agnes 的本地闭环测试
│   ├── run_suite.py          # 测试套件（生成 tests/测试报告.md）
│   └── smoke_mcp.py          # MCP 挂载冒烟测试
└── output/                   # 生成的交付物与证据（打包时一并收入）
    ├── orbit_figure.svg      # 主示意图（抬升 + 55% 故障自愈 + 规避）
    ├── figures/              # 5 张技术验证图
    ├── sim/                  # 5 张仿真实验图
    ├── preview/              # 闭环流程图 + 轨道示意图（当前基线）
    ├── evidence_shots/       # 运行证据截图（AGH 执行 / 调用链 / 验证结果）
    ├── 仿真实验报告.md        # 5 组仿真统计与对比
    ├── AGH闭环测试报告.md     # 完整闭环执行记录（decision_trace）
    ├── validation_table.md   # 理论值 vs 仿真值 核对表
    ├── stress_test_report.md # 压力测试报告
    ├── 测试样例.md            # 单一端到端测试样例
    └── REPRODUCE_MANIFEST.json  # 复现清单（关键指标 + 文件 SHA256）
```

## 四、十一个可被 AGH 调用的工具（MCP stdio）

| 工具 | 作用 |
|------|------|
| `get_tle(target)` | 从 Celestrak 拉取目标 TLE（官方 403 时自动切公开镜像；全失败才回退内置 ISS 样例并如实标注） |
| `get_state(tle, epoch)` | TLE → TEME 状态 (r/v) + 轨道根数 |
| `propagate(state, minutes)` | 二体传播指定分钟，返回新状态 |
| `plan_maneuver_tool(state, objective, params)` | 规划抬升 / 碰撞规避机动方案（含 Δv） |
| `apply_maneuver_tool(state, maneuver)` | 施加脉冲并返回最终状态 |
| `conjunction(objects, primary_state, ...)` | 计算最小接近距离、TCA、风险等级（支持多目标编目） |
| `verify_tool(...)` | 对比目标与结果，输出量化指标与判定 |
| `run_scenario_tool(scenario)` | **一键跑通完整闭环**，支持 `raise` / `collision` / `anomaly_response` 三类场景 |
| `visualize_tool(scenario)` | 跑通场景并生成轨道 / 接近几何 SVG 示意图，返回图片路径与指标摘要 |
| `analyze_tool(scenario)` | **理论 vs 仿真核对**：用活力公式 / 火箭方程独立复算解析基线，返回相对误差（可信度背书） |
| `simulate_tool(experiment, n)` | **仿真实验**：蒙特卡洛鲁棒性 / 机动策略对比 / 规避策略对比 / 故障自愈扫描，返回统计与图表路径 |

`run_scenario_tool` 的三类场景：
- `raise`（轨道抬升）：目标平均高度达标验证。
- `collision`（碰撞规避）：枚举搜索最优规避脉冲，最小接近距离达标验证。
- `anomaly_response`（复合异常自主处置，作品核心）：同时处置「轨道衰减 + 碰撞风险」，支持故障注入 `inject_fault`：
  - `thruster_underperformance`：推进器效能异常 → 自主重规划第二次脉冲完成高度目标（**故障自愈**）；
  - `fuel_exhausted`：燃料耗尽 → 优雅降级拒止执行；
  - `data_unavailable`：数据获取失败 → 降级为内置样例仍完成处置。
  - 返回 `decision_trace`（检测→诊断→规划→执行→验证→重规划 全链路）与 `overall_passed`。

## 五、本地运行（不依赖 AGH，用于调试与生成证据）

```bash
cd orbit-agent
python -m venv .venv && .venv/Scripts/activate      # 或 source .venv/bin/activate（WSL）
pip install -r requirements.txt
python tests/run_suite.py          # 测试套件
python reproduce.py                # 一键复现：测试+出图+仿真，写 REPRODUCE_MANIFEST.json
```

固定历元 + 固定 ISS 快照（FIXED_ISS_TLE），抬升幅度统一 **+40 km**（目标 = 基线 426.558 + 40 = 466.558 km），输出**可复现**。
核对结果：抬升 Δv 解析值与仿真值相对误差 ≈ **0.000%**、燃料误差 < 0.1%；`anomaly_response` 在 55% 推力故障下首脉冲欠交付 → 验证检出 → **重规划自愈**（`recovered=true`）；碎片规避 ≥50 km 因共轨几何物理封顶（≈15 km）**如实判 FAIL**。

## 六、把 MCP 工具挂到 AGH（详细步骤）

> 评委复现 AGH 基础调用通常无障碍，**最关键的是 MCP 白名单注册与载入**。下面给出从零挂上的完整路径；`<ROOT>` 指你解压 / clone 到的项目根目录。

### 6.1 准备 orbit-agent 运行环境

```bash
cd <ROOT>
python -m venv .venv
.venv/Scripts/activate          # Windows；或 source .venv/bin/activate（Linux / WSL）
pip install -r requirements.txt
chmod +x run_mcp.sh             # Linux / WSL 必做；Windows 用 run_mcp.bat 无需此步
```

`run_mcp.sh` / `run_mcp.bat` 已**路径无关**：自动定位项目根与 `.venv`，你把它放哪都行，只要下一步 `--stdio` 指向它的绝对路径。

### 6.2 ⚠️ 关键：带「MCP 可执行白名单」启动 AGH

AGH 出于安全，默认**禁止 spawn 任何外部可执行**。挂 MCP 前必须先用环境变量 `AGNES_RESOURCE_MCP_POLICY` 把 `run_mcp.sh` 加进白名单，**否则挂载会失败（ENOENT / 拒绝 spawn）**。

```bash
cd agnes-harness
# 在「启动 serve 的那个终端」先声明白名单，再 serve
export AGNES_RESOURCE_MCP_POLICY='{"allowedExecutables":["<ROOT>/run_mcp.sh"]}'
node packages/cli/dist/local/agnes.mjs serve
```

启动后终端会打印一个 **loopback URL**（如 `http://127.0.0.1:4177/`），保留此终端不要关。
Windows 用户用浏览器打开 `http://localhost:4177/` 即可进入 Web UI。

### 6.3 把 orbit-agent 注册为 AGH 的 MCP 工具（核心）

另开一个终端（与 6.2 的 `serve` 同机、同 `AGH_HOME` / `AGNES_PROFILE`）：

```bash
cd agnes-harness

# 第 1 步：注册 MCP（--name 必填；--stdio 指向 run_mcp.sh 绝对路径）
node packages/cli/dist/local/agnes.mjs mcp add orbit-anomaly-agent \
  --name "Orbit Anomaly Agent" \
  --stdio <ROOT>/run_mcp.sh

# 第 2 步：取 revision（一串哈希 / 编号），后续 trust / enable 都要用它
node packages/cli/dist/local/agnes.mjs mcp get orbit-anomaly-agent
```

从 `mcp get` 输出复制 **revision**，继续：

```bash
# 第 3 步：信任 + 启用（必须带 --expected-revision，防止挂错版本）
node packages/cli/dist/local/agnes.mjs mcp trust orbit-anomaly-agent --expected-revision <REV>
node packages/cli/dist/local/agnes.mjs mcp enable orbit-anomaly-agent --expected-revision <REV>

# 第 4 步：列出工具，确认 11 个全部注册
node packages/cli/dist/local/agnes.mjs mcp tools orbit-anomaly-agent
```

**成功标志**：第 4 步打印出 11 个工具：`get_tle` / `get_state` / `propagate` / `plan_maneuver_tool` / `apply_maneuver_tool` / `conjunction` / `verify_tool` / `run_scenario_tool` / `visualize_tool` / `analyze_tool` / `simulate_tool`。

### 6.4 跑通第一个闭环

在 AGH 创建 task，给 Agnes 下达指令（中英文均可）：

```
检测到卫星轨道异常：请用 run_scenario_tool 规划并执行「轨道抬升」任务，
把轨道平均高度抬高约 40 km，随后用 verify_tool 验证入轨精度，并报告 Δv 与燃料消耗。
```

预期：AGH 调用 `run_scenario_tool` → 拿到证据 → 用 `verify_tool` 核对 → 输出 **PASS** 结论。
AGH 会自动记录每步的「模型调用 / 工具调用 / 审批」时间线，这就是提交所需的 **AGH 执行记录**。

想看完整能力，可让 Agnes 跑 `run_scenario_tool(scenario="anomaly_response", inject_fault="thruster_underperformance")`，
触发「55% 推力故障 → 自主补脉冲自愈 → 规避诚实 FAIL」完整链路。

## 七、注意事项 / 排错

| 现象 | 原因 | 处理 |
|------|------|------|
| 挂 MCP 报 **ENOENT / 拒绝 spawn run_mcp.sh** | 没设 `AGNES_RESOURCE_MCP_POLICY` 白名单 | 在**启动 serve 的那个终端**先 `export AGNES_RESOURCE_MCP_POLICY='{"allowedExecutables":["<ROOT>/run_mcp.sh"]}'`，再 `serve` |
| `mcp tools` 列出 **0 个工具** | `--stdio` 指向的 python 缺依赖 / 服务一启动就崩 | 先本地 `python -m tools.server` 验证；确认 `<ROOT>/.venv` 已 `pip install -r requirements.txt` |
| `permission denied` / `EACCES` on `run_mcp.sh` | 没有执行位（多见于 WSL 软链） | `chmod +x <ROOT>/run_mcp.sh`（在 WSL 终端执行，Windows 侧 chmod 改不动 Linux 权限位） |
| `mcp trust/enable` 报 **revision mismatch** | 服务代码变了 | 重新 `mcp get` 拿新 revision，用新值再 trust / enable |
| 改了 `tools/` 代码后 AGH 仍跑旧结果 | AGH 长连接复用旧 MCP 子进程；WSL 软链下 `.pyc` 不刷新 | ① 重启 AGH `serve` 进程；② `run_mcp.sh` 已内置「启动即清 `__pycache__`」，正常重启即可生效 |
| `node --version` < 24.10 | Node 版本不够 | 升级到 24.10+ |
| AGH 实在挂不上 MCP（极端兜底） | — | 本地 `tests/local_loop.py` 可独立产出完整 `evidence.json` 作为提交证据兜底（分数会受损，建议尽早验证挂载） |

> AGH **不**读取项目里的 `.mcp.json`（那是给 Claude Desktop / VS Code 等通用客户端用的）。挂 AGH 必须用上面的 `mcp add`。

## 八、一键复现与提交打包

```bash
python reproduce.py                # 测试 + 出图 + 仿真，写出 output/REPRODUCE_MANIFEST.json
python package.py                  # 生成 组别-编号-队名-队长.zip（自动排除 venv/__pycache__/.git/.workbuddy/.zip，≤200MB）
```

提交前务必在 `package.py` 顶部填好 组别 / 编号 / 队名 / 队长，再重新打包。ZIP 已含 `run_mcp.sh` / `run_mcp.bat` / `.mcp.json.example` 与全部源码，评委解压后按第六节即可本地拉起。

## 九、模型配置（略）

全程使用 **Agnes 模型 `agnes-3.0-flash`**（OpenAI 兼容 API，`base_url https://api.agnes-ai.cn/v1`，Key 由官方群发放），由 AGH 内部调用，符合「模型仅限 Agnes」要求。AGH 基础安装与模型填写属常规操作，此处不展开。

## 十、第三方来源声明

sgp4（LGPL/MIT）、astropy（BSD-3）、numpy（BSD-3）、FastMCP（MIT）、Celestrak TLE 公开数据、AGH（Agnes Harness）、Agnes 模型。
