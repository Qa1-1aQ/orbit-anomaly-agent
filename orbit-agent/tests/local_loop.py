"""本地闭环测试：不依赖 AGH/Agnes，用确定性规划跑通两个场景，
打印验证表并把完整记录存为 evidence.json（可作为提交用运行与验证证据）。

运行:  cd orbit-agent && python tests/local_loop.py
"""
import json
import os
import sys

# 允许从任意目录运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.mission import run_scenario


def _print_verify(rec, title):
    ver = rec["verification"]
    print(f"\n=== {title} ===")
    print(f"判定: {ver['verdict']}")
    m = ver["metrics"]
    for k, v in m.items():
        print(f"  {k}: {v}")
    return ver["passed"]


def main():
    print(">>> 场景A：轨道衰减抬升 (raise_altitude)")
    rec_a = run_scenario({"kind": "raise", "target_altitude_km": 430,
                          "primary_target": "ISS (ZARYA)", "initial_fuel_kg": 50})
    if "error" in rec_a:
        print("ERROR:", rec_a["error"]); return
    ok_a = _print_verify(rec_a, "抬升轨道验证")

    print("\n>>> 场景B：空间碎片碰撞规避 (collision_avoid)")
    rec_b = run_scenario({"kind": "collision", "desired_miss_km": 80,
                          "offset_km": 30.0, "initial_fuel_kg": 50})
    if "error" in rec_b:
        print("ERROR:", rec_b["error"]); return
    ok_b = _print_verify(rec_b, "碰撞规避验证")
    print(f"\n  规避前最小距离: {rec_b['initial_conjunction']['worst']['min_range_km']} km")
    print(f"  规避后最小距离: {rec_b['final_conjunction']['worst']['min_range_km']} km")

    out = {"scenario_a_raise": rec_a, "scenario_b_collision": rec_b,
           "summary": {"scenario_a_pass": ok_a, "scenario_b_pass": ok_b}}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n>>> 证据已保存: {path}")
    print(f">>> 总体: 场景A {'PASS' if ok_a else 'FAIL'} | 场景B {'PASS' if ok_b else 'FAIL'}")


if __name__ == "__main__":
    main()
