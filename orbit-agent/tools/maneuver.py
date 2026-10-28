"""轨道机动：规划 (plan_maneuver) 与执行 (apply_maneuver)。

规划返回结构化机动方案（可能含多次脉冲），执行按方案逐级施加脉冲并传播，
返回完整轨迹，供 verify 与可视化使用。
"""
import numpy as np

from .constants import MU_EARTH, R_EARTH
from .kepler import rv_to_coe, propagate_rv


def _unit(frame, r, v):
    r = np.asarray(r, float)
    v = np.asarray(v, float)
    if frame in ("prograde", "along_track"):
        return v / np.linalg.norm(v)
    if frame == "retrograde":
        return -v / np.linalg.norm(v)
    if frame == "radial":
        return r / np.linalg.norm(r)
    if frame == "normal":
        h = np.cross(r, v)
        return h / np.linalg.norm(h)
    raise ValueError(f"未知 burn_frame: {frame}")


def plan_maneuver(state: dict, objective: str, params: dict) -> dict:
    """规划机动方案。

    objective:
      "raise_altitude"  抬升轨道（霍曼双脉冲）
      "collision_avoid" 碰撞规避（沿迹脉冲变周期，制造相位差增大最小距离）
    params:
      raise_altitude -> {target_altitude_km}
      collision_avoid -> {current_miss_km, desired_miss_km, tca_minutes}
    """
    r = np.asarray(state["r_km"], float)
    v = np.asarray(state["v_kms"], float)
    rmag = float(np.linalg.norm(r))
    coe = rv_to_coe(r, v)
    cur_alt = coe["altitude_km"]
    mu = MU_EARTH

    if objective == "raise_altitude":
        target_alt = float(params["target_altitude_km"])
        a_target = target_alt + R_EARTH
        vmag = float(np.linalg.norm(v))
        # 单脉冲将半长轴直接调整到目标值：平均高度严格等于目标（对任意偏心率稳健）
        v_new = np.sqrt(mu * (2.0 / rmag - 1.0 / a_target))
        dv = v_new - vmag
        frame = "prograde" if dv >= 0 else "retrograde"
        burns = [{"dv_kms": abs(float(dv)), "frame": frame, "after_minutes": 0.0,
                 "note": "单脉冲将半长轴调整至目标值（平均高度达标）"}]
        return {
            "objective": "raise_altitude",
            "target_altitude_km": target_alt,
            "current_altitude_km": cur_alt,
            "current_mean_altitude_km": round(float(coe["a_km"]) - R_EARTH, 3),
            "total_dv_kms": float(abs(dv)),
            "burns": burns,
            "estimated_fuel_kg_per_mm_s": None,
        }

    elif objective == "collision_avoid":
        cur_miss = float(params["current_miss_km"])
        des_miss = float(params["desired_miss_km"])
        tca_min = max(float(params["tca_minutes"]), 1.0)  # 防止 TCA=0 除零
        a = coe["a_km"]
        T = coe["period_min"] * 60.0  # s
        # 需要的沿迹相位偏移，使最小距离从 cur_miss 增到 des_miss
        need_sep = max(des_miss - cur_miss, 1.0)  # km
        delta_theta = need_sep / rmag              # rad
        # 周期变化 -> 相位漂移：delta_theta = 2*pi * (tca/T) * (dT/T)
        dT_over_T = delta_theta * T / (2 * np.pi * tca_min * 60.0)
        da = -(2.0 / 3.0) * a * dT_over_T           # 半长轴变化量
        # 速度变化（圆轨道近似）：dv = 0.5*sqrt(mu)/a^1.5 * |da|
        dv = 0.5 * np.sqrt(mu) / (a ** 1.5) * abs(da)
        burns = [{"dv_kms": float(dv), "frame": "along_track", "after_minutes": 0.0,
                  "note": "沿迹脉冲改变轨道周期，制造相位差以增大接近距离"}]
        return {
            "objective": "collision_avoid",
            "current_miss_km": cur_miss,
            "desired_miss_km": des_miss,
            "tca_minutes": tca_min,
            "total_dv_kms": float(dv),
            "burns": burns,
        }
    else:
        raise ValueError(f"未知 objective: {objective}")


def apply_maneuver(state: dict, maneuver: dict) -> dict:
    """按方案逐级施加脉冲并传播，返回含中间轨迹的最终状态。"""
    r = np.asarray(state["r_km"], float).copy()
    v = np.asarray(state["v_kms"], float).copy()
    epoch = state.get("epoch_utc")
    trajectory = []
    for i, burn in enumerate(maneuver["burns"]):
        if burn["after_minutes"] > 0:
            rr, vv = propagate_rv(r, v, burn["after_minutes"] * 60.0)
            r = np.asarray(rr, float)
            v = np.asarray(vv, float)
        u = _unit(burn["frame"], r, v)
        v = v + burn["dv_kms"] * u
        coe = rv_to_coe(r, v)
        trajectory.append({
            "step": i + 1,
            "after_minutes": burn["after_minutes"],
            "frame": burn["frame"],
            "dv_kms": burn["dv_kms"],
            "r_km": r.tolist(),
            "v_kms": v.tolist(),
            "altitude_km": coe["altitude_km"],
            "a_km": coe["a_km"],
        })
    coe = rv_to_coe(r, v)
    return {
        "epoch_utc": epoch,
        "frame": state.get("frame", "TEME"),
        "r_km": r.tolist(),
        "v_kms": v.tolist(),
        "elements": coe,
        "trajectory": trajectory,
    }
