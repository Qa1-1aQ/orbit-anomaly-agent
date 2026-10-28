"""测试样例套件：正常 / 边界 / 失败 三类，输出 tests/测试报告.md。

直接运行：python tests/run_suite.py
（需先 pip install -r requirements.txt，并在 orbit-agent 根目录或带 PYTHONPATH 运行）
"""
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.mission import run_scenario


# 三类测试样例
SUITE = [
    # ---- 正常 ----
    ("正常", "轨道抬升（目标 430km）",
     {"kind": "raise", "target_altitude_km": 430, "initial_fuel_kg": 50}),
    ("正常", "碰撞规避（offset 30km）",
     {"kind": "collision", "offset_km": 30, "horizon_minutes": 90, "initial_fuel_kg": 50}),

    # ---- 边界 ----
    ("边界", "极小抬升（目标 426km，接近初始高度）",
     {"kind": "raise", "target_altitude_km": 426, "initial_fuel_kg": 50}),
    ("边界", "临界接近（offset 10km，接近安全阈值）",
     {"kind": "collision", "offset_km": 10, "horizon_minutes": 90, "initial_fuel_kg": 50}),
    ("边界", "低燃料预算（initial_fuel_kg=2）",
     {"kind": "raise", "target_altitude_km": 430, "initial_fuel_kg": 2}),

    # ---- 失败 / 异常分支（故障注入，演示安全降级与重规划）----
    ("失败", "燃料耗尽 -> 降级告警（不执行）",
     {"kind": "raise", "target_altitude_km": 430, "initial_fuel_kg": 50, "inject_fault": "fuel_exhausted"}),
    ("失败", "数据获取失败 -> 降级样本（可复现）",
     {"kind": "raise", "target_altitude_km": 430, "initial_fuel_kg": 50, "inject_fault": "data_unavailable"}),
    ("失败", "推进器单脉冲失效 -> 重规划为双半脉冲",
     {"kind": "raise", "target_altitude_km": 430, "initial_fuel_kg": 50, "inject_fault": "thruster_failure"}),

    # ---- 复合异常自主处置（含推进器效能异常→自主重规划恢复，题眼）----
    ("正常", "复合异常自主处置（衰减+碰撞，推进器效能异常→重规划恢复）",
     {"kind": "anomaly_response", "target_altitude_km": 430, "desired_miss_km": 50,
      "initial_fuel_kg": 50, "inject_fault": "thruster_underperformance"}),
    ("失败", "复合异常（数据不可用→降级样本仍可处置）",
     {"kind": "anomaly_response", "target_altitude_km": 430, "initial_fuel_kg": 50,
      "inject_fault": "data_unavailable"}),
]


def summarize(rec):
    if "overall_passed" in rec:
        v = rec.get("raise_verification", {}) or {}
        m = v.get("metrics", {}) if v else {}
        av = rec.get("avoid_verification", {}) or {}
        am = av.get("metrics", {}) if av else {}
        return {
            "passed": rec.get("overall_passed"),
            "verdict": (f"异常处置{'成功' if rec.get('overall_passed') else '降级'}；"
                        f"异常={rec.get('anomalies')}；恢复={rec.get('recovered_from_fault')}"),
            "degraded": rec.get("data_degraded", False) or (not rec.get("overall_passed", True)),
            "replanned": rec.get("recovered_from_fault", False),
            "data_degraded": rec.get("data_degraded", False),
            "final_alt": m.get("final_mean_altitude_km"),
            "alt_err": m.get("altitude_error_km"),
            "final_miss": am.get("final_min_miss_km"),
            "dv_kms": m.get("total_dv_kms"),
            "fuel_remain": m.get("fuel_remaining_kg"),
        }
    v = rec.get("verification", {})
    m = v.get("metrics", {})
    return {
        "passed": v.get("passed"),
        "verdict": v.get("verdict"),
        "degraded": rec.get("degraded", False),
        "replanned": rec.get("replanned", False),
        "data_degraded": rec.get("data_degraded", False),
        "final_alt": m.get("final_mean_altitude_km"),
        "alt_err": m.get("altitude_error_km"),
        "final_miss": m.get("final_min_miss_km"),
        "dv_kms": m.get("total_dv_kms"),
        "fuel_remain": m.get("fuel_remaining_kg"),
    }


def main():
    rows = []
    for cat, name, sc in SUITE:
        try:
            rec = run_scenario(sc)
            if "error" in rec:
                rows.append((cat, name, {"passed": False, "verdict": "ERROR: " + rec["error"]}))
                continue
            rows.append((cat, name, summarize(rec)))
        except Exception as e:
            rows.append((cat, name, {"passed": False, "verdict": f"EXCEPTION: {e}"}))
    write_report(rows)
    # 控制台摘要
    for cat, name, s in rows:
        mark = "PASS" if s.get("passed") else "----"
        print(f"[{cat}] {name}: {mark} | {s.get('verdict')}")


def write_report(rows):
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "测试报告.md")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# 测试报告（正常 / 边界 / 失败）\n")
    L.append(f"> 自动生成：{now}  |  工具：`tools/mission.run_scenario`\n")
    L.append("## 总览\n")
    L.append("| 类别 | 用例 | 判定 | 关键结论 |")
    L.append("|------|------|------|----------|")
    for cat, name, s in rows:
        mark = "✅ PASS" if s.get("passed") else "⛔ 降级/失败"
        note = s.get("verdict", "")
        L.append(f"| {cat} | {name} | {mark} | {note} |")
    L.append("\n## 各用例明细\n")
    for cat, name, s in rows:
        L.append(f"### {cat} · {name}\n")
        L.append(f"- 判定：`{'PASS' if s.get('passed') else 'FAIL/降级'}`")
        L.append(f"- 结论：{s.get('verdict','')}")
        if s.get("degraded"):
            L.append("- 异常分支：触发降级告警（degraded=true）")
        if s.get("replanned"):
            L.append("- 异常分支：推进器失效后已重规划并成功执行（replanned=true）")
        if s.get("data_degraded"):
            L.append("- 异常分支：数据获取失败，已降级为内置样例（data_degraded=true）")
        if s.get("final_alt") is not None:
            L.append(f"- 终态平均高度：{s['final_alt']} km（误差 {s.get('alt_err')} km）")
        if s.get("final_miss") is not None:
            L.append(f"- 规避后最小接近距离：{s['final_miss']} km")
        if s.get("dv_kms") is not None:
            L.append(f"- 总 Δv：{s['dv_kms']} km/s")
        if s.get("fuel_remain") is not None:
            L.append(f"- 剩余燃料：{s['fuel_remain']} kg")
        L.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\n报告已写入：{out}")


if __name__ == "__main__":
    main()
