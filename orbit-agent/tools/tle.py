"""TLE 获取与解析：优先在线拉取 Celestrak，失败则回退内置样例。

所有对外函数返回可 JSON 序列化的 dict，方便 AGH / MCP 直接调用。
"""
import os
import time as _time
import requests

# 会话级 TLE 缓存：第一次拉取做真实联网（证明连通性），之后同 (target,group)
# 在 TTL 内直接复用，避免 AGH 反复调用 get_tle 时每次都付镜像的偶发慢响应（可达 30s+）。
# 仅缓存“真实联网成功”的结果；联网失败回退的内置样例绝不进缓存（否则会掩盖降级）。
_TLE_CACHE = {}
_TLE_CACHE_TTL = 6 * 3600  # 6 小时


def _cache_key(target, group):
    return (str(target), str(group))


def _cache_get(target, group):
    v = _TLE_CACHE.get(_cache_key(target, group))
    if v and (_time.time() - v[0]) < _TLE_CACHE_TTL:
        d = dict(v[1])
        d["cached"] = True
        return d
    return None


def _cache_put(target, group, result):
    _TLE_CACHE[_cache_key(target, group)] = (_time.time(), dict(result))


def clear_tle_cache():
    """清空会话缓存（测试/复现时用于强制一次全新联网拉取）。"""
    _TLE_CACHE.clear()


def _now_iso():
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())


# 全局固定历元：所有“当前状态/场景初值”都钉在此刻，
# 与 simulate 实验、analyze 的 _base_state 使用同一初始态，保证数字一致且可复现。
# 该时刻恰好为内置样例 TLE（ISS, NORAD 25544）的历元（2026 年第 283.5 天 = 2026-10-10T12:00:00Z）。
FIXED_EPOCH = "2026-10-10T12:00:00.000"

# 固定历元的真实 ISS TLE 快照（Celestrak 2026-10-10T18:56:31，NORAD 25544）。
# ★ 全系统唯一权威 ISS 轨道根数来源 ★：旗舰闭环（viz.build_flagship_scenario）、
# 压力测试（stress_test）、仿真实验（simulate）、任务编排（mission）、图表生成
# （make_figures）以及离线回退（_get_builtin）全部引用它，杜绝“一处 424.2、一处 426.558”
# 的基线分裂。固定历元 2026-10-10T12:00:00 下初态平均高度 ≈ 426.558 km。
FIXED_ISS_TLE = {
    "name": "ISS (ZARYA)",
    "norad_id": 25544,
    "line1": "1 25544U 98067A   26283.78925095  .00010839  00000+0  20582-3 0  9996",
    "line2": "2 25544  51.6307  88.0087 0006720 246.7167 113.3114 15.48817123589672",
    "source": "celestrak-pinned-20261010T185631",
    "epoch_utc": "2026-10-10T18:56:31.282",
}

# 内置样例 TLE（离线回退用）。ISS 直接复用 FIXED_ISS_TLE（保证全系统口径一致），
# DEBRIS-DEMO 为演示碎片目标（共轨道，用于碰撞规避场景）。
_SAMPLE_TLES = {
    "ISS (ZARYA)": FIXED_ISS_TLE,
    "DEBRIS-DEMO": {
        "name": "DEBRIS-DEMO",
        "norad_id": 99999,
        "line1": "1 99999U 98067A   26283.50000000  .00016717  00000-0  30074-3 0  9998",
        "line2": "2 99999  51.6410 208.2000 0009000  70.0000 290.0000 15.49600000 10000",
    },
}

# 常用目标名 -> NORAD ID（优先用 CATNR 精确拉取，避免整组下载后解析，省时且绕开 GFW 慢链路）
_NAME_TO_NORAD = {
    "ISS": "25544",
    "ISS (ZARYA)": "25544",
    "CSS": "48274",            # 天宫
    "TIANGONG": "48274",
    "HST": "20580",            # 哈勃
    "HUBBLE": "20580",
}

# Celestrak 对默认 python-requests UA 有时会限流；带合规 UA 更稳。
_HEADERS = {
    "User-Agent": "orbit-anomaly-agent/1.0 (educational; +https://celestrak.org)",
    "Accept": "text/plain",
}

# 镜像源回退链（Celestrak 官方对部分出口 IP 直接返回 403 时的备用真实数据源）。
# 每项：(标签, URL 模板, 解析方式)。均为公开、无需鉴权的实时 TLE 服务。
_MIRROR_SOURCES = [
    # ivanstanojevic 的 TLE API（Celestrak 数据镜像，返回 JSON: line1/line2）
    ("celestrak-mirror:ivanstanojevic",
     "https://tle.ivanstanojevic.me/api/tle/{norad}", "json"),
]


def _http_get(url, timeout, tries=3):
    """带重试的 GET，返回 (response_or_None, last_error_or_None)。"""
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, timeout=timeout, headers=_HEADERS)
            if r.ok and r.text.strip():
                return r, None
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return None, last


def _fetch_from_mirrors(norad, timeout, tries=2):
    """按镜像源回退链尝试拉取真实 TLE。成功返回 dict（source=镜像标签），全失败返回 (None, 错误)。

    仅用于已知 NORAD ID 的精确查询；返回真实数据，**绝不**伪造——
    所有镜像均失败时返回 None，由上层继续回退到内置样例并如实标注。

    每个镜像内部做 `tries` 次重试（单次超时 timeout）：本机到公开镜像偶发
    出现单次慢响应（>10s），重试可避免“一次抖动就静默降级到内置样例”
    这种最容易被误判为‘系统没联网’的失败模式。
    """
    last = None
    for label, tpl, kind in _MIRROR_SOURCES:
        url = tpl.format(norad=norad)
        for _ in range(tries):
            try:
                r = requests.get(url, timeout=timeout, headers=_HEADERS)
                if not r.ok:
                    last = f"{label} HTTP {r.status_code}"
                    continue
                if kind == "json":
                    j = r.json()
                    l1, l2 = j.get("line1"), j.get("line2")
                    if not (l1 and l2):
                        last = f"{label} 返回体缺 line1/line2"
                        continue
                    l1s, l2s = l1.strip(), l2.strip()
                    return {
                        "name": j.get("name") or f"NORAD {norad}",
                        "norad_id": _norad_id_from_line1(l1s),
                        "line1": l1s,
                        "line2": l2s,
                        "source": label,
                        "epoch_utc": _tle_epoch_iso(l1s),
                    }, None
                else:
                    name, l1, l2 = _parse_tle_block(r.text)
                    if not (l1 and l2):
                        last = f"{label} 返回体无法解析为 TLE"
                        continue
                    return {
                        "name": name or f"NORAD {norad}",
                        "norad_id": _norad_id_from_line1(l1),
                        "line1": l1, "line2": l2,
                        "source": label,
                        "epoch_utc": _tle_epoch_iso(l1),
                    }, None
            except Exception as e:
                last = f"{label} {type(e).__name__}: {e}"
        # 该镜像 tries 次均失败，继续尝试下一个镜像（若有）
    return None, last


def _parse_tle_block(text: str):
    """从 Celestrak 返回体解析 (name, line1, line2)。

    Celestrak FORMAT=tle 两种格式都可能出现：
      - 带名称：  "NAME\\n1 ...\\n2 ..."
      - 仅两行：  "1 ...\\n2 ..."
    TLE 第 1 行第 2 字段为 NORAD 号，可能带分类后缀字母（如 25544U），
    必须用切片 [2:7] 提取，不能直接 int(split()[1])（会 ValueError）。
    返回 (name_or_None, line1_or_None, line2_or_None)。
    """
    ls = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(ls) >= 3 and ls[1].startswith("1 ") and ls[2].startswith("2 "):
        return ls[0], ls[1], ls[2]
    if len(ls) >= 2 and ls[0].startswith("1 ") and ls[1].startswith("2 "):
        return None, ls[0], ls[1]
    return None, None, None


def _norad_id_from_line1(line1: str):
    """从 TLE 第 1 行第 2 字段（列 3-7）提取 NORAD 号，去掉可能的 U/C 后缀。"""
    raw = line1[2:7].strip()
    return int("".join(ch for ch in raw if ch.isdigit()) or 0)


def _tle_epoch_iso(line1: str):
    """从 TLE 第 1 行解析历元（0-indexed [18:32]，格式 YYDDD.DDDDDDDD）→ ISO UTC 字符串。

    直接回传给模型，避免其自行误读（如把 26283.789 错读成 9-28）。解析失败返回 None。
    """
    try:
        import datetime as dt
        field = line1[18:32].strip()          # 例如 "26283.78925095"
        yy = int(field[:2])
        doy = float(field[2:])
        year = 1900 + yy if yy >= 57 else 2000 + yy
        base = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        t = base + dt.timedelta(days=doy - 1.0)
        return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    except Exception:
        return None


def fetch_tle(target: str, group: str = "stations", timeout: float = 15.0,
              force_live: bool = False) -> dict:
    """从 Celestrak 拉取指定目标的 TLE。

    Args:
        target: 卫星名称（如 "ISS (ZARYA)"）或 NORAD ID 字符串。
        group:  Celestrak 分组，默认 stations；也可传自定义分组。
        timeout: 单次网络超时（秒），内部会自动重试 3 次。
        force_live: True 时跳过会话缓存、强制一次全新联网拉取（用于诚实证明“此刻真能联网”）。

    Returns:
        {name, norad_id, line1, line2, source, fetch_error?}
        source 为 "celestrak" 表示实时拉取成功；为 "builtin_sample" 表示联网失败已回退，
        并附 fetch_error 说明失败原因（DNS/超时/SSL/限流），便于诊断而非静默吞掉。
    """
    last_error = None

    # 会话缓存：非强制情况下，命中且未过期则直接返回真实联网结果（标记 cached=True）。
    if not force_live:
        hit = _cache_get(target, group)
        if hit is not None:
            return hit

    # 0) NORAD ID 直查（最快、最稳，响应体仅 3 行，不下载整组）
    norad = str(target).strip() if str(target).isdigit() else _NAME_TO_NORAD.get(str(target).strip().upper())
    if norad:
        try:
            url0 = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=tle"
            r0, err0 = _http_get(url0, timeout)
            if r0 is not None:
                name, l1, l2 = _parse_tle_block(r0.text)
                if l1 and l2:
                    res = {"name": name or f"NORAD {norad}", "norad_id": _norad_id_from_line1(l1),
                           "line1": l1, "line2": l2, "source": "celestrak",
                           "epoch_utc": _tle_epoch_iso(l1), "fetched_at": _now_iso()}
                    _cache_put(target, group, res)
                    return res
            last_error = err0 or "Celestrak 返回内容无法解析为 TLE"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"

    # 0.5) 镜像源回退：Celestrak 官方对部分出口 IP 返回 403（IIS 拦截）时，
    #      改走公开镜像拉取**同样真实**的 TLE（source 标为镜像标签，仍属联机真实数据）。
    if norad:
        # 镜像单次超时收紧到 10s 并内部重试 2 次：既避免一次慢响应把总耗时拖到 30s+，
        # 又能在偶发抖动时靠重试拿到真实数据（不静默降级到内置样例）。
        mir, mir_err = _fetch_from_mirrors(norad, min(timeout, 10.0), tries=2)
        if mir is not None:
            mir["fetched_at"] = _now_iso()
            _cache_put(target, group, mir)
            return mir
        if mir_err:
            last_error = f"官方:{last_error} | 镜像:{mir_err}" if last_error else mir_err

    # 1) 回退：按名称/ID 在指定分组在线查询（整组下载后匹配）
    try:
        url = f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
        resp, err = _http_get(url, timeout)
        if resp is not None:
            lines = [ln.strip() for ln in resp.text.strip().splitlines() if ln.strip()]
            for i in range(0, len(lines) - 1, 3):
                name = lines[i]
                l1, l2 = lines[i + 1], lines[i + 2]
                if name.upper() == str(target).upper() or _norad_id_from_line1(l1) == int(norad or -1):
                    res = {
                        "name": name,
                        "norad_id": _norad_id_from_line1(l1),
                        "line1": l1,
                        "line2": l2,
                        "source": "celestrak",
                        "epoch_utc": _tle_epoch_iso(l1),
                        "fetched_at": _now_iso(),
                    }
                    _cache_put(target, group, res)
                    return res
        last_error = err or last_error
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"

    # 2) 回退：若 target 是内置样例名
    if target in _SAMPLE_TLES:
        d = dict(_SAMPLE_TLES[target])
        d["source"] = "builtin_sample"
        d["fetch_error"] = last_error
        d["epoch_utc"] = _tle_epoch_iso(d["line1"])
        return d

    # 3) 实在找不到，返回默认 ISS 样例并标注
    d = dict(_SAMPLE_TLES["ISS (ZARYA)"])
    d["source"] = "builtin_sample"
    d["warning"] = f"未找到 '{target}'，已回退至 ISS 样例 TLE"
    d["fetch_error"] = last_error
    d["epoch_utc"] = _tle_epoch_iso(d["line1"])
    return d


def tle_to_satrec(tle: dict):
    """把 TLE dict 转成 sgp4 的 Satrec 对象。"""
    from sgp4.api import Satrec
    return Satrec.twoline2rv(tle["line1"], tle["line2"])


def satrec_state_at(tle: dict, epoch_utc: str):
    """给定 TLE 与 UTC 时间，返回该时刻状态（TEME 系，km / km/s）。

    Returns:
        {epoch_utc, r_km:[x,y,z], v_kms:[vx,vy,vz], frame:"TEME", error?}
    """
    from sgp4.api import jday
    from astropy.time import Time
    import numpy as np
    try:
        sat = tle_to_satrec(tle)
        t = Time(epoch_utc, format="isot", scale="utc")
        jd, fr = jday(t.datetime.year, t.datetime.month, t.datetime.day,
                      t.datetime.hour, t.datetime.minute, t.datetime.second + t.datetime.microsecond * 1e-6)
        err, r, v = sat.sgp4(jd, fr)
        if err != 0:
            return {"error": f"sgp4 error code {err}"}
        r = np.asarray(r, float)
        v = np.asarray(v, float)
        return {
            "epoch_utc": epoch_utc,
            "r_km": r.tolist(),
            "v_kms": v.tolist(),
            "frame": "TEME",
        }
    except Exception as e:
        return {"error": str(e)}
