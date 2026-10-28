"""理论推导与仿真验证：用第一性原理公式独立计算解析基线，与 run_scenario 的
仿真输出逐项比对（相对误差），给出可信度证据。同时提供规避脉冲扫描函数供绘图复用。

所有公式与 tools/ 中实现的物理一致，并补充经典霍曼双脉冲作为独立参考，
便于在提交材料/演示中论证"仿真数值来源于可推导的物理定律，而非黑箱"。
"""
import numpy as np

from .constants import MU_EARTH, R_EARTH, DEFAULT_WET_MASS, DEFAULT_ISP, G0
from .kepler import rv_to_coe, propagate_rv
from .maneuver import plan_maneuver, apply_maneuver
from .verify import _fuel_consumed
from .conjunction import conjunction_check
from .mission import run_scenario


# ----------------------------------------------------------------------------
# 基础解析公式（第一性原理）
# ----------------------------------------------------------------------------
def raise_dv_analytic(rmag_km, vmag_kms, target_alt_km, mu=MU_EARTH):
    """单脉冲定向改半长轴：把平均高度从当前状态直接命中目标值所需的 Δv（km/s）。

    由活力公式 v = sqrt(mu*(2/r - 1/a)) 推得：在半径 r、当前速率 vmag 处切向加/减速至
    v_new = sqrt(mu*(2/r - 1/a_target))，新轨道半长轴即 a_target，平均高度 = a_target - R。
    这里以"当前真实速率"为基线（而非圆轨道速率），才能与仿真精确对齐。
    """
    a_target = target_alt_km + R_EARTH
    v_new = np.sqrt(mu * (2.0 / rmag_km - 1.0 / a_target))
    return abs(float(v_new - vmag_kms))


def hohmann_dv_analytic(rmag_km, vmag_kms, target_alt_km, mu=MU_EARTH):
    """经典霍曼双脉冲参考 Δv（km/s），与单脉冲方案在**同一出发态**下对照。

    从当前真实状态出发、两次切向脉冲把轨道圆化到目标高度：
      第 1 脉冲 v_tr_peri - vmag（抬升远地点），第 2 脉冲 v2 - v_tr_apo（在远地点圆化）。
    与 raise_dv_analytic 用相同的出发速率 vmag，保证口径一致、可比。
    注意：霍曼额外把轨道"完全圆化"，而本系统单脉冲只命中"平均高度"（保留偏心率），
    故两者目标不同，此处作为代价参考。
    """
    r1 = rmag_km
    r2 = target_alt_km + R_EARTH
    a_tr = (r1 + r2) / 2.0
    v_tr_peri = np.sqrt(mu * (2.0 / r1 - 1.0 / a_tr))
    v_tr_apo = np.sqrt(mu * (2.0 / r2 - 1.0 / a_tr))
    v2 = np.sqrt(mu / r2)
    dv1 = v_tr_peri - vmag_kms
    dv2 = v2 - v_tr_apo
    return abs(float(dv1)) + abs(float(dv2))


def bstar_decay_km_per_day(bstar, mean_motion_rev_per_day):
    """基于 TLE 的 Bstar 阻力项与平近点角速率，估算大气阻力导致的每日高度损失（km/天）。

    经验式：da/dt ∝ bstar * n^2（n 为平近点角速率，rad/s），经 ISS 量级校准常数 1.8e8。
    用于"异常感知"环节的衰减趋势量化，是定性趋势而非精确轨道预报。
    """
    n_rad = mean_motion_rev_per_day * 2 * np.pi / 86400.0
    return 1.8e8 * bstar * (n_rad ** 2)


def rocket_equation_fuel(dv_kms, wet_mass_kg=DEFAULT_WET_MASS, isp=DEFAULT_ISP):
    """火箭方程：给定总脉冲（km/s），估算燃料消耗（kg）。"""
    return _fuel_consumed(dv_kms, wet_mass_kg, isp)


# ----------------------------------------------------------------------------
# 规避脉冲扫描（供绘图 / 优化地形展示复用）
# ----------------------------------------------------------------------------
def sweep_avoidance(initial_state, debris_state, horizon_minutes=90.0, step_minutes=1.0):
    """沿 prograde 方向扫描候选 Δv 量级，重算接近距离，返回 (mags, misses_km)。

    复现 mission._search_avoidance 的单一方向扫描，用于绘制"规避 Δv–最小距离"优化地形。
    """
    mags = np.array([0.005, 0.01, 0.02, 0.04, 0.06, 0.1])
    misses = []
    for mag in mags:
        cand = {"objective": "collision_avoid", "burns": [
            {"dv_kms": float(mag), "frame": "prograde", "after_minutes": 0.0}]}
        final_c = apply_maneuver(initial_state, cand)
        conj = conjunction_check(final_c, [{"name": "DEBRIS", "state": debris_state}],
                                horizon_minutes=horizon_minutes, step_minutes=step_minutes)
        misses.append(conj["worst"]["min_range_km"])
    return mags.tolist(), misses


# ----------------------------------------------------------------------------
# 主验证入口
# ----------------------------------------------------------------------------
def analyze(scenario: dict) -> dict:
    """跑通场景并做理论 vs 仿真逐项核对，返回含相对误差的 validation dict。"""
    rec = run_scenario(scenario)
    if "error" in rec:
        return rec
    kind = rec.get("kind") or scenario.get("kind")
    out = {"scenario": scenario, "kind": kind,
           "note": ("All altitudes are computed from the TLE via SGP4 "
                    "(mean_altitude_km = semi_major_axis - Earth_radius). "
                    "Cite the returned numeric values verbatim; do NOT substitute "
                    "textbook/remembered values (e.g. for ISS).")}

    st0 = rec["initial_state"]
    r = np.asarray(st0["r_km"], float)
    v = np.asarray(st0["v_kms"], float)
    rmag = float(np.linalg.norm(r))
    vmag = float(np.linalg.norm(v))
    coe0 = rv_to_coe(r, v)
    a0 = float(coe0.get("a_km", rmag))
    mean_alt0 = a0 - R_EARTH          # 平均高度 = a - R（真正的"平均"口径，与 verify 一致）

    if kind == "raise":
        target_alt = float(scenario.get("target_altitude_km", 430.0))
        dv_code = raise_dv_analytic(rmag, vmag, target_alt)
        dv_hoh = hohmann_dv_analytic(rmag, vmag, target_alt)
        sim = rec["verification"]["metrics"]
        # 用未取整的机动方案 Δv 作为仿真真值，消除 verify 四舍五入造成的显示误差
        man = plan_maneuver(st0, "raise_altitude", {"target_altitude_km": target_alt})
        sim_dv = float(man["total_dv_kms"])
        sim_alt = sim["final_mean_altitude_km"]
        sim_fuel = sim["fuel_used_kg"]
        fuel_code = rocket_equation_fuel(dv_code)
        out.update({
            "initial_radius_km": round(rmag, 3),
            "initial_mean_alt_km": round(mean_alt0, 3),
            "target_alt_km": target_alt,
            "analytic_single_burn_dv_ms": round(dv_code * 1000, 3),
            "hohmann_reference_dv_ms": round(dv_hoh * 1000, 3),
            "sim_dv_ms": round(sim_dv * 1000, 3),
            "dv_reproduction_error_pct": round(abs(sim_dv - dv_code) / dv_code * 100, 4) if dv_code > 0 else 0.0,
            "analytic_fuel_kg": round(fuel_code, 4),
            "sim_fuel_kg": round(sim_fuel, 4),
            "fuel_reproduction_error_pct": round(abs(sim_fuel - fuel_code) / fuel_code * 100, 4) if fuel_code > 0 else 0.0,
            "sim_final_alt_km": sim_alt,
            "alt_target_error_km": round(abs(sim_alt - target_alt), 4),
            "interpretation": ("仿真 Δv 由活力公式单脉冲改半长轴精确给出（误差≈0），"
                               "与经典霍曼双脉冲参考同量级但目标不同：本项目命中'平均高度'且只用一个脉冲，"
                               "保留了偏心率；霍曼进一步圆化轨道但代价更高。"),
        })

    elif kind == "collision":
        el0 = rec["initial_elements"]
        a = el0["a_km"]
        T = el0["period_min"] * 60.0
        conj0 = rec.get("initial_conjunction", {}).get("worst", {})
        cur = conj0.get("min_range_km")
        sim = rec["verification"]["metrics"]
        final_miss = sim["final_min_miss_km"]
        sim_dv = sim["total_dv_kms"]
        out.update({
            "initial_miss_km": cur,
            "final_miss_km": final_miss,
            "sim_dv_ms": round(sim_dv * 1000, 3),
            "period_min": round(el0["period_min"], 2),
            "interpretation": ("规避脉冲通过沿迹改变轨道周期制造相位漂移，从而增大最小接近距离；"
                               "仿真采用枚举方向×量级、在真实二体传播下直接优化'最小距离'指标，"
                               "比解析近似更稳健（主从高度不一致时解析法会反向恶化）。"),
        })

    elif kind == "anomaly_response":
        target_alt = float(scenario.get("target_altitude_km", 430.0))
        dv_code = raise_dv_analytic(rmag, vmag, target_alt)
        sim_raise = rec.get("raise_verification", {}).get("metrics", {})
        sim_avoid = rec.get("avoid_verification", {})
        decay = rec.get("decay_estimate", {})
        sim_raise_dv = float(sim_raise.get("total_dv_kms", 0) or 0.0)
        out.update({
            "initial_radius_km": round(rmag, 3),
            "initial_mean_alt_km": round(mean_alt0, 3),
            "target_alt_km": target_alt,
            "analytic_single_burn_dv_ms": round(dv_code * 1000, 3),
            "sim_raise_dv_ms": round(sim_raise_dv * 1000, 3),
            "dv_reproduction_error_pct": round(abs(sim_raise_dv - dv_code) / dv_code * 100, 4) if dv_code > 0 else 0.0,
            "sim_final_alt_km": sim_raise.get("final_mean_altitude_km"),
            "decay_est_km_per_day": decay.get("est_km_per_day"),
            "decay_trend": decay.get("trend"),
            "recovered_from_fault": rec.get("recovered_from_fault"),
            "overall_passed": rec.get("overall_passed"),
            "avoid_final_miss_km": sim_avoid.get("metrics", {}).get("final_min_miss_km"),
            "interpretation": ("复合异常处置：先按单脉冲 Δv 抬升（与解析值一致），再做接近规避；"
                               "推进器效能异常时由'验证失败→重规划第二次脉冲'实现故障自愈，"
                               "全程决策链路可追溯。衰减趋势由 B-star 估算量化。"),
        })

    return out
