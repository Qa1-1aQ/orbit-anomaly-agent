"""可视化：将场景记录渲染为 SVG 示意图（纯 Python，无第三方绘图依赖）。

生成两张子图：左为轨道机动示意图（初始/终态轨道 + 点火方向），右为接近几何
（主航天器 vs 编目碎片，标注最小接近距离）。输出文件并返回路径，供 AGH 演示展示。

设计要点（保证"准确且看得懂"）：
  1) 轨道经过像素投影后才成图，不会出现"坐标用公里原值导致画到画布外"的问题；
  2) 抬轨量级（真实仅 ~6 km，占轨道半径 0.09%）肉眼不可辨，故左图对"轨道高度差"
     做显式放大，并在图内标注真实数值与"已放大"提示（图题即为"示意图"）；
  3) 左图抬轨终态优先用 raise_final_state（抬升后、规避前），避免被规避机动结果覆盖，
     从而与"抬轨 430 km 达成"的验证结论一致；
  4) 右图优先用"终态"接近几何（规避后），与底部 PASS 指标口径一致；无碎片时明确说明；
  5) 所有数值（高度、Δv、燃料、最近距离、PASS/FAIL）均直接取自记录，不另算；
  6) 输出按场景分文件：只有旗舰场景 anomaly_response 写规范主图 output/orbit_figure.svg，
     其余场景（raise/collision）各写 orbit_figure_<kind>.svg，避免任意一次 visualize 调用
     把"演示主图"静默覆盖成别的场景（曾发生：collision 演示图覆盖了主闭环图，导致图与结论矛盾）。
"""
import os
import math
import numpy as np

from .constants import R_EARTH
from .kepler import rv_to_coe

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 画布与面板几何
_W, _H = 760, 470
_P1 = (12, 40, 366, 348)     # 左面板 x,y,w,h
_P2 = (382, 40, 366, 348)    # 右面板
_C1 = (195, 195)             # 左面板中心
_C2 = (575, 195)             # 右面板中心
_EARTH_R_PX = 58.0           # 地球示意半径(px)
_GAP_PX = 48.0               # 最低轨道离地球表面显示间距(px)
_SPREAD_PX = 18.0            # 两条轨道的高度差显示间距(px) —— 放大后的
_RY_RATIO = 0.86             # y 方向压扁，营造"斜视"立体感
_ARROW_PX = 42.0             # Δv 箭头显示长度(px)


def _norm(v):
    return float(np.linalg.norm(v))


def _text_w(s):
    """粗略估算文本像素宽度（中日韩字符按 12px、ASCII 按 6.6px），用于避免越界/重叠。"""
    return sum(12.0 if ord(ch) > 127 else 6.6 for ch in s)


def _ellipse_pt(c, R, theta):
    """以中心 c、水平半径 R、垂直半径 R*_RY_RATIO 的参数化椭圆取点(屏幕坐标)。"""
    return (c[0] + R * math.cos(theta), c[1] - R * _RY_RATIO * math.sin(theta))


def _ellipse_path(c, R, n=120):
    pts = []
    for k in range(n + 1):
        x, y = _ellipse_pt(c, R, 2 * math.pi * k / n)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _extract_debris(record):
    """从记录中稳健提取碎片列表 -> [(name, state_dict), ...]。"""
    out = []
    for key in ("debris_catalog", "debris_objects"):
        seq = record.get(key)
        if not isinstance(seq, list):
            continue
        for d in seq:
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("state"), dict):
                out.append((str(d.get("name") or "DEBRIS"), d["state"]))
            elif "r_km" in d:
                out.append((str(d.get("name") or "DEBRIS"), d))
        if out:
            break
    return out


def _extract_conjunction(record):
    """取接近分析结果；优先"终态(规避后)"，其次"初始"。返回 (conj, is_final)。"""
    av = record.get("conjunction_avoidance")
    cands = [
        (record.get("final_conjunction"), True),
        (av.get("conj_after") if isinstance(av, dict) else None, True),
        (record.get("conjunction_initial"), False),
        (record.get("initial_conjunction"), False),
    ]
    for c, is_final in cands:
        if isinstance(c, dict) and c.get("worst"):
            return c, is_final
    return {}, False


def _pick_metrics(record):
    """统一取出 (抬轨验证, 规避验证)，兼容 raise / collision / anomaly 三种记录结构。"""
    rv = record.get("raise_verification") or {}
    av = record.get("avoid_verification") or {}
    if rv.get("metrics") or av.get("metrics"):
        return rv, av
    plain = record.get("verification") or {}
    if plain.get("objective") == "collision_avoid":
        return rv, plain
    if plain.get("objective") == "raise_altitude":
        return plain, av
    return rv, av


# 旗舰场景（作品核心）：复合异常自主处置。只有它才会写「规范主图」orbit_figure.svg，
# 其余场景（raise / collision）各写自己的文件，避免任意一次 visualize 调用把主图覆盖掉。
_CANON_KIND = "anomaly_response"

_KIND_ALIAS = {
    "raise_altitude": "raise", "altitude_raise": "raise", "orbit_raise": "raise",
    "reboost": "raise",
    "collision_avoid": "collision", "conjunction": "collision",
    "avoid": "collision", "evade": "collision",
    "anomaly": "anomaly_response", "disposal": "anomaly_response",
    "fault_response": "anomaly_response",
}


def _scenario_kind(record):
    """从记录里稳健地取场景类型，决定 SVG 输出文件名（避免场景间相互覆盖）。"""
    sc = record.get("scenario")
    kind = sc.get("kind") if isinstance(sc, dict) else None
    if not kind:
        kind = record.get("kind") or record.get("objective")
    kind = str(kind or "").strip().lower()
    return _KIND_ALIAS.get(kind, kind) or "scenario"


def render_orbit_svg(record, out_path=None):
    """把场景记录渲染为 SVG 示意图。

    输出命名（避免"任意一次 visualize 覆盖主图"）：
      - 旗舰场景 anomaly_response -> 规范主图 output/orbit_figure.svg（且始终同步刷新）；
      - 其它场景 raise / collision -> output/orbit_figure_<kind>.svg。
    显式传入 out_path 时以其为准（若为旗舰场景，仍会同步刷新规范主图）。
    """
    kind = _scenario_kind(record)
    outdir = os.path.join(_PROJECT_ROOT, "output")
    canonical = os.path.join(outdir, "orbit_figure.svg")
    if out_path is None:
        out_path = (canonical if kind == _CANON_KIND
                    else os.path.join(outdir, f"orbit_figure_{kind}.svg"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    st0 = record.get("initial_state")
    if not st0:
        # 返回解析后的真实路径（跟随软链），避免在 WSL 软链场景下打印出误导性的逻辑路径
        return os.path.realpath(out_path)
    # 抬轨终态优先（异常处置闭环里 final_state 是"含规避"的终态，会覆盖抬轨结果）
    stf = record.get("raise_final_state") or record.get("final_state") or st0

    el0 = rv_to_coe(st0["r_km"], st0["v_kms"])
    elf = rv_to_coe(stf["r_km"], stf["v_kms"])

    alt0 = el0["a_km"] - R_EARTH
    altf = elf["a_km"] - R_EARTH
    alt_min, alt_max = min(alt0, altf), max(alt0, altf)
    span = alt_max - alt_min

    def r_disp(alt):
        if span < 1e-6:
            return _EARTH_R_PX + _GAP_PX + _SPREAD_PX * 0.5
        return _EARTH_R_PX + _GAP_PX + (alt - alt_min) / span * _SPREAD_PX

    R0, Rf = r_disp(alt0), r_disp(altf)

    # ---- 卫星位置角（用 x-y 方向定角，落点必在椭圆上）----
    r0v = np.asarray(st0["r_km"], float)
    rfv = np.asarray(stf["r_km"], float)
    th0 = math.atan2(r0v[1], r0v[0]) if (abs(r0v[0]) + abs(r0v[1])) > 0 else 0.0
    thf = math.atan2(rfv[1], rfv[0]) if (abs(rfv[0]) + abs(rfv[1])) > 0 else 0.0
    p0 = _ellipse_pt(_C1, R0, th0)
    pf = _ellipse_pt(_C1, Rf, thf)

    # ---- Δv 箭头（沿速度方向，屏幕长度固定）----
    v0 = np.asarray(st0["v_kms"], float)
    vxy = np.array([v0[0], -v0[1]], float)          # 屏幕 y 向下 -> 取负
    vn = _norm(vxy)
    uv = vxy / vn if vn > 1e-12 else np.array([1.0, 0.0])
    pe = (p0[0] + uv[0] * _ARROW_PX, p0[1] + uv[1] * _ARROW_PX)
    perp = np.array([-uv[1], uv[0]])
    a1 = (pe[0] + perp[0] * 5, pe[1] + perp[1] * 5)
    a2 = (pe[0] - perp[0] * 5, pe[1] - perp[1] * 5)
    head = f"{pe[0]:.1f},{pe[1]:.1f} {a1[0]:.1f},{a1[1]:.1f} {a2[0]:.1f},{a2[1]:.1f}"

    # ---- 右图：接近几何 ----
    conj, is_final = _extract_conjunction(record)
    enc = {e.get("name"): e for e in (conj.get("encounters") or []) if isinstance(e, dict)}
    worst = conj.get("worst") or {}
    # 与所用接近分析口径一致的状态原点：终态分析 -> 用最终状态；初始分析 -> 用初始状态
    origin = (record.get("final_state") or st0) if is_final else st0
    org = np.asarray(origin["r_km"], float)

    debris = _extract_debris(record)
    # 若碎片无名字（collision 场景），用接近分析里的名字补齐
    if debris and all(nm == "DEBRIS" for nm, _ in debris) and worst.get("name"):
        debris = [(worst["name"], s) for _, s in debris]

    items = []          # (name, ux, uy, range_km)
    for nm, dstate in debris:
        try:
            dr = np.asarray(dstate["r_km"], float) - org
        except Exception:
            continue
        hyp = float(np.hypot(dr[0], dr[1]))
        ux, uy = (dr[0] / hyp, dr[1] / hyp) if hyp > 1e-9 else (1.0, 0.0)
        rng = hyp
        if nm in enc and enc[nm].get("min_range_km") is not None:
            rng = float(enc[nm]["min_range_km"])
        items.append((nm, ux, uy, rng))

    SC = min(_P2[2] / 2 - 34, _P2[3] / 2 - 46) / max([it[3] for it in items] + [1e-6])

    # 点位 + 标签：宽度感知的左右翻转 + 简单防重叠
    marks = []
    closest = None
    for nm, ux, uy, rng in items:
        px = _C2[0] + ux * rng * SC
        py = _C2[1] - uy * rng * SC
        dist = math.hypot(px - _C2[0], py - _C2[1])
        is_worst = (nm == worst.get("name"))
        miss = worst.get("min_range_km")
        # 碎片非常贴近主航天器时，把最接近距离并入名称标签，避免标签互相压住
        label = f"{nm} · min {miss:.2f} km" if (is_worst and miss is not None and dist < 48) else nm
        w = _text_w(label)
        if ux >= 0:
            lx, anchor = px + 12, "start"
            if lx + w > 744:                      # 右侧放不下 -> 翻到左侧
                lx, anchor = px - 10, "end"
        else:
            lx, anchor = px - 12, "end"
            if lx - w < 388:                      # 左侧放不下 -> 翻到右侧
                lx, anchor = px + 10, "start"
        marks.append({"px": px, "py": py, "ux": ux, "uy": uy, "label": label,
                      "lx": lx, "anchor": anchor,
                      "ly": min(max(py - uy * 12 + 4, 58), 380)})
        if is_worst:
            closest = (px, py, ux, uy, dist, miss)

    anchors = [{"lx": _C2[0] - 12, "ly": _C2[1] + 4}]      # 主航天器标签占位
    for mk in sorted(marks, key=lambda d: d["ly"]):
        ly = mk["ly"]
        for a in anchors:
            if abs(ly - a["ly"]) < 14 and abs(mk["lx"] - a["lx"]) < 150:
                ly = a["ly"] + 15
        mk["ly"] = min(ly, 380)
        anchors.append({"lx": mk["lx"], "ly": mk["ly"]})

    debris_svg = []
    for mk in marks:
        debris_svg.append(
            f'<circle cx="{mk["px"]:.1f}" cy="{mk["py"]:.1f}" r="5" fill="#d64545"/>'
            f'<text x="{mk["lx"]:.1f}" y="{mk["ly"]:.1f}" text-anchor="{mk["anchor"]}" '
            f'font-size="11" fill="#ef9a9a">{mk["label"]}</text>')

    line = ""
    min_text = ""
    if closest is not None:
        px, py, ux_, uy_, dist, miss = closest
        line = (f'<line x1="{_C2[0]}" y1="{_C2[1]}" x2="{px:.1f}" y2="{py:.1f}" '
                f'stroke="#ff8f8f" stroke-width="1.5" stroke-dasharray="5 4"/>')
        if dist >= 48 and miss is not None:       # 距离够远才单独标注，避免叠字
            label = f"min {miss:.2f} km"
            w = _text_w(label)
            mx = (_C2[0] + px) / 2 - uy_ * 18
            my = (_C2[1] + py) / 2 + ux_ * 18
            mx = min(max(mx, 392 + w / 2), 746 - w / 2)
            my = min(max(my, 60), 374)
            min_text = (f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" '
                        f'font-size="11" fill="#ffb3b3">{label}</text>')

    if items:
        geo_tag = "终态（规避后）" if is_final else "初始（规避前）"
        panel2_title = f"② 接近几何（相对主航天器 · {geo_tag}）"
        primary_svg = (f'<circle cx="{_C2[0]}" cy="{_C2[1]}" r="7" fill="#2b6cff"/>'
                       f'<text x="{_C2[0] - 12}" y="{_C2[1] + 4}" text-anchor="end" '
                       f'fill="#cfe0ff" font-size="11">主航天器</text>')
    else:
        panel2_title = "② 接近几何（无编目碎片）"
        # 无碎片时不必画主航天器（没有参照对象），只给明确说明
        primary_svg = (f'<text x="{_C2[0]}" y="{_C2[1] - 6}" text-anchor="middle" '
                       f'font-size="12" fill="#8aa0d8">本场景无编目碎片接近事件</text>'
                       f'<text x="{_C2[0]}" y="{_C2[1] + 16}" text-anchor="middle" '
                       f'font-size="11" fill="#6f86bd">（纯轨道机动，故仅左图有效）</text>')

    # ---- 指标摘要 ----
    rv, av = _pick_metrics(record)
    rm = rv.get("metrics", {}) or {}
    am = av.get("metrics", {}) or {}

    s1 = []
    if rm.get("target_altitude_km") is not None:
        s1.append(f"抬轨目标 {rm['target_altitude_km']} km｜终态平均高度 "
                  f"{rm.get('final_mean_altitude_km', '-')} km｜误差 "
                  f"{rm.get('altitude_error_km', '-')} km")
    if rm.get("total_dv_kms") is not None:
        s1.append(f"总 Δv {rm['total_dv_kms'] * 1000:.2f} m/s｜剩余燃料 "
                  f"{rm.get('fuel_remaining_kg', '-')} kg")
    if am.get("final_min_miss_km") is not None:
        s1.append(f"规避后最小距离 {am['final_min_miss_km']} km"
                  f"（阈值 ≥{am.get('required_min_miss_km', '-')} km）")
    line1 = "   |   ".join(s1)

    tags = []
    if rv.get("passed") is not None:
        tags.append("抬轨 " + ("PASS" if rv["passed"] else "FAIL"))
    if av.get("passed") is not None:
        tags.append("规避 " + ("PASS" if av["passed"] else "FAIL"))
    if record.get("recovered_from_fault"):
        tags.append("故障自愈已恢复")
    if not tags and record.get("overall_passed") is not None:
        tags.append("整体 " + ("PASS" if record["overall_passed"] else "FAIL"))
    line2 = "   ·   ".join(tags)

    if span < 1e-6:
        note = f"初始 / 终态轨道一致：平均高度均为 {alt0:.1f} km"
    else:
        note = (f"图幅为示意：轨道高度差已放大以便观察（真实 Δa = "
                f"{elf['a_km'] - el0['a_km']:+.2f} km，即 {alt0:.1f} → {altf:.1f} km）")

    y_leg = _P1[1] + _P1[3] - 30
    y_foot = _P1[1] + _P1[3] + 22

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" font-family="sans-serif">
  <rect width="{_W}" height="{_H}" fill="#0f1830"/>
  <text x="20" y="28" fill="#e6ecff" font-size="16" font-weight="bold">轨道异常处置示意图</text>

  <rect x="{_P1[0]}" y="{_P1[1]}" width="{_P1[2]}" height="{_P1[3]}" fill="#13203f" stroke="#2a3a66"/>
  <text x="24" y="62" fill="#9fb4ff" font-size="13">① 轨道机动（初始 / 终态）</text>
  <circle cx="{_C1[0]}" cy="{_C1[1]}" r="{_EARTH_R_PX}" fill="#2b6cff" opacity="0.85"/>
  <text x="{_C1[0]}" y="{_C1[1] + 4}" text-anchor="middle" fill="#dbe7ff" font-size="11">Earth</text>
  <polygon points="{_ellipse_path(_C1, R0)}" fill="none" stroke="#8f9dc4" stroke-width="1.3" stroke-dasharray="6 4"/>
  <polygon points="{_ellipse_path(_C1, Rf)}" fill="none" stroke="#36d39a" stroke-width="1.8"/>
  <circle cx="{p0[0]:.1f}" cy="{p0[1]:.1f}" r="4.5" fill="#cfd6ee"/>
  <circle cx="{pf[0]:.1f}" cy="{pf[1]:.1f}" r="4.5" fill="#36d39a"/>
  <line x1="{p0[0]:.1f}" y1="{p0[1]:.1f}" x2="{pe[0]:.1f}" y2="{pe[1]:.1f}" stroke="#ffcf4d" stroke-width="2.2"/>
  <polygon points="{head}" fill="#ffcf4d"/>
  <text x="{pe[0] + 6:.1f}" y="{pe[1] - 4:.1f}" fill="#ffcf4d" font-size="11">Δv</text>
  <text x="24" y="{y_leg}" fill="#8aa0d8" font-size="10">灰虚线 = 初始轨道（{alt0:.1f} km）</text>
  <text x="24" y="{y_leg + 16}" fill="#8aa0d8" font-size="10">绿实线 = 终态轨道（{altf:.1f} km）　黄箭头 = 点火脉冲</text>

  <rect x="{_P2[0]}" y="{_P2[1]}" width="{_P2[2]}" height="{_P2[3]}" fill="#13203f" stroke="#2a3a66"/>
  <text x="394" y="62" fill="#9fb4ff" font-size="13">{panel2_title}</text>
  {primary_svg}
  {''.join(debris_svg)}
  {line}
  {min_text}
  <text x="394" y="{y_leg}" fill="#8aa0d8" font-size="10">红点 = 编目碎片</text>
  <text x="394" y="{y_leg + 16}" fill="#8aa0d8" font-size="10">点位到主航天器距离 = 最小接近距离　虚线 = 最接近距离</text>

  <text x="20" y="{y_foot}" fill="#cfe0ff" font-size="11">{line1}</text>
  <text x="20" y="{y_foot + 20}" fill="#8aa0d8" font-size="11">{note}</text>
  <text x="20" y="{y_foot + 42}" fill="#36d39a" font-size="12" font-weight="bold">{line2}</text>
</svg>'''
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    # 旗舰场景：无论走默认路径还是显式路径，都同步刷新规范主图，
    # 保证 output/orbit_figure.svg 永远是「主任务（复合异常自主处置）」的结果。
    if kind == _CANON_KIND and os.path.abspath(out_path) != os.path.abspath(canonical):
        with open(canonical, "w", encoding="utf-8") as f:
            f.write(svg)
    # 返回解析后的真实路径（跟随软链）：WSL 下 ~/orbit-agent/output 软链到 Windows 项目，
    # realpath 会把它还原成 /mnt/d/... 的真实路径，避免打印出 /home/... 这种误导性逻辑路径。
    return os.path.realpath(out_path)


# ======================= 旗舰场景（演示主图）=======================
# 定义唯一一份「旗舰场景」，供出图（本模块）与打包（package.py）共用，避免各处参数漂移。
FLAGSHIP_RAISE_KM = 40.0          # 抬升幅度：初态平均高度 +40 km
FLAGSHIP_DESIRED_MISS_KM = 50.0   # 任务级规避阈值（共轨碎片物理封顶 ~15 km，如实 FAIL）
FLAGSHIP_FAULT = "thruster_underperformance"   # 55% 推进器效能故障 → 真实触发自愈

# 固定历元的真实 ISS TLE 快照统一在 tools/tle.py 定义为 FIXED_ISS_TLE（全系统唯一权威来源），
# 此处仅重新导出，避免口径分裂。用途：让「可复现」的提交主图 / 打包产物恒定
# （固定历元下初态均值 = 426.558 km），不再随每天实时 TLE 漂移导致 manifest 哈希对不上。
# 实时演示仍由 AGH 调 get_tle 拉最新数据、作为 tle 参数传入（走 live 分支），互不影响。
from .tle import FIXED_ISS_TLE


def build_flagship_scenario(tle=None):
    """构造旗舰场景：复合异常自主处置 + 55% 推进器效能故障自愈。

    TLE 默认用**固定历元的真实 ISS 快照** FIXED_ISS_TLE（保证可复现，无需联网）；
    外部传入 tle（如 AGH 实跑时把 get_tle 结果作为 tle 参数）则优先用传入值（实时数据）。
    目标高度 = 初态平均高度 + 40 km —— 使 55% 故障造成的首脉冲误差（≈18 km）
    超过 5 km 验收容差，从而**真实**触发「验证→重规划」自愈（区别于 430 km 那种
    落在容差内、不会触发补脉冲的"假自愈"）。
    """
    from .tle import fetch_tle, satrec_state_at, FIXED_EPOCH
    from .kepler import rv_to_coe
    from .constants import R_EARTH
    if tle is None:
        tle = FIXED_ISS_TLE
    st = satrec_state_at(tle, FIXED_EPOCH)
    if "error" in st:
        return {"__error__": st["error"]}
    mean_alt = float(rv_to_coe(st["r_km"], st["v_kms"])["a_km"]) - R_EARTH
    return {
        "kind": "anomaly_response",
        "epoch_utc": FIXED_EPOCH,
        "target_altitude_km": round(mean_alt + FLAGSHIP_RAISE_KM, 3),
        "desired_miss_km": FLAGSHIP_DESIRED_MISS_KM,
        "initial_fuel_kg": 50.0,
        "inject_fault": FLAGSHIP_FAULT,
        "tle": tle,
    }


def render_flagship_svg(out_path=None, tle=None):
    """跑通旗舰场景并渲染规范主图，返回 (svg_path, record)。

    svg_path 为 None 时 record 内含 error（调用方可据此降级）。
    """
    from .mission import run_scenario
    scenario = build_flagship_scenario(tle)
    if "__error__" in scenario:
        return None, scenario
    rec = run_scenario(scenario)
    if isinstance(rec, dict) and rec.get("error"):
        return None, rec
    return render_orbit_svg(rec, out_path), rec


def render_scene_svgs(tle=None):
    """一键生成三类场景 SVG，保证产物齐全、互不覆盖：
      - 规范主图 output/orbit_figure.svg（旗舰 anomaly_response）
      - output/orbit_figure_raise.svg（纯抬升 +40 km）
      - output/orbit_figure_collision.svg（碰撞规避）
    返回已生成文件的路径列表。供 reproduce.py / package.py 复用，避免漏产物。
    """
    paths = []
    p, _rec = render_flagship_svg(out_path=None, tle=tle)
    if p:
        paths.append(p)
    # 函数内引入，避免与 server 的循环导入
    from .server import visualize_tool
    for sc in ({"kind": "raise", "target_altitude_km": 466.558, "initial_fuel_kg": 50},
               {"kind": "collision", "desired_miss_km": 50, "initial_fuel_kg": 50}):
        try:
            r = visualize_tool(dict(sc), tle)
            if isinstance(r, dict) and r.get("svg_path"):
                paths.append(r["svg_path"])
        except Exception:
            pass
    return paths
