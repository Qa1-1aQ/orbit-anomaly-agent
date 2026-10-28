"""跨平台中文字体最优回退配置（仅影响 matplotlib 出图显示，不影响任何数值）。

动机：演示用的 5 张仿真图标题含中文。原代码把 SimHei 写死成
``C:/Windows/Fonts/simhei.ttf``，在 WSL/Linux 上该路径不存在，导致中文标题
渲染为方框（tofu）。本模块按优先级自寻可用 CJK 字体：

  1. Windows 本机路径（原生 Windows 运行）；
  2. WSL 挂载的 Windows 字体（``/mnt/c/Windows/Fonts/``，在 WSL 里几乎必然存在）；
  3. 常见 Linux CJK 字体（Noto / WenQuanYi / Droid / AR PL UMing）；
  4. 已注册字体列表中名字含 CJK 关键字的任意字体；
  5. 以上皆无则保持 matplotlib 默认（此时中文可能为方框，但数据与图结构正确）。

调用方：``from tools.cjk_font import setup_cjk_font; setup_cjk_font()``
"""
import os

import matplotlib

# 候选字体路径（按系统常见布局排序，命中第一个即停）
_CANDIDATES = [
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/croscore/Arimo.ttf",
]

# 已注册字体名里的 CJK 关键字（用于第 4 步按名字匹配）
_NAME_KEYWORDS = ("CJK", "Noto Sans CJK", "WenQuanYi", "Hei", "SimHei",
                  "YaHei", "Source Han", "UMing", "Droid Sans Fallback")


def setup_cjk_font():
    """配置 matplotlib 使用中文字体；返回实际生效的字体名（未找到返回 None）。"""
    from matplotlib import font_manager as fm

    # 1) 文件级候选
    for path in _CANDIDATES:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                name = fm.FontProperties(fname=path).get_name()
                matplotlib.rcParams["font.family"] = name
                return name
            except Exception:
                continue

    # 2) 按已注册字体名匹配
    try:
        for f in fm.fontManager.ttflist:
            if any(k in f.name for k in _NAME_KEYWORDS):
                matplotlib.rcParams["font.family"] = f.name
                return f.name
    except Exception:
        pass

    return None
