"""烟测：绕过 AGH 网页，直接驱动 orbit-agent 的 MCP 工具函数，
验证「配置→工具可用→完整闭环→理论核对→可视化→仿真」全程口径一致。
等价于 AGH 在后台对工具链的调用，但无需 WSL/网页。
"""
import json
import tools.server as srv
from tools.tle import FIXED_ISS_TLE

print("=" * 70)
print("STEP 0  服务模块加载（AGH 挂载时也会先 import 本模块）")
print("=" * 70)
tools = [n for n in dir(srv) if not n.startswith("_") and callable(getattr(srv, n))
         and n in {"get_tle","get_state","propagate","plan_maneuver_tool",
                   "apply_maneuver_tool","conjunction","verify_tool",
                   "run_scenario_tool","visualize_tool","analyze_tool","simulate_tool"}]
print(f"  已识别工具数: {len(tools)} -> {tools}")

print("\n" + "=" * 70)
print("STEP 1  get_tle（内置样例，可复现，不假装实时）")
print("=" * 70)
tle = srv.get_tle("ISS (ZARYA)", force_live=False)
print(f"  source={tle.get('source')}  epoch={tle.get('epoch_utc')}")

print("\n" + "=" * 70)
print("STEP 2  get_state（钉定 FIXED_ISS_TLE + 固定历元 FIXED_EPOCH）")
print("=" * 70)
st = srv.get_state(FIXED_ISS_TLE)
print(f"  mean_altitude_km = {st['mean_altitude_km']}   instant={st['instant_altitude_km']}")
print(f"  a_km={st['elements']['a_km']:.3f}  i={st['elements']['i_deg']:.4f}°  period={st['elements']['period_min']:.3f} min")
assert abs(st["mean_altitude_km"] - 426.558) < 0.5, "基线分裂！平均高度不是 426.558"

print("\n" + "=" * 70)
print("STEP 3  逐步：plan → apply → verify（手动抬升 40km）")
print("=" * 70)
plan = srv.plan_maneuver_tool(st, "raise_altitude", {"target_altitude_km": 466.558})
print(f"  plan.total_dv_kms = {plan['total_dv_kms']:.6f}  (= {plan['total_dv_kms']*1000:.3f} m/s)")
fin = srv.apply_maneuver_tool(st, plan)
ver = srv.verify_tool(st, fin, "raise_altitude", {"target_altitude_km": 466.558},
                      dv_events=[plan["total_dv_kms"]], initial_fuel_kg=50)
m = ver["metrics"]
print(f"  final_mean_alt = {m['final_mean_altitude_km']:.3f} km  err={m['altitude_error_km']:.4f}  "
      f"dv={m['total_dv_ms']:.3f} m/s  fuel_rem={m['fuel_remaining_kg']:.3f}  PASS={bool(ver['passed'])}")
assert abs(m["final_mean_altitude_km"] - 466.558) < 0.5, "抬升目标不是 466.558"

print("\n" + "=" * 70)
print("STEP 4  run_scenario_tool（复合异常 + 55% 推进器故障注入）")
print("=" * 70)
scn = {"kind": "anomaly_response", "target_altitude_km": 466.558,
       "desired_miss_km": 50, "initial_fuel_kg": 50,
       "inject_fault": "thruster_underperformance"}
res = srv.run_scenario_tool(scn)
print(f"  raise final_alt = {res.get('raise_verification',{}).get('metrics',{}).get('final_mean_altitude_km')}")
print(f"  recovered_from_fault = {res.get('recovered_from_fault')}")
print(f"  avoidance final_min_miss_km = {res.get('avoid_verification',{}).get('metrics',{}).get('final_min_miss_km')}")
print(f"  overall_passed = {res.get('overall_passed')}  (规避物理封顶≈15km < 50km → 诚实 FAIL)")

print("\n" + "=" * 70)
print("STEP 5  analyze_tool（第一性原理 vs 仿真 核对）")
print("=" * 70)
ana = srv.analyze_tool(scn)
print(f"  analytic_single_burn_dv_ms = {ana.get('analytic_single_burn_dv_ms')}")
print(f"  sim_dv_ms = {ana.get('sim_dv_ms')}")
print(f"  dv_reproduction_error_pct = {ana.get('dv_reproduction_error_pct')}")

print("\n" + "=" * 70)
print("STEP 6  visualize_tool（生成 SVG 示意图）")
print("=" * 70)
viz = srv.visualize_tool({"kind": "anomaly_response", "target_altitude_km": 466.558})
print(f"  svg_path = {viz.get('svg_path')}")
print(f"  summary = {json.dumps(viz.get('summary'), ensure_ascii=False)}")

print("\n" + "=" * 70)
print("STEP 7  simulate_tool('all', n=120)（仿真实验 + 5 图）")
print("=" * 70)
sim = srv.simulate_tool("all", n=120)
exp_keys = [k for k in sim.keys() if k != "figure_dir"]
print(f"  experiments = {exp_keys}")
print(f"  target_alt_km = {sim.get('raise_mc',{}).get('target_alt_km') if isinstance(sim.get('raise_mc'),dict) else 'n/a'}")
import os as _os
fig_dir = sim.get("figure_dir")
figs = _os.listdir(fig_dir) if fig_dir and _os.path.isdir(fig_dir) else []
print(f"  figure_dir = {fig_dir}")
print(f"  生成的图 ({len(figs)}): {sorted(figs)}")
assert _os.path.isfile(_os.path.join(fig_dir, "fig_raise_mc.png")), "缺少 fig_raise_mc.png"
# 顺带确认 reality_check 的物理标定结论
rc = sim.get("reality_check")
if isinstance(rc, dict):
    for k in ("all_within_range", "reality_all_within_range"):
        if k in rc:
            print(f"  reality_all_within_range = {rc[k]}")
            break

print("\n" + "=" * 70)
print("✅ 烟测全部通过：工具链在代码层完整闭环，基线 426.558/466.558 一致，规避诚实 FAIL。")
print("=" * 70)
