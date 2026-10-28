"""一键复现：运行全部验证与仿真，生成所有图表/报告，并写出可复现清单（manifest）。

评委/用户只需执行：
    python reproduce.py
即可：① 跑 10 例测试套件  ② 生成 5 张验证图 + 闭环流程图  ③ 跑 5 组仿真实验（n=300）
④ 刷新核对表与仿真报告  ⑤ 写出 output/REPRODUCE_MANIFEST.json（含关键指标 + 文件校验和）。

所有数值基于固定历元 + 内置样例 TLE，完全可复现。
"""
import os
import sys
import json
import hashlib
import subprocess
import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "output")


def _sha256(path, n=1 << 20):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(n)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()[:16]
    except Exception:
        return "n/a"


def _run(cmd):
    print(f"\n>>> {cmd}")
    r = subprocess.run(cmd, shell=True, cwd=ROOT,
                       capture_output=False)
    return r.returncode == 0


def main():
    ok = True
    steps = []

    # 1) 测试套件
    ok &= _run(f'"{sys.executable}" tests/run_suite.py')
    steps.append("tests/run_suite.py")

    # 2) 验证图 + 流程图 + 核对表
    ok &= _run(f'"{sys.executable}" -m tools.make_figures')
    steps.append("tools/make_figures")

    # 3) 仿真实验（全部 5 组，n=300）
    ok &= _run(f'"{sys.executable}" -c "from tools.simulate import run_all_experiments; '
              f'run_all_experiments(\'output\', n=300)"')
    steps.append("tools.simulate.run_all_experiments(n=300)")

    # 3b) 场景 SVG（规范主图 + raise/collision 分文件，互不覆盖）
    ok &= _run(f'"{sys.executable}" -c "from tools.viz import render_scene_svgs; '
              f'print(render_scene_svgs())"')
    steps.append("tools.viz.render_scene_svgs()")

    # 4) 采集关键指标（旗舰闭环 + 抬升解析核对 + 蒙特卡洛 + 真实标定）
    metrics = {}
    try:
        from tools.analysis import analyze
        from tools.simulate import (experiment_raise_mc, experiment_raise_sensitivity,
                                    experiment_reality_check)
        from tools.viz import render_flagship_svg
        EPO = {"epoch_utc": "2026-10-10T12:00:00.000"}

        # 4a) 旗舰闭环：anomaly_response（+40km 抬升 + 55% 推力故障自愈 + 规避≥50km）
        try:
            _svg, rec = render_flagship_svg()
            planned = (rec.get("manoeuvre") or {}).get("planned", {}) or {}
            rmet = (rec.get("raise_verification") or {}).get("metrics", {}) or {}
            amt = (rec.get("avoid_verification") or {}).get("metrics", {}) or {}
            init_alt = planned.get("current_mean_altitude_km")
            final_alt = rmet.get("final_mean_altitude_km")
            dv_kms = rmet.get("total_dv_kms")
            floor_alt = round((rec.get("scenario") or {}).get("target_altitude_km") or 0, 3) or None
            metrics.update({
                "flagship_scenario": "anomaly_response(+40km, 55% thruster fault, avoid>=50km)",
                "initial_mean_alt_km": round(init_alt, 3) if init_alt else None,
                "target_alt_km": floor_alt,
                "final_mean_alt_km": round(final_alt, 3) if final_alt else None,
                "delta_a_km": (round(final_alt - init_alt, 3)
                               if (init_alt and final_alt) else None),
                "total_dv_ms": round(dv_kms * 1000, 3) if dv_kms else None,
                "fuel_remaining_kg": rmet.get("fuel_remaining_kg"),
                "avoid_min_distance_km": amt.get("final_min_miss_km"),
                "avoid_threshold_km": 50.0,
                "avoid_passed": (amt.get("final_min_miss_km") or 0) >= 50.0,
                "recovered_from_fault": rec.get("recovered_from_fault"),
                "overall_passed": rec.get("overall_passed"),
                "tle_source": (rec.get("tle") or {}).get("source"),
            })
        except Exception as e:
            metrics["flagship_error"] = str(e)

        # 4b) 抬升解析核对（+40 km，与旗舰、仿真同幅度）
        ra = analyze({**EPO, "kind": "raise", "target_altitude_km": 466.558, "initial_fuel_kg": 50})
        mc = experiment_raise_mc(n=300)
        sens = {r["vel_sigma_ms"]: round(r["success_rate_pct"], 1)
                for r in experiment_raise_sensitivity(n=300)}
        rc = experiment_reality_check()
        metrics.update({
            "raise_mc_target_km": mc["target_alt_km"],
            "raise_dv_analytic_ms": ra["analytic_single_burn_dv_ms"],
            "raise_dv_sim_ms": ra.get("sim_dv_ms"),
            "raise_dv_error_pct": ra["dv_reproduction_error_pct"],
            "raise_final_alt_km": ra.get("sim_final_alt_km"),
            "raise_fuel_error_pct": ra.get("fuel_reproduction_error_pct"),
            "mc_success_rate_pct": round(mc["success_rate_pct"], 1),
            "mc_alt_err_1sigma_km": round(mc["alt_err_std_km"], 3),
            "mc_sensitivity_pct": sens,
            "reality_all_within_range": all(r["within_range"] for r in rc["rows"]),
        })
    except Exception as e:
        metrics["error"] = str(e)

    # 5) manifest（artifacts 只收录真实存在的文件，缺项自动剔除，保证清单与产物一致）
    key_files = [
        "README.md", "理论推导与原理.md", "实际工程落地可行性分析.md",
        "AGH接入说明.md", "DEMO_SCRIPT_3MIN.md", "演示提问_成败样例.md",
        "tests/测试报告.md",
        "output/validation_table.md", "output/仿真实验报告.md", "output/stress_test_report.md",
        "output/orbit_figure.svg", "output/orbit_figure_raise.svg", "output/orbit_figure_collision.svg",
        "output/preview/flow_diagram.png",
        "output/figures/fig_altitude_timeline.png", "output/figures/fig_dv_budget.png",
        "output/figures/fig_conjunction.png", "output/figures/fig_decay.png",
        "output/figures/fig_avoidance_search.png",
        "output/sim/fig_raise_mc.png", "output/sim/fig_method_compare.png",
        "output/sim/fig_avoid_compare.png", "output/sim/fig_fault_sweep.png",
        "output/sim/fig_reality_check.png",
    ]
    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "reproducible": True,
        "fixed_epoch": "2026-10-10T12:00:00.000",
        "steps_run": steps,
        "key_metrics": metrics,
        "artifacts": {f: _sha256(os.path.join(ROOT, f)) for f in key_files
                      if os.path.exists(os.path.join(ROOT, f))},
        "all_steps_ok": bool(ok),
    }
    mp = os.path.join(OUT, "REPRODUCE_MANIFEST.json")
    os.makedirs(OUT, exist_ok=True)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("REPRODUCE", "OK ✅" if ok else "FAILED ❌")
    print("关键指标:", json.dumps(metrics, ensure_ascii=False))
    print("清单已写出:", mp)
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
