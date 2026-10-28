"""MCP 服务入口：把仿真工具层暴露给 Agnes Harness (AGH)。

运行:  python tools/server.py          (默认 stdio 传输，AGH 以 MCP client 挂载)
       python -m tools.server          (等价，需 cwd=项目根)
无论 AGH 从哪个工作目录拉起，本文件都会把项目根加入 sys.path 并改用
绝对导入，保证 tools 包始终可找到。
"""
import os
import sys

# 让本服务无论从哪个工作目录被 AGH 以 stdio 拉起，都能正确找到 tools 包。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import json as _json
import traceback as _tb

from fastmcp import FastMCP

from tools.kepler import rv_to_coe, propagate_rv, now_utc_isot
from tools.tle import fetch_tle, satrec_state_at
from tools.maneuver import plan_maneuver, apply_maneuver
from tools.conjunction import conjunction_check
from tools.verify import verify
from tools.mission import run_scenario
from tools.analysis import analyze
from tools.viz import render_orbit_svg
from tools.simulate import run_experiment

mcp = FastMCP("orbit-anomaly-agent")


def _as_obj(x):
    """AGH 有时会把 dict/list 参数以 JSON 字符串形式传入，这里统一做兼容，
    避免协议层/调用层差异导致工具直接崩溃（如 str 下标取键报 TypeError）。"""
    if isinstance(x, str):
        try:
            return _json.loads(x)
        except Exception:
            return x
    return x


def _propagate(state: dict, minutes: float) -> dict:
    r, v = propagate_rv(state["r_km"], state["v_kms"], minutes * 60.0)
    return {"epoch_utc": state.get("epoch_utc"), "frame": state.get("frame", "TEME"),
            "r_km": r, "v_kms": v, "elements": rv_to_coe(r, v)}


@mcp.tool()
def get_tle(target: str, group: str = "stations", force_live: bool = False) -> dict:
    """从 Celestrak（官方 403 时自动切公开镜像）获取目标 TLE；全失败回退内置样例并如实标注。

    返回 line1/line2/source/epoch_utc/fetched_at。source 为 "celestrak" 或
    "celestrak-mirror:*" 表示实时联网成功；为 "builtin_sample" 表示联网失败已回退，
    并附 fetch_error 说明原因（绝不假装实时）。force_live=True 时跳过会话缓存强制一次
    全新联网拉取（用于诚实证明“此刻真能联网”）。
    """
    return fetch_tle(target, group, force_live=force_live)


@mcp.tool()
def get_state(tle: dict, epoch_utc: str = None) -> dict:
    """给定 TLE，返回「任务场景固定参考历元」下的 TEME 系状态、轨道根数与平均高度。

    【重要口径】整个任务场景（run_scenario / analyze / visualize / simulate 以及本工具）
    统一锚定在 FIXED_EPOCH = 2026-10-10T12:00:00.000，以保证所有数字同源、可复现、可直接对比。
    因此本工具**始终返回该固定参考历元下的状态**，不会随传入的 epoch_utc 改变——
    epoch_utc 参数仅作回声（见返回 requested_epoch），不参与 SGP4 传播。
    若需要其它历元下的轨道状态，请用 propagate 工具。

    高度口径（务必不要混用）：
      - mean_altitude_km  = a − 地球半径，即“平均高度”（报告/规划请用此值）
      - instant_altitude_km = |r| − 地球半径，即“当刻瞬时高度”，近圆轨道下与平均高度相差约 e·a（数公里）
    """
    from tools.constants import R_EARTH
    from tools.tle import FIXED_EPOCH
    # 整个场景统一锚定 FIXED_EPOCH：始终返回该历元下的状态，从根本上消除
    # “同一卫星在报告里出现两种高度”的口径矛盾（elements 内部也保持 a_km 与 mean_altitude_km 同历元）。
    st = satrec_state_at(tle, FIXED_EPOCH)
    if "error" in st:
        return st
    st["elements"] = rv_to_coe(st["r_km"], st["v_kms"])
    mean_alt = round(float(st["elements"]["a_km"]) - R_EARTH, 3)
    inst_alt = round(float(st["elements"]["altitude_km"]), 3)
    # elements 内部自洽（a_km 与 mean_altitude_km 同一历元）
    st["elements"]["mean_altitude_km"] = mean_alt
    st["epoch_used"] = FIXED_EPOCH
    st["reference_epoch"] = FIXED_EPOCH
    st["requested_epoch"] = epoch_utc
    st["mean_altitude_km"] = mean_alt
    st["instant_altitude_km"] = inst_alt
    st["altitude_note"] = (
        "本工具始终返回固定参考历元 " + FIXED_EPOCH + " 下的状态（与 run_scenario/analyze/"
        "visualize/simulate 同源同口径）。报告中的‘平均高度’请一律使用 mean_altitude_km（= a − 地球半径）；"
        "instant_altitude_km（=|r|−地球半径）是当刻瞬时高度，近圆轨道下与平均高度相差约 e·a（数公里），切勿混用。"
        "需要其它历元的状态请用 propagate。")
    return st


@mcp.tool()
def propagate(state: dict, minutes: float) -> dict:
    """二体传播 state 指定分钟数，返回新状态与轨道根数。"""
    try:
        state = _as_obj(state)
        return _propagate(state, minutes)
    except Exception as e:
        return {"_tool_error": f"{type(e).__name__}: {e}",
                "_tool_traceback": _tb.format_exc(),
                "_diag": {"state_type": type(state).__name__}}


@mcp.tool()
def plan_maneuver_tool(state: dict, objective: str, params: dict) -> dict:
    """规划机动。objective: raise_altitude(参数 target_altitude_km) | collision_avoid(参数 current_miss_km, desired_miss_km, tca_minutes)。"""
    try:
        # AGH 有时会把 dict 参数以 JSON 字符串形式传入，这里做兼容，避免协议层/调用层误判
        if isinstance(params, str):
            params = _json.loads(params)
        if isinstance(state, str):
            state = _json.loads(state)
        return plan_maneuver(state, objective, params)
    except Exception as e:
        return {"_tool_error": f"{type(e).__name__}: {e}",
                "_tool_traceback": _tb.format_exc(),
                "_diag": {"objective": objective,
                          "params_type": type(params).__name__,
                          "state_type": type(state).__name__,
                          "state_keys": list(state.keys()) if isinstance(state, dict) else None}}


@mcp.tool()
def apply_maneuver_tool(state: dict, maneuver: dict) -> dict:
    """按机动方案施加脉冲并返回含轨迹的最终状态。"""
    try:
        if isinstance(maneuver, str):
            maneuver = _json.loads(maneuver)
        if isinstance(state, str):
            state = _json.loads(state)
        return apply_maneuver(state, maneuver)
    except Exception as e:
        return {"_tool_error": f"{type(e).__name__}: {e}",
                "_tool_traceback": _tb.format_exc(),
                "_diag": {"state_type": type(state).__name__,
                          "maneuver_type": type(maneuver).__name__}}


@mcp.tool()
def conjunction(objects: list, primary_state: dict,
                horizon_minutes: float = 90.0, step_minutes: float = 1.0) -> dict:
    """评估主航天器与多个目标的最小接近距离 (TCA/风险等级)。objects: [{name, tle?|state?}]。

    primary_state 需含 r_km/v_kms；epoch_utc 缺省时自动使用系统固定历元。
    objects 每项形如 {"name":"...", "state":{r_km,v_kms}} 或 {"name":"...", "tle":{line1,line2}}。
    传入 dict/list 若被包成 JSON 字符串也可自动解析。
    """
    try:
        objects = _as_obj(objects)
        primary_state = _as_obj(primary_state)
        return conjunction_check(primary_state, objects, horizon_minutes, step_minutes)
    except Exception as e:
        return {"_tool_error": f"{type(e).__name__}: {e}",
                "_tool_traceback": _tb.format_exc(),
                "_diag": {"objects_type": type(objects).__name__,
                          "primary_state_type": type(primary_state).__name__,
                          "has_epoch_utc": bool(isinstance(primary_state, dict)
                                                and primary_state.get("epoch_utc"))}}


@mcp.tool()
def verify_tool(initial_state: dict, final_state: dict, objective: str,
                params: dict, dv_events: list = None,
                initial_fuel_kg: float = 50.0) -> dict:
    """闭环验证：对比目标与结果，输出量化指标与 PASS/FAIL 判定。"""
    try:
        initial_state = _as_obj(initial_state)
        final_state = _as_obj(final_state)
        params = _as_obj(params)
        dv_events = _as_obj(dv_events) if dv_events else []
        return verify(initial_state, final_state, objective, params,
                      dv_events or [], initial_fuel_kg)
    except Exception as e:
        return {"_tool_error": f"{type(e).__name__}: {e}",
                "_tool_traceback": _tb.format_exc(),
                "_diag": {"objective": objective,
                          "initial_state_type": type(initial_state).__name__,
                          "final_state_type": type(final_state).__name__,
                          "params_type": type(params).__name__}}


@mcp.tool()
def run_scenario_tool(scenario: dict, tle: dict = None) -> dict:
    """一键跑通完整任务闭环（异常感知->规划->执行->验证），返回端到端证据。

    可选参数 tle：若提供（即 get_tle 工具实时拉取的样例 dict，含 line1/line2），
    本轮闭环将基于该 TLE 分析（实现「从数据库取样例→平台分析」的真闭环）；
    不提供则回退内置样例 TLE（保证可复现）。

    scenario 结构（kind 必填，三选一）：
      轨道抬升:         {"kind": "raise", "target_altitude_km": 466.558, "initial_fuel_kg": 50}
      碰撞规避:         {"kind": "collision", "offset_km": 30, "horizon_minutes": 90, "initial_fuel_kg": 50}
      复合异常自主处置:   {"kind": "anomaly_response", "target_altitude_km": 466.558,
                         "desired_miss_km": 50, "initial_fuel_kg": 50,
                         "inject_fault": "thruster_underperformance"}

    - kind: "raise"（轨道抬升） / "collision"（碰撞规避） / "anomaly_response"（复合异常处置）。
    - anomaly_response 会同时处置轨道衰减与碰撞风险，并在推进器效能异常时自主重规划第二次脉冲
      完成自救（故障自愈），返回 decision_trace（决策链路，可直接作演示旁白/证据）。
    - inject_fault（仅演示/测试用，正常任务不传）："thruster_underperformance"（推进器效能异常→恢复）、
      "fuel_exhausted"（燃料耗尽→优雅降级拒止）、"data_unavailable"（数据失败→降级样本）。
    - raise 用 target_altitude_km；collision 用 offset_km / horizon_minutes；
      anomaly_response 用 target_altitude_km / desired_miss_km / horizon_minutes。
    - initial_fuel_kg: 初始燃料 kg（默认 50）；primary_target: 主航天器（默认 "ISS (ZARYA)"）。
    也接受别名（raise_altitude / collision_avoid / anomaly / disposal）或把参数放进 params 子对象。
    返回: scenario / initial_state / maneuver / final_state / verification /（anomaly_response 额外含
    decay_estimate / conjunction_initial / anomalies / decision_trace / overall_passed）。
    """
    if isinstance(tle, dict) and tle.get("line1") and tle.get("line2"):
        scenario = dict(scenario); scenario["tle"] = tle
    return run_scenario(scenario)


@mcp.tool()
def visualize_tool(scenario: dict, tle: dict = None) -> dict:
    """跑通场景并生成轨道/接近几何 SVG 示意图，返回图片路径与关键指标摘要，适合演示展示。

    可选参数 tle：同 run_scenario_tool，提供则基于该实时样例绘制（与闭环分析同源）。

    scenario 同 run_scenario_tool（支持 raise / collision / anomaly_response）。
    生成的 SVG 含两张子图：① 轨道机动（初始/终态轨道 + 点火方向）② 接近几何（主航天器 vs 编目碎片）。
    返回 svg_path（绝对路径，可直接打开/录制）与 summary（关键指标摘要）；会同时返回完整记录 record。
    """
    if isinstance(tle, dict) and tle.get("line1") and tle.get("line2"):
        scenario = dict(scenario); scenario["tle"] = tle
    rec = run_scenario(scenario)
    if "error" in rec:
        return rec
    path = render_orbit_svg(rec)
    rv = rec.get("raise_verification") or rec.get("verification") or {}
    rm = rv.get("metrics", {})
    av = rec.get("avoid_verification") or {}
    am = av.get("metrics", {})
    summary = {
        "kind": scenario.get("kind"),
        "target_altitude_km": rm.get("target_altitude_km"),
        "final_mean_altitude_km": rm.get("final_mean_altitude_km"),
        "altitude_error_km": rm.get("altitude_error_km"),
        "total_dv_kms": rm.get("total_dv_kms"),
        "fuel_remaining_kg": rm.get("fuel_remaining_kg"),
        "avoid_final_min_miss_km": am.get("final_min_miss_km"),
        "overall_passed": rec.get("overall_passed"),
        "recovered_from_fault": rec.get("recovered_from_fault"),
    }
    return {"svg_path": path, "summary": summary, "record": rec}


@mcp.tool()
def analyze_tool(scenario: dict, tle: dict = None) -> dict:
    """理论推导 vs 仿真 逐项核对：用第一性原理公式（活力公式/火箭方程/B-star 衰减）独立
    计算解析基线，与 run_scenario 的仿真输出比对，返回含相对误差的验证证据（可信度背书）。

    scenario 同 run_scenario_tool（支持 raise / collision / anomaly_response）。
    返回示例字段（raise）：
      analytic_single_burn_dv_ms（解析单脉冲 Δv）、sim_dv_ms（仿真 Δv）、
      dv_reproduction_error_pct（相对误差，应≈0）、analytic_fuel_kg / sim_fuel_kg / fuel_reproduction_error_pct、
      hohmann_reference_dv_ms（经典霍曼双脉冲参考 Δv）、sim_final_alt_km / alt_target_error_km / interpretation。
    可选参数 tle：同 run_scenario_tool，提供则基于该实时样例做核对。
    """
    if isinstance(tle, dict) and tle.get("line1") and tle.get("line2"):
        scenario = dict(scenario); scenario["tle"] = tle
    return analyze(scenario)


@mcp.tool()
def simulate_tool(experiment: str = "all", n: int = 300,
                  target_altitude_km: float = 466.558) -> dict:
    """运行仿真实验，量化系统性能与鲁棒性（演示时可现场跑出统计结果与图表）。

    实验项（experiment 可单选或 "all"）：
      raise_mc  —— 蒙特卡洛抬升鲁棒性：导航估计存在高斯误差时，统计抬升后高度误差分布与成功率
      method    —— 机动策略对比：本系统单脉冲改半长轴 vs 经典霍曼双脉冲（Δv/燃料/终态偏心率）
      avoid     —— 规避策略对比：枚举搜索最优脉冲 vs 解析相位近似（达成的最小距离与 Δv）
      fault     —— 故障自愈扫描：推进器效能异常严重程度 vs 二次脉冲恢复后的高度误差与成功率
      reality   —— 真实世界标定：本模型推得的 ISS 轨道参数 vs 公开已知 ISS 量级（sanity check）
      all       —— 依次运行以上全部五项

    参数：n=蒙特卡洛样本数（默认 300，现场演示建议 150~300）；target_altitude_km=抬升目标高度。
    返回：各实验的统计摘要（数值）+ figure_dir（图表所在目录，含 fig_raise_mc.png 等 5 张图）。
    说明：基于内置样例 TLE（ISS）与二体传播、固定历元，结果可复现；详见《仿真实验报告.md》。
    """
    return run_experiment(experiment=experiment, n=n, target_altitude_km=target_altitude_km)


if __name__ == "__main__":
    mcp.run(transport="stdio")
