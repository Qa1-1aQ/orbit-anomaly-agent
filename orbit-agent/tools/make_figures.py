"""生成技术验证图表（真实 matplotlib 绘制，非示意）：

  1) fig_altitude_timeline.png  —— 抬升前后高度-时间曲线 + 目标线
  2) fig_dv_budget.png          —— Δv 与燃料预算对比（解析 / 仿真 / 霍曼参考）
  3) fig_conjunction.png        —— 碰撞规避前后"接近距离-时间"曲线
  4) fig_decay.png              —— 基于 B-star 的轨道衰减投影
  5) fig_avoidance_search.png   —— 规避脉冲"Δv–最小接近距离"优化地形

同时把理论 vs 仿真核对结果写入 output/validation_table.md。
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from tools.cjk_font import setup_cjk_font

# 跨平台自寻中文字体（WSL/Linux 下也能正常显示中文标题，避免方框）
setup_cjk_font()
plt.rcParams["axes.unicode_minus"] = False

from tools.mission import run_scenario
from tools.analysis import analyze, sweep_avoidance, bstar_decay_km_per_day
from tools.kepler import rv_to_coe, propagate_rv
from tools.constants import R_EARTH
from tools.diagram import render_flow_diagram

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "figures")
os.makedirs(OUT, exist_ok=True)

# 统一配色（与 SVG 风格一致）
C_INIT = "#7f8db0"
C_FIN = "#36d39a"
C_TGT = "#ffcf4d"
C_DEB = "#d64545"
plt.rcParams.update({"font.size": 11, "axes.grid": True, "grid.alpha": 0.3,
                     "figure.dpi": 130, "savefig.bbox": "tight"})


def alt_timeline(st0, stf, target_alt, fname):
    def sample(state, period_min):
        r = np.asarray(state["r_km"], float)
        v = np.asarray(state["v_kms"], float)
        T = period_min * 60.0
        ts = np.linspace(0, T, 220)
        alts = []
        for dt in ts:
            rr, _ = propagate_rv(r, v, dt)
            alts.append(np.linalg.norm(rr) - R_EARTH)
        return ts / 60.0, np.array(alts)
    p0 = rv_to_coe(st0["r_km"], st0["v_kms"])["period_min"]
    pf = rv_to_coe(stf["r_km"], stf["v_kms"])["period_min"]
    t0, a0 = sample(st0, p0)
    t1, a1 = sample(stf, pf)
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.plot(t0, a0, color=C_INIT, lw=1.8, label="初始轨道")
    ax.plot(t1, a1, color=C_FIN, lw=1.8, label="抬升后轨道")
    ax.axhline(target_alt, color=C_TGT, ls="--", lw=1.6, label=f"目标 {target_alt:.1f} km")
    ax.set_xlabel("轨道相位时间 (min)")
    ax.set_ylabel("平均高度 (km)")
    ax.set_title("图1 抬升前后轨道高度剖面（半长轴命中目标）")
    ax.legend(loc="lower right", fontsize=9)
    fig.savefig(os.path.join(OUT, fname))
    plt.close(fig)


def dv_budget(rec, ana, fname):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.2, 3.8))
    labels = ["解析单脉冲", "霍曼参考", "仿真结果"]
    dv = [ana["analytic_single_burn_dv_ms"], ana["hohmann_reference_dv_ms"], ana["sim_dv_ms"]]
    bars = ax1.bar(labels, dv, color=[C_FIN, C_INIT, "#2b6cff"])
    ax1.set_ylabel("Δv (m/s)")
    ax1.set_title("图2a 抬升 Δv 预算")
    for b, val in zip(bars, dv):
        ax1.text(b.get_x() + b.get_width() / 2, val + 0.1, f"{val:.2f}", ha="center", fontsize=9)
    from tools.analysis import rocket_equation_fuel
    fuel = [ana["analytic_fuel_kg"],
            rocket_equation_fuel(ana["hohmann_reference_dv_ms"] / 1000.0),
            ana["sim_fuel_kg"]]
    bars2 = ax2.bar(labels, fuel, color=[C_FIN, C_INIT, "#2b6cff"])
    ax2.set_ylabel("燃料 (kg)")
    ax2.set_title("图2b 燃料消耗（火箭方程）")
    for b, val in zip(bars2, fuel):
        ax2.text(b.get_x() + b.get_width() / 2, val + 0.005, f"{val:.4f}", ha="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, fname))
    plt.close(fig)


def conjunction_timeline(st0, stf, debris_state, horizon_min, fname):
    def miss_series(primary, other_state, horizon, step=1.0):
        r = np.asarray(primary["r_km"], float)
        v = np.asarray(primary["v_kms"], float)
        ro = np.asarray(other_state["r_km"], float)
        vo = np.asarray(other_state["v_kms"], float)
        n = int(horizon / step)
        ts, ms = [], []
        for k in range(n + 1):
            if k == 0:
                pr = r
            else:
                pr, _ = propagate_rv(r, v, step * 60 * k)
                pr = np.asarray(pr, float)
            rr, _ = propagate_rv(ro, vo, step * 60 * k)
            rr = np.asarray(rr, float)
            ts.append(k * step)
            ms.append(np.linalg.norm(pr - rr))
        return np.array(ts), np.array(ms)
    t0, m0 = miss_series(st0, debris_state, horizon_min)
    t1, m1 = miss_series(stf, debris_state, horizon_min)
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.plot(t0, m0, color=C_DEB, lw=1.8, label="规避前")
    ax.plot(t1, m1, color=C_FIN, lw=1.8, label="规避后")
    ax.axhline(10, color=C_TGT, ls="--", lw=1.2, label="安全阈值 10 km")
    ax.set_xlabel("时间 (min)")
    ax.set_ylabel("主航天器–碎片距离 (km)")
    ax.set_title("图3 碰撞规避前后接近距离随时间变化")
    ax.legend(loc="upper right", fontsize=9)
    fig.savefig(os.path.join(OUT, fname))
    plt.close(fig)


def decay_projection(rec, fname):
    decay = rec.get("decay_estimate", {})
    rate = decay.get("est_km_per_day", 0.069)
    alt0 = rec["initial_mean_alt_km"] if "initial_mean_alt_km" in rec else (np.linalg.norm(
        np.asarray(rec["initial_state"]["r_km"], float)) - R_EARTH)
    days = np.linspace(0, 730, 200)
    alt = alt0 - rate * days
    # 安全下界（示意：LEO 运营经验下限）
    safe = 380.0
    cross = (alt0 - safe) / rate if rate > 0 else None
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.plot(days, alt, color="#2b6cff", lw=2.0, label=f"衰减率 ≈ {rate:.3f} km/天")
    ax.axhline(safe, color=C_DEB, ls="--", lw=1.4, label=f"运营安全下界 {safe:.0f} km")
    if cross is not None and cross <= 730:
        ax.axvline(cross, color=C_TGT, ls=":", lw=1.4)
        ax.text(cross + 8, alt0 - 5, f"≈{cross:.0f} 天触线", color=C_TGT, fontsize=9)
    ax.set_xlabel("时间 (天)")
    ax.set_ylabel("平均高度 (km)")
    ax.set_title("图4 基于 TLE-Bstar 的轨道衰减投影（异常感知输入）")
    ax.legend(loc="upper right", fontsize=9)
    fig.savefig(os.path.join(OUT, fname))
    plt.close(fig)


def avoidance_landscape(st0, debris_state, horizon_min, fname):
    mags, misses = sweep_avoidance(st0, debris_state, horizon_minutes=horizon_min, step_minutes=1.0)
    mags = np.array(mags) * 1000.0
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.plot(mags, misses, "o-", color="#2b6cff", lw=1.8)
    best_i = int(np.argmax(misses))
    ax.plot(mags[best_i], misses[best_i], "o", color=C_FIN, ms=10, label=f"最优 Δv≈{mags[best_i]:.0f} m/s")
    ax.set_xlabel("候选脉冲 Δv (m/s)")
    ax.set_ylabel("规避后最小接近距离 (km)")
    ax.set_title("图5 规避脉冲扫描：Δv–最小接近距离优化地形")
    ax.legend(fontsize=9)
    fig.savefig(os.path.join(OUT, fname))
    plt.close(fig)


def main():
    # 为保证证据可复现：固定历元 + 锁定内置样例 TLE（避免在线数据随时间漂移）
    import tools.mission as mission_mod
    import tools.tle as tle_mod
    FIXED_EPOCH = "2026-10-10T12:00:00.000"
    mission_mod.fetch_tle = lambda target, *a, **k: dict(
        tle_mod._SAMPLE_TLES.get(target, tle_mod._SAMPLE_TLES["ISS (ZARYA)"]), source="builtin_sample")
    EPO = {"epoch_utc": FIXED_EPOCH}

    # 1) raise（+40 km：目标 = 固定快照基线 426.558 + 40 = 466.558，与旗舰闭环同幅度）
    rec_r = run_scenario({"kind": "raise", "target_altitude_km": 466.558, "initial_fuel_kg": 50, **EPO})
    ana_r = analyze({"kind": "raise", "target_altitude_km": 466.558, "initial_fuel_kg": 50, **EPO})
    alt_timeline(rec_r["initial_state"], rec_r["final_state"], 466.558, "fig_altitude_timeline.png")
    dv_budget(rec_r, ana_r, "fig_dv_budget.png")

    # 2) collision
    rec_c = run_scenario({"kind": "collision", "desired_miss_km": 80, "initial_fuel_kg": 50, **EPO})
    debris = rec_c["debris_objects"][0]
    conjunction_timeline(rec_c["initial_state"], rec_c["final_state"], debris,
                         rec_c.get("initial_conjunction", {}).get("horizon", 90), "fig_conjunction.png")
    avoidance_landscape(rec_c["initial_state"], debris, 90, "fig_avoidance_search.png")

    # 3) anomaly_response (decay + 抬升 +40 km + 55% 推力故障自愈 + 规避≥50km)
    rec_a = run_scenario({"kind": "anomaly_response", "target_altitude_km": 466.558,
                          "desired_miss_km": 50, "initial_fuel_kg": 50, **EPO})
    decay_projection(rec_a, "fig_decay.png")

    # 校验表
    def row(name, d):
        if name == "raise":
            return [name, f"{d['initial_mean_alt_km']:.2f}", f"{d['target_alt_km']:.1f}",
                    f"{d['analytic_single_burn_dv_ms']:.3f}", f"{d['sim_dv_ms']:.3f}",
                    f"{d['dv_reproduction_error_pct']:.3f}", f"{d['sim_final_alt_km']:.2f}",
                    f"{d['fuel_reproduction_error_pct']:.3f}"]
        if name == "collision":
            return [name, f"{d['initial_miss_km']:.2f}", "≥10", "—", f"{d['sim_dv_ms']:.3f}",
                    "—", f"{d['final_miss_km']:.2f}", "—"]
        return [name, f"{d.get('initial_mean_alt_km','-'):.2f}", f"{d.get('target_alt_km','-'):.1f}",
                f"{d.get('analytic_single_burn_dv_ms','-'):.3f}", f"{d.get('sim_raise_dv_ms','-'):.3f}",
                "自愈*", f"{d.get('sim_final_alt_km','-')}", "—"]

    lines = ["| 场景 | 初始高度(km) | 目标 | 解析Δv(m/s) | 仿真Δv(m/s) | Δv误差% | 终态高度(km) | 燃料误差% |",
             "|------|------|------|------|------|------|------|------|"]
    for nm, sc in [("raise", {"kind": "raise", "target_altitude_km": 466.558, "initial_fuel_kg": 50, **EPO}),
                   ("collision", {"kind": "collision", "desired_miss_km": 80, "initial_fuel_kg": 50, **EPO}),
                   ("anomaly_response", {"kind": "anomaly_response", "target_altitude_km": 466.558,
                                         "desired_miss_km": 50, "initial_fuel_kg": 50, **EPO})]:
        lines.append("| " + " | ".join(row(nm, analyze(sc))) + " |")
    md = "# 理论值 vs 仿真值 核对表\n\n" + "\n".join(lines) + "\n\n" + (
        "说明：固定历元 `2026-10-10T12:00:00` + 固定 ISS 快照（FIXED_ISS_TLE），结果可复现（抬升幅度统一为 **+40 km**，"
        "目标高度 = 固定快照基线 426.558 + 40 = 466.558 km，与旗舰闭环同幅度）。"
        f"抬升 Δv 由活力公式单脉冲改半长轴精确给出，与仿真误差 {ana_r['dv_reproduction_error_pct']:.3f}%"
        f"（解析={ana_r['analytic_single_burn_dv_ms']:.3f} vs 仿真={ana_r['sim_dv_ms']:.3f} m/s）；"
        f"燃料由火箭方程独立复算，误差 {ana_r['fuel_reproduction_error_pct']:.3f}%。"
        "碰撞规避以最小接近距离为优化目标，枚举搜索在真实二体传播下求最优（表中目标列 `≥10` 为"
        "系统内部安全阈值；任务级阈值如 ≥50 km 由 `desired_miss_km` 单独传入）。"
        "*anomaly_response 为推进器效能异常故障自愈口径（目标＝初态 +40 km）：第一次脉冲仅交付 55%，"
        "欠交付超出 5 km 容差 → 验证器真判未达标 → 自主重规划补脉冲，故其 Δv 与单脉冲解析值不同属设计预期。"
        "完整推导见 `理论推导与原理.md`。\n")
    with open(os.path.join(os.path.dirname(OUT), "validation_table.md"), "w", encoding="utf-8") as f:
        f.write(md)

    # 闭环流程图（FDIR）
    flow = render_flow_diagram()

    print("FIGURES:", os.listdir(OUT))
    print("FLOW_DIAGRAM:", os.path.basename(flow))
    print("VALIDATION_TABLE written.")


if __name__ == "__main__":
    main()
