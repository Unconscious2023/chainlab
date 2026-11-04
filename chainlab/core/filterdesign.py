"""滤波器原型：Butterworth / Chebyshev I / Chebyshev II / Bessel。

输出 s 域零极点（推荐，数值稳定），另有 num/den 的兼容接口。
最高 8 阶（带通到 4 阶）。
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------- 原型
def _butter_poles(n: int) -> np.ndarray:
    k = np.arange(n)
    return np.exp(1j * np.pi * (2 * k + 1 + n) / (2 * n))


def _cheby1(n: int, ripple_db: float):
    eps = np.sqrt(10 ** (ripple_db / 10.0) - 1.0)
    a = np.arcsinh(1.0 / eps) / n
    k = np.arange(n)
    th = np.pi * (2 * k + 1) / (2 * n)
    poles = -np.sinh(a) * np.sin(th) + 1j * np.cosh(a) * np.cos(th)
    return poles, np.array([])


def _cheby2(n: int, atten_db: float):
    """逆切比雪夫：通带最平、阻带等波纹，带有限零点。"""
    eps = 1.0 / np.sqrt(max(10 ** (atten_db / 10.0) - 1.0, 1e-30))
    a = np.arcsinh(1.0 / eps) / n
    k = np.arange(n)
    th = np.pi * (2 * k + 1) / (2 * n)
    q = -np.sinh(a) * np.sin(th) + 1j * np.cosh(a) * np.cos(th)
    poles = 1.0 / q
    zeros = 1j / np.cos(th)
    zeros = zeros[np.abs(np.cos(th)) > 1e-9]
    return poles, zeros


def _bessel(n: int):
    """反向贝塞尔多项式（最平群延迟），归一化到 -3 dB 频率。"""
    coeffs = []
    for k in range(n + 1):
        a = _fact(2 * n - k)
        b = (2 ** (n - k)) * _fact(k) * _fact(n - k)
        coeffs.append(a / b)
    poles = np.roots(np.array(coeffs, dtype=float))
    return poles / _find_minus3db(poles), np.array([])


def _fact(k: int) -> float:
    r = 1.0
    for i in range(2, k + 1):
        r *= i
    return r


def _find_minus3db(poles):
    a0 = float(np.real(np.prod(-poles)))

    def mag(w):
        return abs(a0 / np.prod(1j * w - poles))

    lo, hi = 1e-4, 1e3
    for _ in range(200):
        mid = np.sqrt(lo * hi)
        if mag(mid) > 1 / np.sqrt(2):
            lo = mid
        else:
            hi = mid
    return float(np.sqrt(lo * hi))


def _proto(n: int, response: str, ripple_db: float):
    r = response.lower().replace(" ", "")
    if r.startswith("cheby2") or r.startswith("chebyshev2") or "inverse" in r:
        return _cheby2(n, ripple_db)
    if r.startswith("cheby") or r.startswith("chebyshev"):
        return _cheby1(n, ripple_db)
    if r.startswith("bessel"):
        return _bessel(n)
    return _butter_poles(n), np.array([])


# ---------------------------------------------------------------- 变换与求值
def pz_response(poles, zeros, k, f):
    s = 1j * 2 * np.pi * np.asarray(f, dtype=float)
    num = np.full(s.shape, complex(k))
    for z in zeros:
        num = num * (s - z)
    den = np.ones(s.shape, dtype=complex)
    for p in poles:
        den = den * (s - p)
    return num / den


def design_pz(ftype: str, order: int, fc: float, response: str = "butterworth",
              ripple_db: float = 0.5, gain: float = 1.0, q: float = 0.7071,
              f_lo: float | None = None, f_hi: float | None = None,
              atten_db: float | None = None):
    """返回 (poles, zeros, k)，其中 H(s) = k · Π(s−z) / Π(s−p)。"""
    ftype = ftype.lower()
    n = int(order)
    if n < 1 or n > 8:
        raise ValueError(f"阶数必须在 1~8，收到 {n}")

    if ftype == "bandpass" and f_lo and f_hi:
        f_lo, f_hi = float(f_lo), float(f_hi)
        if f_hi <= f_lo:
            raise ValueError("上限截止频率必须大于下限截止频率")
        fc = np.sqrt(f_lo * f_hi)
        q = fc / (f_hi - f_lo)

    is_cheb2 = str(response).lower().replace(" ", "").startswith(("cheby2", "chebyshev2"))
    poles0, zeros0 = _proto(n, response, atten_db if (is_cheb2 and atten_db) else ripple_db)
    wc = 2 * np.pi * fc

    if ftype == "lowpass":
        poles, zeros = wc * poles0, wc * zeros0
    elif ftype == "highpass":
        poles = wc / poles0
        zeros = np.concatenate([wc / zeros0, np.zeros(n - zeros0.size)]) if zeros0.size \
            else np.zeros(n)
    elif ftype == "bandpass":
        if n > 4:
            raise ValueError("带通最高 4 阶（极点数翻倍）")
        w0 = wc
        bw = w0 / max(q, 1e-6)
        poles = _bp_roots(poles0, w0, bw)
        zeros = np.concatenate([_bp_roots(zeros0, w0, bw), np.zeros(n - zeros0.size)]) \
            if zeros0.size else np.zeros(n)
    else:
        raise ValueError(f"未知滤波器类型：{ftype}")

    # 归一化：低通看 DC，带通看中心，高通看无穷远
    if ftype == "highpass":
        k = float(np.real(gain))
    elif ftype == "bandpass":
        h = abs(pz_response(poles, zeros, 1.0, [fc])[0])
        k = gain / h if h > 0 else gain
    else:
        h = abs(pz_response(poles, zeros, 1.0, [0.0])[0])
        k = gain / h if h > 0 else gain
    return np.array(poles), np.array(zeros), float(k)


def design(ftype: str, order: int, fc: float, response: str = "butterworth",
           ripple_db: float = 0.5, gain: float = 1.0, q: float = 0.7071,
           f_lo: float | None = None, f_hi: float | None = None,
           atten_db: float | None = None):
    """兼容接口：返回 (num, den)。高阶建议改用 design_pz。"""
    poles, zeros, k = design_pz(ftype, order, fc, response, ripple_db, gain, q,
                                f_lo, f_hi, atten_db)
    num = np.real(np.poly(zeros)) * k if zeros.size else np.array([k])
    return num.tolist(), np.real(np.poly(poles)).tolist()


def _bp_roots(roots: np.ndarray, w0: float, bw: float) -> np.ndarray:
    out = []
    for r in roots:
        disc = np.sqrt((bw * r) ** 2 - 4 * w0 ** 2 + 0j)
        out.append((bw * r + disc) / 2)
        out.append((bw * r - disc) / 2)
    return np.array(out)


def response(fc: float, num, den, f):
    s = 1j * 2 * np.pi * np.asarray(f, dtype=float)
    return np.polyval(num, s) / np.polyval(den, s)

# ---------------------------------------------------------------- 元件值
SERIES = {
    "E6":  [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
            3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
    "E48": [1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54,
            1.62, 1.69, 1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49,
            2.61, 2.74, 2.87, 3.01, 3.16, 3.32, 3.48, 3.65, 3.83, 4.02,
            4.22, 4.42, 4.64, 4.87, 5.11, 5.36, 5.62, 5.90, 6.19, 6.49,
            6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53],
    "E96": [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24,
            1.27, 1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58,
            1.62, 1.65, 1.69, 1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00,
            2.05, 2.10, 2.15, 2.21, 2.26, 2.32, 2.37, 2.43, 2.49, 2.55,
            2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09, 3.16, 3.24,
            3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
            4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23,
            5.36, 5.49, 5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65,
            6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45,
            8.66, 8.87, 9.09, 9.31, 9.53, 9.76],
}
E24 = SERIES["E24"]


def snap(x: float, series: str = "E24") -> float:
    """把数值吸附到最近的 E 系列标准值；series 为空或 "none" 时不吸附。"""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    if x <= 0:
        return x
    key = (series or "E24").upper()
    if key in ("NONE", "无", "不吸附", ""):
        return x
    vals = SERIES.get(key, SERIES["E24"])
    decades = 10 ** np.floor(np.log10(x))
    mant = x / decades
    best = min(vals, key=lambda e: abs(e - mant))
    return best * decades


def snap_e24(x: float) -> float:
    return snap(x, "E24")


def components(ftype: str, order: int, fc: float, q: float = 0.7071,
               c_hint: float = 10e-9, series: str = "E24") -> dict:
    from .block import num as _num

    c_hint = _num(c_hint) or 10e-9
    """给二阶 Sallen-Key / 一阶 RC 的元件值，并回算实际 fc、Q。

    只覆盖最常用的两种拓扑；三阶以上请级联。
    """
    ftype = ftype.lower()
    q = max(float(q), 0.5)
    out = {"拓扑": "Sallen-Key（单位增益）" if order >= 2 else "一阶 RC"}

    if order <= 1:
        c = snap(c_hint, series)
        r = snap_e24(1.0 / (2 * np.pi * fc * c))
        out.update({"C": c, "R": r,
                    "实际 fc": 1.0 / (2 * np.pi * r * c), "实际 Q": 0.5})
        return out

    if ftype == "lowpass":
        c2 = snap(c_hint, series)
        c1 = snap(4.0 * q ** 2 * c2, series)
        r = snap(1.0 / (2 * np.pi * fc * np.sqrt(c1 * c2)), series)
        f0 = 1.0 / (2 * np.pi * r * np.sqrt(c1 * c2))
        qq = 0.5 * np.sqrt(c1 / c2)
        out.update({"R1": r, "R2": r, "C1": c1, "C2": c2,
                    "实际 fc": f0, "实际 Q": qq})
    elif ftype == "highpass":
        r2 = snap(1e5, series)
        r1 = snap(4.0 * q ** 2 * r2, series)
        c = snap(1.0 / (2 * np.pi * fc * np.sqrt(r1 * r2)), series)
        f0 = 1.0 / (2 * np.pi * c * np.sqrt(r1 * r2))
        qq = 0.5 * np.sqrt(r1 / r2)
        out.update({"C1": c, "C2": c, "R1": r1, "R2": r2,
                    "实际 fc": f0, "实际 Q": qq})
    else:
        out["说明"] = "带通请用二阶 Sallen-Key 或状态变量拓扑，暂未生成"
    return out

def _f3_over_f0(q: float) -> float:
    """二阶低通的 -3dB 频率 / f0。"""
    k = 1.0 - 1.0 / (2.0 * q * q)
    return float(np.sqrt(k + np.sqrt(k * k + 1.0)))


def mfb_lowpass(f_minus3db: float, q: float, av: float = 2.0,
                r2: float = 1000.0, series: str = "E24") -> dict:
    from .block import num as _num

    c_hint = 10e-9
    r2 = _num(r2) or 1000.0
    """二阶 MFB 低通（反相多反馈），按 TI SBOA588 式 (2)~(5)。

    注意：这里的 f 是 **-3dB 频率**，不是 ω0；两者差一个只跟 Q 有关的系数。
    所有公式已用 TI 表 1 的三行数据（500k/5M/20M，Q=1，Av=2）校验过。
    """
    q = float(q)
    av = max(float(av), 1.0)
    f0 = f_minus3db / _f3_over_f0(q)
    w0 = 2 * np.pi * f0

    c2 = 1.0 / (w0 * r2 * q * (2 * av + 1.0))            # (2)

    def q_of(c1):                                         # 由 C1 反算实际 Q
        r3 = 1.0 / (av * w0 * w0 * r2 * c1 * c2)          # (4)
        r1 = r3 * av                                      # (5)
        return w0 / ((1.0 / c1) * (1.0 / r1 + 1.0 / r2 + 1.0 / r3)), r1, r3

    # C1 用数值解（式 3 的闭式在扫描版里糊了，这里直接解 Q）
    best = None
    for c1 in np.logspace(np.log10(c2 / 400.0), np.log10(c2 * 400.0), 4000):
        qq, r1, r3 = q_of(c1)
        d = abs(qq - q)
        if best is None or d < best[0]:
            best = (d, c1, r1, r3, qq)
    _, c1, r1, r3, qq = best

    return {"可行": True, "拓扑": "MFB（反相多反馈）",
            "R1": float(r1), "R2": float(r2), "R3": float(r3),
            "C1": float(c1), "C2": float(c2),
            "增益 K": -av, "f0": f0, "实际 fc": f_minus3db, "实际 Q": float(qq)}

def stage_qs(order: int, response: str = "butterworth",
             ripple_db: float = 0.5) -> list[dict]:
    """把 N 阶滤波器拆成级联的二阶节，返回每一节应有的 Q。

    奇数阶最后是一个一阶节（Q 记为 None）。
    这是教科书上的标准分配：4 阶巴特沃斯 = 1.307 / 0.541，5 阶 = 1.618 / 0.618。
    """
    n = int(order)
    if n < 1:
        return []
    if n == 1:
        return [{"级": 1, "阶": 1, "Q": None}]
    poles, _ = _proto(n, response, ripple_db)
    pairs = []
    for pole in poles:
        if np.imag(pole) > 1e-9:
            # 每个极点对有自己的 ω0 和 Q
            # 巴特沃斯：所有极点在同一圆上 → 各节 f0 相同，只有 Q 不同
            # 切比雪夫/贝塞尔：极点在椭圆/其他曲线 → 各节 f0 和 Q 都不同
            pairs.append({"ratio": float(abs(pole)),
                          "Q": float(abs(pole) / (2.0 * abs(np.real(pole))))})
    pairs.sort(key=lambda d: -d["Q"])
    stages = [{"级": i + 1, "阶": 2, "Q": p["Q"], "f0_ratio": p["ratio"]}
              for i, p in enumerate(pairs)]
    if n % 2 == 1:
        stages.append({"级": len(stages) + 1, "阶": 1, "Q": None, "f0_ratio": 1.0})
    return stages

def stages_components(order: int, fc: float, response: str = "butterworth",
                      ripple_db: float = 0.5, topo: str = "sk",
                      gain: float = 2.0, series: str = "E24",
                      ftype: str = "lowpass") -> list[dict]:
    """级联设计：每一节的 Q、f0 和该节元件值。

    ftype 决定用低通还是高通的拓扑（低通 R 串联 / 高通 C 串联）。
    topo='mfb' 时每级尝试 MFB，做不出来就退回 SK 并注明。
    """
    ftype = ftype.lower()
    seg = "高通" if ftype == "highpass" else "低通"
    out = []
    for st in stage_qs(order, response, ripple_db):
        q = st["Q"]
        f_stage = fc * st.get("f0_ratio", 1.0)
        item = {"级": st["级"], "阶": st["阶"], "Q": q, "fc": f_stage,
                "topo": "sk", "seg": seg}
        if st["阶"] == 1:
            d1 = components(ftype, 1, f_stage, series=series)
            if topo == "mfb":
                item["note"] = "一阶节没有 MFB 形式（MFB 是二阶拓扑）"
            item.update({"R1": d1.get("R"), "C1": d1.get("C"),
                         "R2": None, "C2": None,
                         "实际 fc": d1.get("实际 fc"), "实际 Q": d1.get("实际 Q")})
        elif topo == "mfb" and ftype in ("lowpass", "highpass"):
            d = (mfb_lowpass(f_stage, q, gain, series=series) if ftype == "lowpass"
                 else mfb_highpass(f_stage, q, 1.0, series=series))
            if d.get("可行") and d.get("R1"):
                item.update(d)
                item["topo"] = "mfb"
            else:
                item.update(components(ftype, 2, f_stage, q, series=series))
                item["note"] = "MFB 做不出来，已退回 SK"
        else:
            item.update(components(ftype, 2, f_stage, q, series=series))
            if topo == "mfb":
                item["note"] = "本工具暂未实现 MFB 高通，本级已用 Sallen-Key"
        out.append(item)
    return out


def bandpass_stages(f_lo: float, f_hi: float, order_total: int = 4,
                    topo: str = "sk", gain: float = 2.0,
                    series: str = "E24") -> list[dict]:
    """带通 = 高通段（f_lo）+ 低通段（f_hi）级联。

    order_total 是总阶数（2~8），按高低段平分；每段再拆成 1/2 阶节。
    """
    if f_hi <= f_lo:
        raise ValueError("上限截止频率必须大于下限截止频率")
    n = int(order_total)
    if n < 2 or n > 8:
        raise ValueError("带通总阶数必须在 2~8")

    hp_n = (n + 1) // 2
    lp_n = n - hp_n

    out = []
    for seg, cnt, fc in (("高通", hp_n, f_lo), ("低通", lp_n, f_hi)):
        if cnt <= 0:
            continue
        for st in stage_qs(cnt, "butterworth", 0.5):
            q = st["Q"]
            ftype = "highpass" if seg == "高通" else "lowpass"
            item = {"级": st["级"], "阶": st["阶"], "Q": q, "fc": fc,
                    "topo": "sk", "seg": seg}
            if st["阶"] == 1:
                d1 = components(ftype, 1, fc, series=series)
                if topo == "mfb":
                    item["note"] = "一阶节没有 MFB 形式（MFB 是二阶拓扑）"
                item.update({"R1": d1.get("R"), "C1": d1.get("C"),
                             "R2": None, "C2": None,
                             "实际 fc": d1.get("实际 fc"),
                             "实际 Q": d1.get("实际 Q")})
            elif topo == "mfb" and ftype in ("lowpass", "highpass"):
                d = (mfb_lowpass(fc, q, gain, series=series) if ftype == "lowpass"
                     else mfb_highpass(fc, q, 1.0, series=series))
                if d.get("可行") and d.get("R1"):
                    item.update(d)
                    item["topo"] = "mfb"
                else:
                    item.update(components(ftype, 2, fc, q, series=series))
                    item["note"] = "MFB 做不出来，已退回 SK"
            else:
                item.update(components(ftype, 2, fc, q, series=series))
                if topo == "mfb" and ftype == "highpass":
                    item["note"] = "本工具暂未实现 MFB 高通，本级已用 Sallen-Key"
            out.append(item)
    return out

def mfb_highpass(f_minus3db: float, q: float, av: float = 1.0,
                 c_hint: float = 1e-9, series: str = "E24") -> dict:
    """二阶 MFB 高通（反相），按 TI SBOA 手册。

    H(s) = −s²·(C1/C3) / [s² + s·(C1+C2+C3)/(R2·C2·C3) + 1/(R1·R2·C2·C3)]

    归一化设计（ωc=1, C1=C2=C3=1）：
        R1n·R2n = 1,  a1 = 3/R2n = 1/Q  =>  R1n = 1/(3Q), R2n = 3Q
    缩放：m = 1/(C·ω0)，R1 = R1n·m，R2 = R2n·m，C1=C2=C3=C。
    """
    q = max(float(q), 0.3)
    from .block import num as _num

    c_hint = _num(c_hint) or 1e-9
    f0 = f_minus3db / _f3_over_f0(q)
    w0 = 2 * np.pi * f0

    c = snap(c_hint, series)
    m = 1.0 / (c * w0)
    r1 = snap(m / (3.0 * q), series)
    r2 = snap(3.0 * q * m, series)

    # 用实际元件值回算 a0/a1
    a1 = (c + c + c) / (r2 * c * c)
    a0 = 1.0 / (r1 * r2 * c * c)
    w_act = float(np.sqrt(a0))
    q_act = float(w_act / a1)
    f3 = (w_act / (2 * np.pi)) * _f3_over_f0(q_act)

    return {"可行": True, "拓扑": "MFB 高通（反相多反馈）",
            "R1": float(r1), "R2": float(r2),
            "C1": float(c), "C2": float(c), "C3": float(c),
            "增益 K": -1.0, "f0": f0,
            "实际 fc": float(f3), "实际 Q": q_act}

# 注意：mfb_actual 只对 **MFB 低通** 成立（B2/B1/B0 由低通传函推出）。
# MFB 高通目前没有对应的 GBW 修正，调用方不要用它算高通。
def mfb_actual(f_minus3db: float, q_target: float, av: float = 2.0,
               r2: float = 1000.0, gbw: float = 50e6,
               aol: float = 1e6) -> dict:
    """把运放建成单极点模型，算 MFB 低通的「实际」Q 和 f0（TI 式 8~15）。"""
    d = mfb_lowpass(f_minus3db, q_target, av, r2)
    r1, r3, c1, c2 = d["R1"], d["R3"], d["C1"], d["C2"]
    wa = gbw * 2 * np.pi / aol

    rpar = r2 * r3 / (r2 + r3)
    B2 = (1.0 / (r2 * c2)
          + (1.0 + r1 / rpar) / (r2 * c1)
          + wa * (1.0 + aol))
    B1 = ((1.0 + r1 / r3) / (r1 * r2 * c1 * c2)
          + wa * (1.0 / (r2 * c2) + (aol + 1.0) / (r1 * c1) * (1.0 + r1 / rpar)))
    B0 = wa * (aol + 1.0 + r1 / r3) / (r1 * r2 * c1 * c2)

    roots = np.roots([1.0, B2, B1, B0])
    pair = [z for z in roots if abs(z.imag) > 1e-6]
    if not pair:
        return {"可行": False, "说明": "解不出复极点对"}
    z = max(pair, key=lambda x: abs(x.imag))
    a, b = float(z.real), float(abs(z.imag))
    w0_act = float(np.hypot(a, b))
    q_act = float(1.0 / (2.0 * abs(np.cos(np.arctan2(b, a)))))

    f0_ideal = f_minus3db / _f3_over_f0(q_target)
    # -3dB 直接由完整三次传函数值求解：非理想运放多了一个极点，
    # 只靠复极点对的 Q 折算会偏，必须把三个极点一起考虑。
    G = aol * wa / (c1 * c2 * r2 * r3)
    wgrid = np.logspace(np.log10(w0_act / 100), np.log10(w0_act * 100), 8000)
    sgrid = 1j * wgrid
    mag = np.abs(G / (sgrid ** 3 + B2 * sgrid ** 2 + B1 * sgrid + B0))
    ref = np.abs(G / B0)                      # 直流增益
    idx = np.where(mag <= ref / np.sqrt(2))[0]
    f3_actual = wgrid[idx[0]] / (2 * np.pi) if idx.size else w0_act / (2 * np.pi)
    return {"可行": True, "f0_ideal": f0_ideal, "f0_actual": w0_act / (2 * np.pi),
            "f3_actual": float(f3_actual),
            "Q_ideal": q_target, "Q_actual": q_act,
            "元件": {"R1": r1, "R2": r2, "R3": r3, "C1": c1, "C2": c2}}
