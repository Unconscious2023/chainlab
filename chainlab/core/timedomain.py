"""时域层的基础算子。

整条链在 fs_int 上跑：线性块用 FFT 施加频响（周期信号、相干窗下是精确的），
非线性块逐点运算。最后按 OSR 抽取成 ADC 采样点。
"""

from __future__ import annotations

import numpy as np


def lti(x: np.ndarray, fs: float, h) -> np.ndarray:
    """在频域施加 H(f)。

    记录长度一般不是信号周期的整数倍，直接循环卷积会在首尾接缝处产生
    伪影（波形上表现为满屏毛刺）。这里补零做线性卷积，只取前 n 个点。
    """
    n = len(x)
    if n == 0:
        return x
    m = 2 * n
    buf = np.zeros(m)
    buf[:n] = x
    f = np.fft.rfftfreq(m, d=1.0 / fs)
    spec = np.fft.rfft(buf) * np.asarray(h(f), dtype=complex)
    return np.fft.irfft(spec, m)[:n]


def clamp(x: np.ndarray, lo, hi):
    y = x
    if lo is not None:
        y = np.maximum(y, lo)
    if hi is not None:
        y = np.minimum(y, hi)
    return y


def clipped_fraction(x: np.ndarray, lo, hi) -> float:
    if lo is None and hi is None:
        return 0.0
    y = clamp(x, lo, hi)
    return float(np.mean(y != x))
