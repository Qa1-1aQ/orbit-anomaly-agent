"""空间目标接近分析 (conjunction assessment)。

给定主航天器状态与一个/多个其他目标（TLE 或状态），在给定时间窗内
逐步传播并计算最小接近距离 (miss distance) 与最接近时刻 (TCA)。
"""
import numpy as np

from .kepler import propagate_rv
from .tle import satrec_state_at, FIXED_EPOCH


def _sample_states(obj, epoch_utc, horizon_min, step_min):
    """返回 (name, list_of_r_vectors)。

    注意：只有 "tle" 分支需要用 epoch_utc 生成采样时刻；"state" 分支用二体传播，
    与 epoch 无关。这样即便调用方未提供 epoch_utc，也不会因 astropy 解析 None
    而整条 conjunction 调用崩溃（state 分支完全不碰 astropy）。
    """
    step_min = float(step_min) or 1.0
    n = int(float(horizon_min) / step_min)

    if "tle" in obj:
        from astropy.time import Time
        import datetime as dt
        t0 = Time(epoch_utc, format="isot", scale="utc")
        times = [epoch_utc]
        cur = t0.datetime
        for k in range(1, n + 1):
            times.append(Time(cur + dt.timedelta(minutes=step_min * k)).isot)
        rs = []
        for t in times:
            st = satrec_state_at(obj["tle"], t)
            if "error" in st:
                return obj.get("name", "unknown"), None
            rs.append(np.asarray(st["r_km"], float))
        return obj.get("name", "object"), rs
    elif "state" in obj:
        r0 = np.asarray(obj["state"]["r_km"], float)
        v0 = np.asarray(obj["state"]["v_kms"], float)
        rs = [r0]
        for k in range(1, n + 1):
            rr, _ = propagate_rv(r0, v0, step_min * 60.0 * k)
            rs.append(np.asarray(rr, float))
        return obj.get("name", "object"), rs
    return obj.get("name", "unknown"), None


def conjunction_check(primary_state: dict, objects: list,
                      horizon_minutes: float = 90.0, step_minutes: float = 1.0) -> dict:
    """主航天器状态 vs 多个目标，求最小接近距离。

    objects: [{name, tle?|state?}, ...]
    """
    p_r0 = np.asarray(primary_state["r_km"], float)
    p_v0 = np.asarray(primary_state["v_kms"], float)
    # 缺失/None epoch 时回退固定历元（全系统统一锚点），避免 astropy 解析 None 崩溃。
    epoch = primary_state.get("epoch_utc") or FIXED_EPOCH

    encounters = []
    for obj in objects:
        name, other_rs = _sample_states(obj, epoch, horizon_minutes, step_minutes)
        if other_rs is None:
            encounters.append({"name": name, "error": "目标状态解析失败"})
            continue
        min_d = float("inf")
        tca_min = 0.0
        p_r = p_r0
        best_k = 0
        for k in range(len(other_rs)):
            if k == 0:
                pr = p_r0
            else:
                pr, _ = propagate_rv(p_r0, p_v0, step_minutes * 60.0 * k)
                pr = np.asarray(pr, float)
            d = float(np.linalg.norm(pr - other_rs[k]))
            if d < min_d:
                min_d = d
                tca_min = k * step_minutes
                best_k = k
        if min_d < 5:
            risk = "critical"
        elif min_d < 50:
            risk = "warning"
        else:
            risk = "safe"
        encounters.append({
            "name": name,
            "min_range_km": round(min_d, 3),
            "tca_minutes": round(tca_min, 2),
            "risk_level": risk,
        })
    worst = min((e for e in encounters if "min_range_km" in e),
                key=lambda e: e["min_range_km"], default=None)
    return {"epoch_utc": epoch, "encounters": encounters, "worst": worst}
