"""纯 numpy/astropy 实现的二体轨道力学：状态<->轨道根数、通用变量传播。

不依赖 poliastro 的传播接口，避免版本差异；结果可控、可复现，便于验证。
"""
import numpy as np
from astropy.time import Time

from .constants import MU_EARTH, R_EARTH


def _norm(v):
    return float(np.linalg.norm(v))


def rv_to_coe(r, v):
    """笛卡尔状态 (km, km/s) -> 经典轨道根数。"""
    r = np.asarray(r, float)
    v = np.asarray(v, float)
    rmag = _norm(r)
    vmag = _norm(v)
    h_vec = np.cross(r, v)
    hmag = _norm(h_vec)
    n_vec = np.cross([0, 0, 1], h_vec)
    nmag = _norm(n_vec)
    e_vec = (np.cross(v, h_vec) / MU_EARTH) - (r / rmag)
    e = _norm(e_vec)
    energy = vmag ** 2 / 2 - MU_EARTH / rmag
    a = -MU_EARTH / (2 * energy)
    i = np.arccos(np.clip(h_vec[2] / hmag, -1, 1))
    if nmag > 1e-9:
        raan = np.arccos(np.clip(n_vec[0] / nmag, -1, 1))
        if n_vec[1] < 0:
            raan = 2 * np.pi - raan
    else:
        raan = 0.0
    if e > 1e-9:
        argp = np.arccos(np.clip(np.dot(n_vec, e_vec) / (nmag * e), -1, 1))
        if e_vec[2] < 0:
            argp = 2 * np.pi - argp
    else:
        argp = 0.0
    if e > 1e-9:
        nu = np.arccos(np.clip(np.dot(e_vec, r) / (e * rmag), -1, 1))
        if np.dot(r, v) < 0:
            nu = 2 * np.pi - nu
    else:
        nu = 0.0
    period = 2 * np.pi * np.sqrt(a ** 3 / MU_EARTH) if a > 0 else None
    return {
        "a_km": float(a),
        "e": float(e),
        "i_deg": float(np.degrees(i)),
        "raan_deg": float(np.degrees(raan)),
        "argp_deg": float(np.degrees(argp)),
        "nu_deg": float(np.degrees(nu)),
        "period_min": float(period / 60.0) if period else None,
        "altitude_km": float(rmag - R_EARTH),
    }


def coe_to_rv(a, e, i_deg, raan_deg, argp_deg, nu_deg):
    """经典轨道根数 -> 笛卡尔状态 (km, km/s)。"""
    i = np.radians(i_deg)
    raan = np.radians(raan_deg)
    argp = np.radians(argp_deg)
    nu = np.radians(nu_deg)
    p = a * (1 - e ** 2)
    rmag = p / (1 + e * np.cos(nu))
    r_pqw = rmag * np.array([np.cos(nu), np.sin(nu), 0.0])
    v_pqw = np.sqrt(MU_EARTH / p) * np.array([-np.sin(nu), e + np.cos(nu), 0.0])
    R = np.array([
        [np.cos(raan) * np.cos(argp) - np.sin(raan) * np.sin(argp) * np.cos(i),
         -np.cos(raan) * np.sin(argp) - np.sin(raan) * np.cos(argp) * np.cos(i),
         np.sin(raan) * np.sin(i)],
        [np.sin(raan) * np.cos(argp) + np.cos(raan) * np.sin(argp) * np.cos(i),
         -np.sin(raan) * np.sin(argp) + np.cos(raan) * np.cos(argp) * np.cos(i),
         -np.cos(raan) * np.sin(i)],
        [np.sin(argp) * np.sin(i), np.cos(argp) * np.sin(i), np.cos(i)],
    ])
    r = R @ r_pqw
    v = R @ v_pqw
    return r.tolist(), v.tolist()


def _stumpff(psi):
    if psi > 1e-6:
        sq = np.sqrt(psi)
        s, c = np.sin(sq), np.cos(sq)
        return (1 - c) / psi, (sq - s) / (psi ** 1.5)
    elif psi < -1e-6:
        sq = np.sqrt(-psi)
        sh, ch = np.sinh(sq), np.cosh(sq)
        return (1 - ch) / (-psi), (sh - sq) / ((-psi) ** 1.5)
    return 0.5, 1.0 / 6.0


def propagate_rv(r, v, dt_seconds):
    """通用变量法二体传播 dt 秒。返回新的 (r, v) (km, km/s)。"""
    r = np.asarray(r, float)
    v = np.asarray(v, float)
    rmag = _norm(r)
    vmag = _norm(v)
    sqrtmu = np.sqrt(MU_EARTH)
    alpha = 2 / rmag - vmag ** 2 / MU_EARTH  # = 1/a
    rv = float(np.dot(r, v))

    if alpha > 1e-9:          # 椭圆
        chi = sqrtmu * dt_seconds * np.sqrt(alpha)
    elif abs(alpha) < 1e-9:   # 抛物线
        chi = sqrtmu * dt_seconds * 1e9
    else:                     # 双曲线
        chi = (np.sign(dt_seconds) * np.sqrt(-1 / alpha) *
               np.log((-2 * MU_EARTH * alpha * dt_seconds) /
                      (rv + np.sign(dt_seconds) * np.sqrt(-MU_EARTH / alpha) * (1 - rmag * alpha))))

    for _ in range(100):
        chi2 = chi * chi
        psi = alpha * chi2
        c2, c3 = _stumpff(psi)
        rnew = chi2 * c2 + (rv / sqrtmu) * chi * (1 - psi * c3) + rmag * (1 - psi * c2)
        dchi = (sqrtmu * dt_seconds - chi ** 3 * c3 - (rv / sqrtmu) * chi2 * c2 -
                rmag * chi * (1 - psi * c3)) / rnew
        if abs(dchi) < 1e-9:
            chi += dchi
            break
        chi += dchi

    chi2 = chi * chi
    psi = alpha * chi2
    c2, c3 = _stumpff(psi)
    f = 1 - (chi ** 2 / rmag) * c2
    g = dt_seconds - (chi ** 3 / sqrtmu) * c3
    r_vec = f * r + g * v
    rnew_mag = _norm(r_vec)
    gdot = 1 - (chi ** 2 / rnew_mag) * c2
    fdot = (sqrtmu / (rnew_mag * rmag)) * chi * (psi * c3 - 1)
    v_vec = fdot * r + gdot * v
    return r_vec.tolist(), v_vec.tolist()


def now_utc_isot():
    return Time.now().isot
