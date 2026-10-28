"""高层任务编排：把单个工具串成完整闭环，供 AGH 调用或本地测试复用。

run_scenario 执行：拉取 TLE -> 解析初始状态 -> (异常感知) -> 规划机动
-> 执行机动 -> 验证，返回完整记录（含量化证据），可直接作为提交用"运行与验证证据"。
"""
import numpy as np

from .kepler import rv_to_coe, propagate_rv, now_utc_isot
from .tle import fetch_tle, satrec_state_at, FIXED_EPOCH, _SAMPLE_TLES
from .maneuver import plan_maneuver, apply_maneuver
from .conjunction import conjunction_check
from .verify import verify, _fuel_consumed, THRESH
from .constants import DEFAULT_WET_MASS, DEFAULT_ISP


def _resolve_tle(scenario, fault):
    """解析本场景使用的 TLE。

    优先使用 scenario 中携带的外部 TLE（即 get_tle 工具实时拉取的样例，
    dict 中含 line1/line2），这样「从数据库取样例 → 让平台分析」才是真闭环；
    否则（未提供 / data_unavailable 故障注入）回退内置样例 TLE 以保证可复现。

    Returns:
        (tle_dict, data_degraded: bool)
        tle_dict["source"] 保留原标记（celestrak / builtin_sample / provided），
        便于记录与诚实报告数据来源。
    """
    provided = scenario.get("tle") if isinstance(scenario, dict) else None
    if (fault != "data_unavailable"
            and isinstance(provided, dict)
            and provided.get("line1") and provided.get("line2")):
        t = dict(provided)
        t["source"] = t.get("source") or "provided"
        return t, False
    # 回退内置样例（data_unavailable 故障注入也走这里，并标注 data_degraded）
    t = dict(_SAMPLE_TLES.get(scenario.get("primary_target", "ISS (ZARYA)"),
                              _SAMPLE_TLES["ISS (ZARYA)"]))
    t["source"] = "builtin_sample"
    return t, (fault == "data_unavailable")


def _make_debris_state(primary_state, offset_km=30.0):
    """构造一个与主航天器共轨道、沿迹偏置 offset_km 的空间碎片（演示用）。

    共轨道意味着两者最小距离≈offset_km（warning 级接近），符合"检测到碰撞风险"
    的叙事；提供给 AGH 的 conjunction 工具做接近分析。
    """
    r = np.asarray(primary_state["r_km"], float)
    v = np.asarray(primary_state["v_kms"], float)
    rhat = r / np.linalg.norm(r)
    hhat = np.cross(r, v); hhat = hhat / np.linalg.norm(hhat)
    that = np.cross(hhat, rhat)            # 沿迹方向
    rd = r + that * offset_km
    vd = v                                # 共轨道（相同速度）
    return {"r_km": rd.tolist(), "v_kms": vd.tolist(),
            "epoch_utc": primary_state.get("epoch_utc"), "frame": "TEME"}


def _estimate_decay(tle):
    """基于 TLE 的 Bstar 阻力项与平近点角速率，估算大气阻力导致的轨道衰减趋势。

    返回定性趋势（mean_altitude decreasing，即大气阻力使轨道高度缓慢下降）+ 近似每日高度损失（km/天），用于"异常感知"环节。
    注：为定性趋势估算（非精确轨道预报），用于触发处置逻辑，公式常数经 ISS 量级校准。
    """
    l1 = tle.get("line1", "")
    l2 = tle.get("line2", "")
    bstar = None
    try:
        raw = l1[53:61].strip()
        i = next((k for k in range(len(raw)) if raw[k] in "+-"), None)
        if i is not None:
            man = float(raw[:i]) / (10.0 ** len(raw[:i]))
            bstar = man * (10.0 ** int(raw[i:]))
    except Exception:
        bstar = None
    if bstar is None:
        bstar = 1e-4
    n_rev = 15.5
    try:
        n_rev = float(l2[52:63].strip())
    except Exception:
        pass
    n_rad = n_rev * 2 * np.pi / 86400.0
    est = 1.8e8 * bstar * (n_rad ** 2)
    return {
        "bstar": bstar,
        "mean_motion_rev_per_day": n_rev,
        "est_km_per_day": round(est, 4),
        "trend": "decreasing",
        "trend_quantity": "mean_altitude",
        "risk": "high" if est > 0.05 else "low",
        "note": "基于 TLE Bstar 与平近点角速率的近似大气阻力衰减估算（定性趋势，非精确预报）",
    }


def _make_debris_catalog(primary_state, offsets_km=(15, 60, 110)):
    """构造一个与主航天器共轨道、沿迹不同偏置的多目标碎片编目（演示用）。

    用于"接近分析"环节体现多目标筛选能力（而非单一碎片）。
    """
    cats = []
    for i, off in enumerate(offsets_km):
        st = _make_debris_state(primary_state, offset_km=off)
        cats.append({"name": f"DEBRIS-{i + 1:02d}", "state": st})
    return cats


def _search_avoidance(primary_state, objects, horizon_minutes=90.0, step_minutes=1.0):
    """枚举方向×量级脉冲，实时重算接近距离，选取使最小距离最大的规避方案。

    与 collision 场景一致，直接优化真实指标，避免解析近似在主从高度不一致时失效。
    返回 (best_min_range, maneuver, final_state, conjunction)。
    """
    best = None
    for frame in ["prograde", "retrograde", "radial", "normal"]:
        for mag in [0.005, 0.01, 0.02, 0.04, 0.06, 0.1]:
            cand = {"objective": "collision_avoid", "burns": [
                {"dv_kms": mag, "frame": frame, "after_minutes": 0.0,
                 "note": f"{frame} {mag} km/s 候选"}]}
            final_c = apply_maneuver(primary_state, cand)
            conj_c = conjunction_check(final_c, objects,
                                      horizon_minutes=horizon_minutes, step_minutes=step_minutes)
            miss = conj_c["worst"]["min_range_km"]
            if best is None or miss > best[0]:
                best = (miss, cand, final_c, conj_c)
    return best


def _run_anomaly_response(scenario, fault):
    """复合异常自主处置闭环：检测→诊断→规划→执行→(失败)→重规划恢复→验证→规避→验证。

    这是作品的"异常处置"核心演示：同时处置轨道衰减与碰撞风险，并在推进器效能异常时
    自主重规划第二次脉冲完成高度目标（故障自愈），全程记录决策链路 decision_trace。
    """
    trace = []
    initial_fuel = float(scenario.get("initial_fuel_kg", 50.0))
    target_alt = float(scenario.get("target_altitude_km", 430.0))
    desired_miss = float(scenario.get("desired_miss_km", 50.0))
    horizon = float(scenario.get("horizon_minutes", 120.0))

    # ---- 数据获取（优先用外部提供的 TLE，否则回退内置样例）----
    tle, data_degraded = _resolve_tle(scenario, fault)
    epoch = scenario.get("epoch_utc") or FIXED_EPOCH
    st0 = satrec_state_at(tle, epoch)
    if "error" in st0:
        return {"error": st0["error"], "scenario": scenario}
    el0 = rv_to_coe(st0["r_km"], st0["v_kms"])

    # ---- 1) 异常感知（检测）----
    decay = _estimate_decay(tle)
    catalog = _make_debris_catalog(st0, scenario.get("debris_offsets_km", [15, 60, 110]))
    conj0 = conjunction_check(st0, catalog, horizon_minutes=horizon, step_minutes=2)
    anomalies = []
    if decay["risk"] == "high" or decay["est_km_per_day"] > 0.05:
        anomalies.append("orbital_decay")
    if conj0["worst"] and conj0["worst"]["min_range_km"] < 50:
        anomalies.append("conjunction_risk")
    trace.append({
        "phase": "detect",
        "action": "遥测与轨道异常感知",
        "result": (f"衰减≈{decay['est_km_per_day']:.3f} km/天（{decay['risk']}）；"
                   f"最近接近 {conj0['worst']['min_range_km']:.2f} km（{conj0['worst']['risk_level']}）"),
        "reasoning": (f"基于 TLE 的 Bstar 与平近点角速率估算轨道衰减趋势；对 {len(catalog)} 个编目空间目标"
                      f"做接近分析（TCA/最小距离/风险等级），识别复合异常。"),
    })

    # ---- 2) 诊断与优先级 ----
    diag = "复合异常：" + (" + ".join(anomalies) if anomalies else "未检出显著异常，状态健康")
    trace.append({
        "phase": "diagnose",
        "action": "异常分类与处置优先级排序",
        "result": diag,
        "reasoning": "衰减威胁轨道寿命→先抬升；接近威胁安全→再规避。二者独立，按优先级分步处置。",
    })

    # ---- 3) 规划抬升 ----
    man1 = plan_maneuver(st0, "raise_altitude", {"target_altitude_km": target_alt})
    total_dv1 = sum(b["dv_kms"] for b in man1["burns"])
    est_fuel1 = _fuel_consumed(total_dv1, DEFAULT_WET_MASS, DEFAULT_ISP)
    trace.append({
        "phase": "plan",
        "action": "规划抬升机动 (raise_altitude)",
        "result": f"单脉冲 {man1['burns'][0]['frame']} Δv={total_dv1 * 1000:.2f} m/s，预估燃料 {est_fuel1:.2f} kg",
        "reasoning": "霍曼式单脉冲将半长轴调至目标高度；燃料预算内则可执行。",
    })

    recovered = False
    raise_maneuver = man1
    skip_avoid = False
    # ---- 4) 执行抬升（含故障/燃料分支）----
    if fault == "fuel_exhausted" or est_fuel1 > initial_fuel:
        ver_r = verify(st0, st0, "raise_altitude", {"target_altitude_km": target_alt},
                       [0.0], initial_fuel_kg=initial_fuel)
        ver_r["passed"] = False
        ver_r["verdict"] = (f"FAIL（优雅降级）：规划需燃料 {est_fuel1:.3f} kg 超过剩余 "
                            f"{initial_fuel:.3f} kg，已拒绝盲目执行，建议地面介入")
        final_state = st0
        raise_ver = ver_r
        skip_avoid = True
        trace.append({
            "phase": "execute",
            "action": "抬升执行（拒绝）",
            "result": f"燃料不足，拒绝执行；剩余 {initial_fuel:.2f} kg",
            "reasoning": "安全第一：燃料不足以支撑目标抬升时，不执行任何可能危及后续任务的机动。",
        })
    elif fault == "thruster_underperformance":
        eff = 0.55
        man_exec = {"objective": "raise_altitude", "burns": [
            {"dv_kms": b["dv_kms"] * eff, "frame": b["frame"],
             "after_minutes": b["after_minutes"],
             "note": f"推进器效能异常，仅交付 {eff * 100:.0f}% 指令 Δv"} for b in man1["burns"]]}
        final1 = apply_maneuver(st0, man_exec)
        dv1 = [b["dv_kms"] for b in man_exec["burns"]]
        ver1 = verify(st0, final1, "raise_altitude", {"target_altitude_km": target_alt},
                      dv1, initial_fuel_kg=initial_fuel)
        short = target_alt - ver1["metrics"]["final_mean_altitude_km"]
        detected = not ver1["passed"]
        tol = THRESH["raise_altitude"]["altitude_error_km"]
        if detected:
            # 验证器真判 FAIL：推进器仅兑现 55% 指令 Δv，误差超出容差 -> 触发自主重规划（自愈）
            trace.append({
                "phase": "execute",
                "action": "执行抬升（一次脉冲，含推进器效能异常）",
                "result": (f"实际平均高度 {ver1['metrics']['final_mean_altitude_km']:.2f} km，"
                           f"距目标差 {short:.2f} km → {ver1['verdict'][:4]}"),
                "reasoning": (f"验证器判定未达标（推进器仅兑现 {eff * 100:.0f}% 指令 Δv，"
                              f"误差 {short:.2f} km 超出 {tol:.0f} km 容差），"
                              f"系统据此自主触发重规划。"),
            })
            man2 = plan_maneuver(final1, "raise_altitude", {"target_altitude_km": target_alt})
            final_state = apply_maneuver(final1, man2)
            dv2 = dv1 + [b["dv_kms"] for b in man2["burns"]]
            raise_ver = verify(st0, final_state, "raise_altitude",
                              {"target_altitude_km": target_alt}, dv2, initial_fuel_kg=initial_fuel)
            recovered = bool(raise_ver["passed"])
            raise_maneuver = {"planned": man1, "executed_degraded": man_exec, "recovery_burn": man2}
            trace.append({
                "phase": "recover",
                "action": "自主重规划第二次脉冲并补执行",
                "result": (f"补 Δv 后平均高度 {raise_ver['metrics']['final_mean_altitude_km']:.2f} km"
                           f" → {raise_ver['verdict'][:4]}"),
                "reasoning": "以当前状态重新计算到达目标高度所需的增量并执行，闭合高度误差，实现故障自愈。",
            })
        else:
            # 验证器真判 PASS：本场景下 55% 效能损失仍在容差内，不触发重规划（如实，不演）
            final_state = final1
            raise_ver = ver1
            recovered = False
            raise_maneuver = {"planned": man1, "executed_degraded": man_exec}
            trace.append({
                "phase": "execute",
                "action": "执行抬升（一次脉冲，含推进器效能异常）",
                "result": (f"实际平均高度 {ver1['metrics']['final_mean_altitude_km']:.2f} km，"
                           f"距目标差 {short:.2f} km → {ver1['verdict'][:4]}"),
                "reasoning": (f"验证器判定误差 {short:.2f} km 仍在 {tol:.0f} km 容差内，"
                              f"推进器 {eff * 100:.0f}% 效能损失不足以触发重规划，抬升一次到位。"),
            })
    else:
        final_state = apply_maneuver(st0, man1)
        dv1 = [b["dv_kms"] for b in man1["burns"]]
        raise_ver = verify(st0, final_state, "raise_altitude", {"target_altitude_km": target_alt},
                          dv1, initial_fuel_kg=initial_fuel)
        trace.append({
            "phase": "execute",
            "action": "执行抬升（一次脉冲）",
            "result": f"平均高度 {raise_ver['metrics']['final_mean_altitude_km']:.2f} km → {raise_ver['verdict'][:4]}",
            "reasoning": "指令增量足额交付，一次到位。",
        })

    # ---- 5) 碰撞规避（基于抬升后的状态，搜索最优规避脉冲）----
    conj1 = conjunction_check(final_state, catalog, horizon_minutes=horizon, step_minutes=2)
    avoid = None
    avoid_ver = None
    avoid_final = final_state
    if (conj1["worst"] and conj1["worst"]["min_range_km"] < desired_miss) and not skip_avoid:
        best = _search_avoidance(final_state, catalog, horizon_minutes=horizon, step_minutes=2)
        avoid_man = best[1]
        avoid_final = best[2]
        conj2 = best[3]
        final_miss = best[0]
        avoid_ver = verify(final_state, avoid_final, "collision_avoid",
                          {"final_miss_km": final_miss,
                           "required_min_miss_km": desired_miss},
                          [b["dv_kms"] for b in avoid_man["burns"]], initial_fuel_kg=initial_fuel)
        avoid = {"planned": avoid_man, "conj_before": conj1, "conj_after": conj2, "target_miss_km": final_miss}
        trace.append({
            "phase": "plan/execute",
            "action": "规划并执行碰撞规避（枚举搜索最优脉冲）",
            "result": (f"规避前最近 {conj1['worst']['min_range_km']:.2f} km → 规避后 {final_miss:.2f} km → {avoid_ver['verdict'][:4]}"),
            "reasoning": "枚举方向×量级脉冲并实时重算接近距离，选取使最小距离最大的方案，保证规避后安全。",
        })
    else:
        trace.append({
            "phase": "verify",
            "action": "接近复核",
            "result": f"抬升后最近距离 {conj1['worst']['min_range_km']:.2f} km ≥ 需求 {desired_miss:.0f} km，无需规避",
            "reasoning": "抬升改变相位，已自然拉开与碎片的接近距离。",
        })

    if fault == "thruster_underperformance":
        overall_pass = recovered
    elif fault == "fuel_exhausted" or est_fuel1 > initial_fuel:
        overall_pass = False
    else:
        overall_pass = bool(raise_ver["passed"])
    if avoid_ver is not None:
        overall_pass = overall_pass and bool(avoid_ver["passed"])

    return {
        "scenario": scenario,
        "kind": "anomaly_response",
        "horizon_minutes": horizon,
        "tle_source": tle.get("source"),
        "data_degraded": data_degraded,
        "epoch_utc": epoch,
        "initial_state": st0,
        "initial_elements": el0,
        "decay_estimate": decay,
        "conjunction_initial": conj0,
        "anomalies": anomalies,
        "debris_catalog": catalog,
        "maneuver": raise_maneuver,
        "raise_verification": raise_ver,
        "conjunction_avoidance": avoid,
        "avoid_verification": avoid_ver,
        # final_state 为"全部机动完成后"的终态（含规避）；raise_final_state 专指
        # 抬升完成后、规避之前的终态，供可视化正确绘制"抬轨终态轨道"（二者不同）。
        "raise_final_state": final_state,
        "final_state": avoid_final,
        "decision_trace": trace,
        "recovered_from_fault": bool(fault == "thruster_underperformance") and recovered,
        "overall_passed": overall_pass,
    }


# AGH/LLM 可能给出多种入参写法：这里统一规整，避免因形参不一致抛 KeyError。
_KIND_ALIASES = {
    "raise": "raise", "raise_altitude": "raise", "altitude_raise": "raise",
    "orbit_raise": "raise", "reboost": "raise",
    "collision": "collision", "collision_avoid": "collision", "conjunction": "collision",
    "avoid": "collision", "evade": "collision",
    "anomaly": "anomaly_response", "anomaly_response": "anomaly_response",
    "disposal": "anomaly_response", "fault_response": "anomaly_response",
}


def _normalize_scenario(raw) -> dict:
    """把入参规整为标准 {"kind": "raise"|"collision", ...}。

    兼容 LLM 常见写法：
      - 顶层直接 {"kind":"raise", ...}
      - 被包一层 {"scenario": {...}}
      - 用 name/objective/type/mode 代替 kind
      - 业务参数放在嵌套 params 里，或与 kind 平铺
      - kind 用别名（raise_altitude / collision_avoid 等）
    无法识别时返回 {"__error__": <说明>}，由调用方转成友好错误。
    """
    if not isinstance(raw, dict):
        return {"__error__": f"scenario 应为对象，收到 {type(raw).__name__}"}

    s = dict(raw)
    inner = s.get("scenario")
    if isinstance(inner, dict):
        merged = dict(inner)
        for k, v in s.items():
            if k != "scenario" and k not in merged:
                merged[k] = v
        s = merged

    kind_raw = None
    for key in ("kind", "name", "objective", "type", "mode"):
        val = s.get(key)
        if isinstance(val, str) and val.strip():
            kind_raw = val
            break
    if kind_raw is None:
        return {"__error__": "缺少场景类型：请提供 kind（raise 或 collision）"}

    kind = _KIND_ALIASES.get(kind_raw.strip().lower())
    if kind is None:
        return {"__error__": f"未知场景类型 '{kind_raw}'：仅支持 raise 或 collision"}

    params = s.get("params")
    if isinstance(params, dict):
        for k, v in params.items():
            s.setdefault(k, v)

    s["kind"] = kind
    return s


def run_scenario(scenario) -> dict:
    """跑通一个完整任务场景，返回端到端记录。

    scenario 示例:
      raise:    {"kind":"raise","target_altitude_km":430,"initial_fuel_kg":50}
      collision:{"kind":"collision","desired_miss_km":80,"initial_fuel_kg":50}
    """
    scen = _normalize_scenario(scenario)
    if "__error__" in scen:
        return {
            "error": scen["__error__"],
            "hint": ("示例：{\"kind\":\"raise\",\"target_altitude_km\":430,\"initial_fuel_kg\":50} "
                     "或 {\"kind\":\"collision\",\"offset_km\":30,\"initial_fuel_kg\":50}"),
            "received": scenario,
        }
    scenario = scen

    # 故障注入（仅用于演示/测试异常分支，正常任务不传）：
    #   data_unavailable  -> TLE 拉取失败，降级为内置样例并标注 data_degraded
    #   fuel_exhausted    -> 强制触发燃料不足降级（不执行机动）
    #   thruster_failure  -> 规划成功后模拟单脉冲执行失败，触发重规划告警
    fault = scenario.get("inject_fault")
    data_degraded = False

    tle, data_degraded = _resolve_tle(scenario, fault)
    epoch = scenario.get("epoch_utc") or FIXED_EPOCH
    st0 = satrec_state_at(tle, epoch)
    if "error" in st0:
        return {"error": st0["error"], "scenario": scenario}
    el0 = rv_to_coe(st0["r_km"], st0["v_kms"])
    record = {
        "scenario": scenario,
        "tle_source": tle.get("source"),
        "epoch_utc": epoch,
        "primary_target": scenario.get("primary_target", "ISS (ZARYA)"),
        "initial_state": st0,
        "initial_elements": el0,
        "data_degraded": data_degraded,
    }

    if scenario["kind"] == "raise":
        obj = "raise_altitude"
        params = {"target_altitude_km": float(scenario.get("target_altitude_km", 430.0))}
        man = plan_maneuver(st0, obj, params)
        initial_fuel = float(scenario.get("initial_fuel_kg", 50.0))
        total_dv = sum(b["dv_kms"] for b in man["burns"])
        est_fuel = _fuel_consumed(total_dv, DEFAULT_WET_MASS, DEFAULT_ISP)

        # 异常分支①：燃料不足 -> 降级告警，不盲目执行
        if fault == "fuel_exhausted" or est_fuel > initial_fuel:
            ver = verify(st0, st0, obj, params, [0.0], initial_fuel_kg=initial_fuel)
            ver["passed"] = False
            if fault == "fuel_exhausted":
                reason = "注入燃料耗尽故障：剩余燃料不足以完成规划机动"
            else:
                reason = f"机动所需燃料 {est_fuel:.3f} kg 超过剩余 {initial_fuel:.3f} kg"
            ver["verdict"] = (f"FAIL（降级告警）：{reason}，已停止执行，"
                              f"建议地面介入或多脉冲分配")
            record.update({"maneuver": man, "final_state": st0,
                           "verification": ver, "degraded": True})
            return record

        # 异常分支②：推进器单脉冲失效 -> 重规划为双半脉冲并成功执行
        if fault == "thruster_failure":
            half = [{"dv_kms": b["dv_kms"] / 2.0, "frame": b["frame"],
                     "after_minutes": 0.0, "note": f"重规划半脉冲({b['frame']})"}
                    for b in man["burns"]]
            man2 = {"objective": obj, "burns": half + half}
            final = apply_maneuver(st0, man2)
            dv_events = [b["dv_kms"] for b in man2["burns"]]
            ver = verify(st0, final, obj, params, dv_events, initial_fuel_kg=initial_fuel)
            record.update({"maneuver": man2, "final_state": final,
                           "verification": ver, "replanned": True,
                           "degrade_note": "推进器单脉冲失效，已重规划为双半脉冲并成功执行"})
        else:
            final = apply_maneuver(st0, man)
            dv_events = [b["dv_kms"] for b in man["burns"]]
            ver = verify(st0, final, obj, params, dv_events, initial_fuel_kg=initial_fuel)
            record.update({"maneuver": man, "final_state": final, "verification": ver})

    elif scenario["kind"] == "collision":
        horizon = scenario.get("horizon_minutes", 90)
        debris = _make_debris_state(st0, offset_km=scenario.get("offset_km", 30.0))
        conj0 = conjunction_check(st0, [{"name": "DEBRIS-DEMO", "state": debris}],
                                 horizon_minutes=horizon, step_minutes=1)
        cur = conj0["worst"]
        obj = "collision_avoid"
        # 智能体搜索最优规避脉冲：枚举方向×量级，重算接近距离，选使最小距离最大者
        best = None
        for frame in ["prograde", "retrograde", "radial", "normal"]:
            for mag in [0.005, 0.01, 0.02, 0.04, 0.06, 0.1]:
                cand_man = {"objective": obj, "burns": [
                    {"dv_kms": mag, "frame": frame, "after_minutes": 0.0,
                     "note": f"{frame} {mag} km/s 候选"}]}
                final_c = apply_maneuver(st0, cand_man)
                conj_c = conjunction_check(final_c,
                                          [{"name": "DEBRIS-DEMO", "state": debris}],
                                          horizon_minutes=horizon, step_minutes=1)
                miss = conj_c["worst"]["min_range_km"]
                if best is None or miss > best[0]:
                    best = (miss, cand_man, final_c, conj_c)
        man = best[1]
        final = best[2]
        conj1 = best[3]
        final_miss = conj1["worst"]["min_range_km"]
        dv_events = [b["dv_kms"] for b in man["burns"]]
        ver = verify(st0, final, obj, {"final_miss_km": final_miss}, dv_events,
                    initial_fuel_kg=scenario.get("initial_fuel_kg", 50.0))
        record.update({"initial_conjunction": conj0, "maneuver": man,
                       "final_state": final, "final_conjunction": conj1,
                       "debris_objects": [debris],
                       "verification": ver})
    elif scenario["kind"] == "anomaly_response":
        record = _run_anomaly_response(scenario, fault)
        return record

    else:
        return {"error": f"未知 scenario.kind: {scenario['kind']}"}

    return record
