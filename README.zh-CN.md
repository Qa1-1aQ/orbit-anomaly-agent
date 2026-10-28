# orbit-anomaly-agent — 航天器轨道异常自主处置智能体

[English](README.md) | 简体中文

<div align="center">

**让大模型真正"跑"轨道异常处置，而不只是"聊"处置。**

一个智能体：你用自然语言下任务（"轨道在衰减、附近有碎片、剩 50 kg 燃料——处置一下"），它自己完成可验证的完整闭环：感知 → 诊断 → 规划 → 执行 → 验证 → 自愈。

<img src="https://img.shields.io/badge/状态-可提交-2F4F4F" alt="状态：可提交" />
<img src="https://img.shields.io/badge/底座-Agnes%20Harness-339933" alt="底座：Agnes Harness" />
<img src="https://img.shields.io/badge/模型-agnes--3.0--flash-3b8fff" alt="模型：agnes-3.0-flash" />
<img src="https://img.shields.io/badge/工具-11%20个%20MCP%20工具-ff9900" alt="11 个 MCP 工具" />
<img src="https://img.shields.io/badge/可复现-true-00aa55" alt="可复现：true" />

**团队：** 逐星小队 · 本科生组 · 编号 U390 · 队长：徐海铭

</div>

<p align="center">
  <img src="orbit-agent/output/orbit_figure.svg" alt="轨道示意图：抬升 40 km、55% 推力故障自愈、规避诚实 FAIL" width="640" />
</p>
<p align="center"><sub>闭环结果：抬升 +40 km → 466.558 km（Δv 误差 0.000%）；55% 推力故障自动恢复；规避后最小距离 15.0 km < 50 km 阈值，如实标 FAIL。</sub></p>

<details>
<summary><kbd>目录</kbd></summary>

- [1. 项目介绍](#1-项目介绍)
  - [项目背景](#项目背景)
  - [核心功能](#核心功能)
  - [创新亮点](#创新亮点)
- [2. 视频介绍](#2-视频介绍)
- [3. 架构介绍](#3-架构介绍)
  - [技术架构](#技术架构)
  - [核心模块（11 个 MCP 工具）](#核心模块11-个-mcp-工具)
  - [实现方式](#实现方式)
- [4. 集成标准](#4-集成标准)
  - [接口规范（MCP over stdio）](#接口规范mcp-over-stdio)
  - [技术标准](#技术标准)
  - [集成方式（在 AGH 上挂载 MCP 服务）](#集成方式在-agh-上挂载-mcp-服务)
- [5. 本地复现](#5-本地复现)
- [6. 提交材料](#6-提交材料)
- [许可与第三方](#许可与第三方)

</details>

---

## 1. 项目介绍

### 项目背景

低轨航天器（如国际空间站，轨道高度约 400 km 量级）在轨长期面临两类持续性威胁：一是**大气阻力导致的轨道衰减**，轨道高度缓慢下降、再入风险上升；二是**空间碎片（失效卫星、火箭末级、解体碎片）的接近甚至碰撞风险**。这两类异常常常叠加出现——既在缓慢掉高度，又有碎片在逼近。

传统处置依赖地面测控：工程师研判遥测、制订机动方案、上行指令、再等待执行反馈，整条"发现—研判—规划—执行—确认"链路存在**分钟到小时级时延**，在紧急避撞窗口下往往来不及。将异常处置能力前移到智能体侧、实现自主闭环，是航天工程的重要方向。

### 核心功能

给定一段自然语言任务，智能体自主完成完整的轨道异常处置闭环：

- **轨道状态计算与预报**——解算位置/速度/平均高度，并用二体模型向前传播。
- **机动规划与执行**——规划并执行轨道抬升 / 碰撞规避脉冲（给出标称 Δv 与燃料）。
- **交会（接近）检测**——逐目标给出最小距离、最接近时刻（TCA）与风险等级。
- **自主闭环与故障自愈**——检测 → 诊断 → 规划 → 执行 → 验证，并**在出错时自主重规划**。
- **量化验证**——蒙特卡洛鲁棒性与故障自愈扫描。
- **可视化**——轨道变化与接近几何示意图。

### 创新亮点

1. **真计算，不编数。** 所有关键数值（轨道传播、Δv、燃料、最小距离）均来自确定性求解器（SGP4、二体、活力公式、火箭方程、枚举搜索）。模型只做编排，不做数值生成；并用活力公式、火箭方程独立复核——**Δv 误差 0.000%、燃料误差 0.006%**。
2. **自主闭环 + 故障自愈。** "感知—诊断—规划—执行—验证—恢复"链路可自主跑完。注入约 55% 推力故障后，智能体自主检测偏差、重规划并补加脉冲，最终命中目标高度并标记 `recovered=true`——与只会说"建议再推一次"的通用大模型形成本质区别。
3. **工具化封装。** 轨道力学能力拆成 **11 个 MCP 工具**供模型调用，既避免幻觉，又让每一步决策可追溯、可验证。
4. **边界诚实。** 指标达不到阈值时如实判 **FAIL**（如规避后最小距离 15.0 km < 50 km 要求）并说明物理原因，不粉饰。
5. **可复现。** 固定 TLE 快照 + 固定历元 + 固定种子，`REPRODUCE_MANIFEST.json` 标记 `reproducible=true`，评委可一键复现（`reproduce.py`）。

---

## 2. 视频介绍

> 🎬 **演示视频：** _待发布_ —— **[TODO：在此填入视频链接]**（YouTube / Bilibili / 网盘）。

视频展示"一个问题触发完整闭环"：工具调用链滚动、55% 推力故障自愈片段、蒙特卡洛结果、生成的轨道示意图。执行记录与工具调用链截图见 [`orbit-agent/output/运行证据.pdf`](orbit-agent/output)。

---

## 3. 架构介绍

### 技术架构

```
自然语言任务（用户）
        │
        ▼
AGH（Agnes Harness —— "大脑"：Agnes 模型 agnes-3.0-flash，
     经 OpenAI 兼容接口内部调用）
        │  MCP（stdio，长连接复用子进程）
        ▼
orbit-agent 工具层（FastMCP 服务，11 个工具）   ← 本项目
        │
        ▼
确定性求解器（SGP4 轨道传播 / 二体动力学 /
     活力公式 / 火箭方程 / 规避枚举搜索）
        │
        ▼
结构化数值（状态、根数、Δv、燃料、最小距离、TCA、PASS/FAIL）
        │
        ▼
Agnes 读取结果 → 决策下一步 → 闭环
```

- **底座硬性要求**：基于 AGH，且模型仅限 Agnes（符合赛事规定）。
- **可复现锚点**：固定 ISS 快照（`FIXED_ISS_TLE`，平均高度 **426.558 km**）+ 固定历元（`2026-10-10T12:00:00.000`）+ 固定随机种子。换输入（其他卫星 / 实时 TLE / 不同目标高度）仍可重算，但默认钉死快照以保证可复现。

### 核心模块（11 个 MCP 工具）

| 工具 | 作用 |
|------|------|
| `get_tle(target)` | 获取目标 TLE（Celestrak → 公开镜像回退；全失败才回退内置 ISS 样例并如实标注） |
| `get_state(tle, epoch)` | TLE → TEME 状态 (r/v) + 轨道根数 |
| `propagate(state, minutes)` | 二体传播指定分钟，返回新状态 |
| `plan_maneuver_tool(state, objective, params)` | 规划抬升 / 碰撞规避机动方案（含 Δv） |
| `apply_maneuver_tool(state, maneuver)` | 施加脉冲并返回最终状态 |
| `conjunction(objects, primary_state, …)` | 计算最小接近距离、TCA、风险等级（支持多目标编目） |
| `verify_tool(…)` | 对比目标与结果，输出量化指标与判定 |
| `run_scenario_tool(scenario)` | **一键跑通完整闭环**：`raise` / `collision` / `anomaly_response` |
| `visualize_tool(scenario)` | 跑通场景并生成轨道 / 接近几何 SVG 示意图 + 指标摘要 |
| `analyze_tool(scenario)` | **理论 vs 仿真核对**：用活力公式 / 火箭方程独立复算，返回相对误差 |
| `simulate_tool(experiment, n)` | **仿真实验**：蒙特卡洛鲁棒性 / 策略对比 / 故障自愈扫描 → 统计与图表 |

`run_scenario_tool` 三类场景：
- `raise`（轨道抬升）：目标平均高度达标验证。
- `collision`（碰撞规避）：枚举搜索最优规避脉冲，最小接近距离达标验证。
- `anomaly_response`（复合异常自主处置，作品核心）：同时处置"轨道衰减 + 碰撞风险"，支持故障注入 `inject_fault`：
  - `thruster_underperformance`：推进器效能异常 → 自主重规划第二次脉冲完成高度目标（**故障自愈**）；
  - `fuel_exhausted`：燃料耗尽 → 优雅降级拒止执行；
  - `data_unavailable`：数据获取失败 → 降级为内置样例仍完成处置。
  - 返回 `decision_trace`（检测→诊断→规划→执行→验证→重规划 全链路）与 `overall_passed`。

### 实现方式

- **工具层**：`orbit-agent/tools/` 中的纯函数，由 `tools/server.py`（FastMCP）包装，通过 MCP stdio 暴露 11 个工具。
- **确定性求解器**：`tle.py`（SGP4 + TLE 解析）、`kepler.py`（状态⇄根数、通用变量法二体传播）、`maneuver.py`、`conjunction.py`、`verify.py`、`mission.py`（场景编排）、`analysis.py`（理论 vs 仿真核对）、`simulate.py`（蒙特卡洛）、`viz.py` / `diagram.py`（SVG 渲染，无绘图依赖）。
- **闭环编排**：`mission.run_scenario` 将工具串成完整 FDIR 闭环，含复合异常自主处置与故障自愈。
- **可复现**：`reproduce.py` 跑测试 + 出图 + 仿真，并写出 `output/REPRODUCE_MANIFEST.json`（关键指标 + 文件 SHA256）。

---

## 4. 集成标准

### 接口规范（MCP over stdio）

- **传输方式**：MCP（Model Context Protocol）基于 **stdio**，作为**长连接复用子进程**（一个服务进程，跨调用复用）。
- **服务入口**：`run_mcp.sh`（Linux/WSL）/ `run_mcp.bat`（Windows）——路径无关，自动定位项目根与 `.venv`。
- **工具契约**：每个工具为单一职责函数，带 JSON Schema（类型化输入 → 结构化 JSON 输出）。命名以 `<动词>_<对象>_tool` 为宜（如 `plan_maneuver_tool`）。
- **安全**：底座默认**禁止 spawn 任意外部可执行**。挂载前必须先把 MCP 服务加入资源白名单 `AGNES_RESOURCE_MCP_POLICY`（见下）。

### 技术标准

- **模型 API**：Agnes `agnes-3.0-flash`，OpenAI 兼容（`base_url https://api.agnes-ai.cn/v1`），由 AGH 内部调用。
- **数值标准**：SGP4（轨道传播）、二体动力学（状态传播）、活力公式（Δv）、齐奥尔科夫斯基火箭方程（燃料）、枚举搜索（规避）。为可复现采用固定输入。
- **验证标准**：`verify_tool` 以容差（±5 km / ≥10 km）判定 PASS/FAIL；`analyze_tool` 返回相对解析基线的误差。

### 集成方式（在 AGH 上挂载 MCP 服务）

> 评委最容易漏掉的关键一步是 **MCP 可执行白名单 + 载入**。`<ROOT>` = 你 clone / 解压到的项目根目录。

**第 1 步 —— 准备 orbit-agent 运行环境**

```bash
cd <ROOT>
python -m venv .venv
.venv/Scripts/activate            # Windows；或 source .venv/bin/activate（Linux/WSL）
pip install -r requirements.txt
chmod +x run_mcp.sh               # 仅 Linux/WSL
```

**第 2 步 —— 带 MCP 白名单启动 AGH（关键）**

```bash
cd agnes-harness
export AGNES_RESOURCE_MCP_POLICY='{"allowedExecutables":["<ROOT>/run_mcp.sh"]}'
node packages/cli/dist/local/agnes.mjs serve
```

保留此终端，它会打印 loopback URL（如 `http://127.0.0.1:4177/`）。浏览器打开 `http://localhost:4177/` 即可进入 Web UI。

**第 3 步 —— 把 orbit-agent 注册为 AGH 的 MCP 工具**

```bash
cd agnes-harness
node packages/cli/dist/local/agnes.mjs mcp add orbit-anomaly-agent \
  --name "Orbit Anomaly Agent" --stdio <ROOT>/run_mcp.sh
node packages/cli/dist/local/agnes.mjs mcp get orbit-anomaly-agent   # 复制 revision
node packages/cli/dist/local/agnes.mjs mcp trust orbit-anomaly-agent --expected-revision <REV>
node packages/cli/dist/local/agnes.mjs mcp enable orbit-anomaly-agent --expected-revision <REV>
node packages/cli/dist/local/agnes.mjs mcp tools orbit-anomaly-agent  # 应列出 11 个工具
```

**第 4 步 —— 跑通第一个闭环**

在 AGH 中给 Agnes 下达指令（中英文均可）：

```
检测到卫星轨道异常：请用 run_scenario_tool 规划并执行「轨道抬升」任务，
把轨道平均高度抬高约 40 km，随后用 verify_tool 验证入轨精度，并报告 Δv 与燃料消耗。
```

预期：AGH 调用 `run_scenario_tool` → `verify_tool` → 输出 **PASS**。完整能力可让 Agnes 跑 `run_scenario_tool(scenario="anomaly_response", inject_fault="thruster_underperformance")`，触发"55% 推力故障 → 自主补脉冲自愈 → 规避诚实 FAIL"完整链路。

---

## 5. 本地复现

```bash
cd orbit-agent
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
python tests/run_suite.py          # 测试套件
python reproduce.py                # 测试 + 出图 + 仿真 → output/REPRODUCE_MANIFEST.json
```

固定历元 + 固定 ISS 快照（`FIXED_ISS_TLE`），统一抬升 **+40 km**（目标 = 426.558 + 40 = **466.558 km**），完全可复现。参考结果：抬升 Δv 解析值 vs 仿真值相对误差 ≈ **0.000%**、燃料误差 < 0.1%；`anomaly_response` 在 55% 推力故障下首脉冲欠交付 → 验证检出 → **重规划自愈**（`recovered=true`）；碎片规避 ≥50 km 因共轨几何物理封顶（≈15 km）**如实判 FAIL**。

---

## 6. 提交材料

四份赛事提交 PDF 位于仓库根目录：

- `项目说明.pdf` — 项目说明（名称 / 方向 / 摘要 / 问题来源与目标）
- `技术信息.pdf` — 技术信息（AGH 作用、Agnes 模型、数据/代码/专业软件/仿真环境/API/设备接口）
- `成员声明与分工.pdf` — 成员声明与分工
- `运行证据.pdf` — 运行证据（AGH 执行记录、工具调用链、Agnes 参与核心任务、关键配置与验证结果）

详细证据位于 `orbit-agent/output/`（`仿真实验报告.md`、`AGH闭环测试报告.md`、`validation_table.md`、`REPRODUCE_MANIFEST.json`、各图表）。

---

## 许可与第三方

为 *2026 年江苏省 AI+科学与工程创新实践黑客松（高校组）* 参赛作品。第三方：sgp4（LGPL/MIT）、astropy（BSD-3）、numpy（BSD-3）、FastMCP（MIT）、Celestrak 公开 TLE 数据、Agnes Harness（AGH）、Agnes 模型。
