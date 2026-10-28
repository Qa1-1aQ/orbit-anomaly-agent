"""生成「异常处置自主闭环（FDIR）」流程图 SVG（纯 Python，无第三方依赖）。

该图用于演示视频与提交材料，直观展示作品的智能体闭环：
  输入 → 异常感知 → 诊断 → 规划 → 执行 → 验证 → (PASS 输出 / FAIL 重规划自愈)
  + 燃料/数据异常的优雅降级分支。
风格与 viz.py 一致（深色主题），可直接打开或截图。
"""
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _box(x, y, w, h, fill, stroke, text, tcolor="#e6ecff", fs=13, sub=None):
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" ry="9" '
         f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
    cx = x + w / 2
    cy = y + h / 2
    s += (f'<text x="{cx}" y="{cy + (6 if sub else 0)}" text-anchor="middle" '
          f'font-size="{fs}" font-weight="bold" fill="{tcolor}">{text}</text>')
    if sub:
        s += (f'<text x="{cx}" y="{cy + 22}" text-anchor="middle" '
              f'font-size="10" fill="#9fb4ff">{sub}</text>')
    return s


def _arrow(x1, y1, x2, y2, color="#6c7fb0", dash=False):
    # 水平/竖直箭头（简单直线 + 箭头）
    marker = f"m{abs(hash(color))%10000}"
    s = (f'<defs><marker id="{marker}" markerWidth="8" markerHeight="8" '
         f'refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="{color}"/></marker></defs>')
    dashattr = ' stroke-dasharray="5 4"' if dash else ''
    s += (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
          f'stroke-width="2.0" marker-end="url(#{marker})"{dashattr}/>')
    return s


def render_flow_diagram(out_path=None):
    if out_path is None:
        out_path = os.path.join(_PROJECT_ROOT, "output", "flow_diagram.svg")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    W, H = 920, 600
    C_INPUT = "#1f3a6b"
    C_SENSE = "#244a7a"
    C_PLAN = "#2b6c5a"
    C_EXEC = "#6b5a1f"
    C_VER = "#5a2b6b"
    C_OK = "#1f6b3a"
    C_WARN = "#8a5a14"
    C_LINE = "#6c7fb0"

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif">',
             f'<rect width="{W}" height="{H}" fill="#0f1830"/>',
             f'<text x="20" y="30" fill="#e6ecff" font-size="17" font-weight="bold">航天器轨道异常处置 · 自主闭环（AGH + Agnes，FDIR）</text>',
             f'<text x="20" y="50" fill="#8aa0d8" font-size="11">检测 → 诊断 → 规划 → 执行 → 验证 →（通过则输出 / 失败则重规划自愈）；燃料·数据异常优雅降级</text>']

    # 主链路（顶部一排）
    y0 = 90
    bw, bh, gap = 150, 64, 26
    x = 40
    nodes = [
        (x, "输入\nTLE/遥测", "SGP4+二体状态", C_INPUT),
    ]
    # 输入
    parts.append(_box(x, y0, bw, bh, C_INPUT, "#3f6fd0", "输入 TLE/遥测", sub="SGP4 + 二体状态"))
    x2 = x + bw + gap
    parts.append(_box(x2, y0, bw, bh, C_SENSE, "#3f6fd0", "异常感知", sub="衰减(B-star)/碰撞(接近)"))
    x3 = x2 + bw + gap
    parts.append(_box(x3, y0, bw, bh, "#3a3f7a", "#7a7fd0", "诊断与优先级", sub="衰减先 / 碰撞后"))
    x4 = x3 + bw + gap
    parts.append(_box(x4, y0, bw, bh, C_PLAN, "#2f9b7a", "机动规划", sub="单脉冲抬升/规避搜索"))
    x5 = x4 + bw + gap
    parts.append(_box(x5, y0, bw, bh, C_EXEC, "#b5902f", "脉冲执行", sub="apply_maneuver"))
    x6 = x5 + bw + gap
    parts.append(_box(x6, y0, bw, bh, C_VER, "#a04fd0", "闭环验证", sub="量化指标 vs 阈值"))

    # 箭头：主链路
    ay = y0 + bh / 2
    for xa, xb in [(x + bw, x2), (x2 + bw, x3), (x3 + bw, x4), (x4 + bw, x5), (x5 + bw, x6)]:
        parts.append(_arrow(xa + 2, ay, xb - 2, ay, C_LINE))

    # 验证 → 判定菱形
    dx, dy, dsize = x6 + 95, y0 + 6, 92
    # 用菱形近似：polygon
    dpts = f"{dx+dsize/2},{dy} {dx+dsize},{dy+dsize/2} {dx+dsize/2},{dy+dsize} {dx},{dy+dsize/2}"
    parts.append(f'<polygon points="{dpts}" fill="#2a2f5a" stroke="#a04fd0" stroke-width="1.6"/>')
    parts.append(f'<text x="{dx+dsize/2}" y="{dy+dsize/2-2}" text-anchor="middle" font-size="13" font-weight="bold" fill="#e6ecff">PASS?</text>')
    parts.append(f'<text x="{dx+dsize/2}" y="{dy+dsize/2+14}" text-anchor="middle" font-size="10" fill="#9fb4ff">验收阈值</text>')
    parts.append(_arrow(x6 + bw, ay, dx - 2, dy + dsize / 2, C_LINE))

    # PASS → 输出（右侧绿框）
    oy = 230
    ow, oh = 230, 70
    ox = 620
    parts.append(_box(ox, oy, ow, oh, C_OK, "#36d39a", "输出决策链路 + 证据", sub="decision_trace / overall_passed", fs=13))
    # 菱形右 → 输出
    parts.append(_arrow(dx + dsize, dy + dsize / 2, ox - 2, oy + oh / 2, "#36d39a"))

    # FAIL → 重规划自愈（下方）
    ry = 300
    rw, rh = bw, bh
    rx = x4 - 10
    parts.append(_box(rx, ry, rw, rh, C_WARN, "#d69a2f", "故障隔离与重规划", sub="FDIR 自愈（二次脉冲）", fs=12))
    # 菱形下 → 重规划
    parts.append(_arrow(dx + dsize / 2, dy + dsize, rx + rw / 2, ry - 2, "#d69a2f", dash=True))
    # 重规划 → 回到执行（向上连到执行节点底部）
    parts.append(_arrow(rx + rw / 2, ry - 2, rx + rw / 2, y0 + bh + 2, "#d69a2f", dash=True))
    # 标注
    parts.append(f'<text x="{rx + rw/2}" y="{ry - 10}" text-anchor="middle" font-size="10" fill="#d69a2f">FAIL → 自主重规划</text>')

    # 优雅降级分支（左下，从感知/验证引出）
    gy = 430
    gw, gh = 300, 64
    gx = 40
    parts.append(_box(gx, gy, gw, gh, "#5a3a14", "#d6a04f", "优雅降级", sub="燃料不足→拒止 / 数据失败→回退样例", fs=12))
    parts.append(_arrow(x2 + bw / 2, y0 + bh, gx + 60, gy - 2, "#d6a04f", dash=True))
    parts.append(f'<text x="{x2+bw/2-6}" y="{gy-12}" text-anchor="middle" font-size="10" fill="#d6a04f">异常分支</text>')
    # 降级 → 输出（弱连接）
    parts.append(_arrow(gx + gw, gy + gh / 2, ox - 2, oy + oh + 18, "#d6a04f", dash=True))

    # 图例
    lx, ly = 380, 560
    parts.append(f'<rect x="{lx}" y="{ly}" width="14" height="14" rx="3" fill="{C_OK}" stroke="#36d39a"/>')
    parts.append(f'<text x="{lx+20}" y="{ly+12}" fill="#cfe0ff" font-size="11">通过</text>')
    parts.append(f'<rect x="{lx+90}" y="{ly}" width="14" height="14" rx="3" fill="{C_WARN}" stroke="#d69a2f"/>')
    parts.append(f'<text x="{lx+110}" y="{ly+12}" fill="#cfe0ff" font-size="11">自愈/降级（虚线）</text>')

    parts.append("</svg>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    # 返回解析后的真实路径（跟随软链），避免 WSL 软链场景下打印误导性逻辑路径
    return os.path.realpath(out_path)


if __name__ == "__main__":
    print(render_flow_diagram())
