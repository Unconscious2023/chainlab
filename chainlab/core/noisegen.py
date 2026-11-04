"""按给定的单边功率谱密度合成有色噪声。

白噪声过一遍形状为 S(f) 的滤波器，等价于这个频域加权。
只用来生成采样序列，指标仍由解析预算给出。
"""

from __future__ import annotations

import numpy as np


def synthesize(psd_fn, fs: float, n: int, seed: int = 0) -> np.ndarray:
    """返回长度为 n 的实序列，单边 PSD 约等于 psd_fn(f)。"""
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n)
    spec = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, d=1.0 / fs)

    s = np.maximum(np.asarray(psd_fn(f), dtype=float), 0.0)
    s_white = 2.0 / fs          # 单位方差白噪声的单边 PSD
    gain = np.sqrt(s / s_white)

    out = np.fft.irfft(spec * gain, n)
    return out
