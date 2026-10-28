"""闭环验证：对比机动前后状态与任务目标，输出量化指标与判定。

所有阈值集中在此，便于按评审标准调整。
"""
import numpy as np

from .constants import MU_EARTH, R_EARTH, DEFAULT_ISP, DEFAULT_WET_MASS, G0
from .kepler import rv_to_coe


# 验证阈值（评审可量化依据）
THRESH = {
    "raise_altitude": {"altitude_error_km": 5.0},
    "collision_avoid": {"min_miss_km": 10.0},
}


def _fuel_consumed(total_dv_kms, wet_mass_kg, isp):
    """火箭方程：给定总脉冲，估算燃料消耗 (kg)。"""
    m = wet_mass_kg
    used = 0.0
    # 逐段指数衰减（此处 dv 已汇总，用总 dv 近似）
    dv_ms = total_dv_kms * 1000.0
    m_final = m / np.exp(dv_ms / (isp * G0 * 1000.0))
    used = m - m_final
    return used


def verify(initial_state, final_state, objective, params,
           dv_events=None, initial_fuel_kg=50.0, wet_mass_kg=DEFAULT_WET_MASS,
           isp=DEFAULT_ISP) -> dict:
    """执行验证，返回指标 + 判定 + 证据表。"""
    if dv_events is None:
        dv_events = []
    r_f = np.asarray(final_state["r_km"], float)
    v_f = np.asarray(final_state["v_kms"], float)
    coe_f = rv_to_coe(r_f, v_f)
    mean_alt = coe_f["a_km"] - R_EARTH

    total_dv = sum(dv_events) if dv_events else 0.0
    fuel_used = _fuel_consumed(total_dv, wet_mass_kg, isp)
    fuel_remaining = max(initial_fuel_kg - fuel_used, 0.0)

    metrics = {
        "final_mean_altitude_km": round(mean_alt, 3),
        "final_perigee_alt_km": round(coe_f["a_km"] * (1 - coe_f["e"]) - R_EARTH, 3),
        "final_apogee_alt_km": round(coe_f["a_km"] * (1 + coe_f["e"]) - R_EARTH, 3),
        "total_dv_kms": round(total_dv, 6),
        "total_dv_ms": round(total_dv * 1000.0, 3),
        "fuel_used_kg": round(fuel_used, 3),
        "fuel_remaining_kg": round(fuel_remaining, 3),
        "initial_fuel_kg": initial_fuel_kg,
        "final_eccentricity": round(coe_f["e"], 5),
        "final_inclination_deg": round(coe_f["i_deg"], 3),
    }

    thresholds_used = dict(THRESH.get(objective, {}))

    if objective == "raise_altitude":
        target = float(params["target_altitude_km"])
        err = abs(mean_alt - target)
        metrics["target_altitude_km"] = target
        metrics["altitude_error_km"] = round(err, 3)
        passed = err <= THRESH["raise_altitude"]["altitude_error_km"]
        verdict = ("PASS" if passed else "FAIL") + "："
        verdict += f"目标 {target} km，实际平均高度 {mean_alt:.2f} km，误差 {err:.2f} km"
    elif objective == "collision_avoid":
        final_miss = float(params.get("final_miss_km", 0.0))
        # 验收阈值优先取任务要求（required_min_miss_km，通常来自场景的 desired_miss_km），
        # 未提供时回退到系统安全下限 THRESH。避免"任务要 50 km、验收却按 10 km 判 PASS"的口径错位。
        req = float(params.get("required_min_miss_km",
                               THRESH["collision_avoid"]["min_miss_km"]))
        metrics["final_min_miss_km"] = round(final_miss, 3)
        metrics["required_min_miss_km"] = req
        passed = final_miss >= req
        verdict = ("PASS" if passed else "FAIL") + "："
        verdict += f"规避后最小距离 {final_miss:.2f} km（要求 ≥ {req} km）"
    else:
        passed = False
        verdict = "FAIL：未知 objective"

    return {
        "objective": objective,
        "passed": bool(passed),
        "verdict": verdict,
        "metrics": metrics,
        "thresholds": THRESH.get(objective, {}),
    }
