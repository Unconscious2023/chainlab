"""按元件值算频响：解析 "R1k" / "C1u" 这类写法，用节点法求 H(jω)。

支持两级拓扑：
  SK : Z1 Vin-A, Z2 A-B(+), Z3 A-out, Z4 B-gnd，跟随器 out=B
  MFB: Z1 Vin-A, Z2 A-B(-), Z3 A-out, Z4 A-gnd, Z5 B-out，虚地 B=0
"""

from __future__ import annotations

import re

import numpy as np

PREFIX = {
    "g": 1e9, "meg": 1e6, "m": 1e-3, "k": 1e3, "u": 1e-6, "µ": 1e-6,
    "n": 1e-9, "p": 1e-12, "f": 1e-15,
}
# 大写 M 按百万处理（用户约定：M/Meg 都是 M）
UPPER = {"K": 1e3, "M": 1e6, "G": 1e9}


def parse_elem(txt: str):
    """'R1k' → ('R', 1000.0)；'C1u' → ('C', 1e-6)；'null'/'' → (None, None)。"""
    if txt is None:
        return None, None
    s = str(txt).strip()
    if not s or s.lower() in ("null", "open", "开", "-"):
        return None, None
    s = s.replace(" ", "").replace("Ω", "").replace("F", "").replace("f", "f")
    m = re.fullmatch(r"([RrCc])\s*([0-9]*\.?[0-9]+)\s*(meg|MEG|Meg|[a-zA-Zµ]?)", s)
    if not m:
        raise ValueError(f"看不懂的元件写法：{txt!r}（示例 R1k / C1u / null）")
    kind = m.group(1).upper()
    val = float(m.group(2))
    suf = m.group(3) or ""
    if suf:
        if suf in UPPER:
            scale = UPPER[suf]
        elif suf in ("meg", "MEG", "Meg"):
            scale = 1e6
        else:
            scale = PREFIX.get(suf.lower())
            if scale is None:
                raise ValueError(f"不认识的单位前缀：{suf!r}")
        val *= scale
    return kind, val


def _admittance(kind, value, w):
    if kind is None:
        return 0.0
    if kind == "R":
        return 1.0 / value if value > 0 else 0.0
    return 1j * w * value


def solve(topo: str, z: list, freqs):
    """返回复频响数组。z = [Z1..Z5] 的 (kind, value)。"""
    topo = (topo or "sk").lower()
    f = np.asarray(freqs, dtype=float)

    # SK 且 Z2 开路 = 运放 + 端直接接中间节点，Z1/Z4 构成一阶 RC（跟随器输出）
    if topo == "sk" and z[1][0] is None:
        out = np.empty(f.shape, dtype=complex)
        for i, fr in enumerate(f):
            w = 2 * np.pi * fr
            y1 = _admittance(*z[0], w)
            y4 = _admittance(*z[3], w)
            if y1 == 0:
                out[i] = 0.0
            elif y4 == 0:
                out[i] = 1.0
            else:
                out[i] = y1 / (y1 + y4)
        return out
    out = np.empty(f.shape, dtype=complex)

    # 节点：0=A, 1=B, 2=out；外加运放输出电流 Io
    for idx, fr in enumerate(f):
        w = 2 * np.pi * fr
        A = np.zeros((4, 4), dtype=complex)
        b = np.zeros(4, dtype=complex)

        def add(i, j, y):
            A[i, i] += y
            if j >= 0:
                A[j, j] += y
                A[i, j] -= y
                A[j, i] -= y

        def inject(i, y):
            b[i] += y  # Vin = 1

        z1, z2, z3, z4 = z[0], z[1], z[2], z[3]
        z5 = z[4] if len(z) > 4 else (None, None)

        y1 = _admittance(*z1, w)
        y2 = _admittance(*z2, w)
        y3 = _admittance(*z3, w)
        y4 = _admittance(*z4, w)
        y5 = _admittance(*z5, w)

        add(0, -1, y1); inject(0, y1)          # Z1: Vin - A
        add(0, 1, y2)                          # Z2: A - B
        add(0, 2, y3)                          # Z3: A - out
        if topo == "mfb":
            add(0, -1, y4)                     # Z4: A - gnd
            add(1, 2, y5)                      # Z5: B - out
        else:
            add(1, -1, y4)                     # Z4: B - gnd

        A[2, 3] = 1.0                          # KCL at out: ... + Io = 0
        if topo == "mfb":
            A[3, 1] = 1.0                      # 虚地 V(B) = 0
        else:
            A[3, 1] = -1.0                     # V(out) - V(B) = 0
            A[3, 2] = 1.0

        x = np.linalg.solve(A, b)
        out[idx] = x[2]

    return out


def stats(freqs, h):
    """从算出的响应里找峰值、-3dB 点和 Q。"""
    f = np.asarray(freqs)
    mag = np.abs(h)
    if mag.size == 0 or mag.max() <= 0:
        return {}
    pk = float(mag.max())
    k = int(np.argmax(mag))
    fpk = float(f[k])
    # 峰值在端点 → 低通/高通：截止频率取下降沿那一侧
    if k == 0:
        idx = np.where(mag <= pk / np.sqrt(2))[0]
        return {"peak": pk, "f_peak": fpk,
                "f_lo": float(f[0]),
                "f_hi": float(f[idx[0]]) if idx.size else fpk,
                "Q": 0.5, "kind": "lowpass"}
    if k == len(mag) - 1:
        idx = np.where(mag <= pk / np.sqrt(2))[0]
        return {"peak": pk, "f_peak": fpk,
                "f_lo": float(f[idx[-1]]) if idx.size else fpk,
                "f_hi": float(f[-1]),
                "Q": 0.5, "kind": "highpass"}
    half = pk / np.sqrt(2)
    idx = np.where(mag >= half)[0]
    if idx.size >= 2:
        f_lo, f_hi = float(f[idx[0]]), float(f[idx[-1]])
    else:
        f_lo = f_hi = fpk
    bw = max(f_hi - f_lo, 1e-9)
    return {"peak": pk, "f_peak": fpk, "f_lo": f_lo, "f_hi": f_hi,
            "Q": fpk / bw if bw > 0 else 0.0}
