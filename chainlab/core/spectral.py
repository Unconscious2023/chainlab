"""谱表示：信号 = Σ_k Re(c_k · e^{j2π f_k t})。

解析层不生成时域数组，只在需要的位置求值——所以采样率再高也不会爆内存。
"""

from __future__ import annotations

import numpy as np


class Spectrum:
    """一组复幅度正弦分量。c_k 为复数，实部信号由 Re() 还原。"""

    __slots__ = ("freqs", "coefs")

    def __init__(self, freqs, coefs):
        f = np.asarray(freqs, dtype=float).ravel()
        c = np.asarray(coefs, dtype=complex).ravel()
        if f.shape != c.shape:
            raise ValueError(f"频率与系数长度不一致：{f.shape} vs {c.shape}")
        self.freqs = f
        self.coefs = c

    # ---------- 构造 ----------
    @classmethod
    def empty(cls):
        return cls(np.zeros(0), np.zeros(0, dtype=complex))

    @classmethod
    def tone(cls, f, amp, phase=0.0):
        """amp·cos(2πft + phase)"""
        return cls([f], [amp * np.exp(1j * phase)])

    def copy(self):
        return Spectrum(self.freqs.copy(), self.coefs.copy())

    # ---------- 变换 ----------
    def scaled(self, k):
        return Spectrum(self.freqs, self.coefs * k)

    def filtered(self, h):
        """逐频点乘复频响；h 可以是标量或 f -> array 的函数。"""
        freqs = self.freqs
        if callable(h):
            resp = np.asarray(h(freqs), dtype=complex)
        else:
            resp = np.full(freqs.shape, complex(h))
        return Spectrum(freqs, self.coefs * resp)

    def plus(self, other):
        return Spectrum(
            np.concatenate([self.freqs, other.freqs]),
            np.concatenate([self.coefs, other.coefs]),
        )

    def merged(self, rel: float = 1e-12):
        """同频分量相加。两路信号叠加后必须合并，否则取基波时只会拿到其中一路。"""
        if self.freqs.size <= 1:
            return self
        order = np.argsort(self.freqs)
        f = self.freqs[order]
        c = self.coefs[order]
        scale = max(abs(f).max(), 1.0)
        key = np.round(f / (scale * rel)).astype(np.int64)
        uniq, start = np.unique(key, return_index=True)
        sums = np.add.reduceat(c, start)
        return Spectrum(f[start], sums)

    def with_dc(self, v):
        if abs(v) < 1e-18:
            return self.copy()
        return self.plus(Spectrum([0.0], [complex(v)]))

    def with_tone(self, f, amp, phase=0.0):
        if abs(amp) < 1e-18:
            return self.copy()
        return self.plus(Spectrum.tone(f, amp, phase))

    # ---------- 求值 ----------
    def eval(self, t):
        t = np.asarray(t, dtype=float)
        if self.freqs.size == 0:
            return np.zeros_like(t)
        ph = np.exp(1j * 2 * np.pi * np.multiply.outer(t, self.freqs))
        return np.real(ph @ self.coefs)

    def rms(self) -> float:
        if self.freqs.size == 0:
            return 0.0
        pos = self.freqs > 1e-9
        power = np.where(pos, np.abs(self.coefs) ** 2 / 2.0, np.abs(self.coefs) ** 2)
        return float(np.sqrt(power.sum()))

    def ac_rms(self) -> float:
        """不含直流分量的有效值。"""
        if self.freqs.size == 0:
            return 0.0
        pos = self.freqs > 1e-9
        return float(np.sqrt((np.abs(self.coefs[pos]) ** 2 / 2.0).sum()))

    def peak(self) -> float:
        """峰值上界：各分量绝对值之和（保守，够用于范围检查）。"""
        if self.freqs.size == 0:
            return 0.0
        return float(np.abs(self.coefs).sum())

    def fundamental(self):
        pos = self.freqs > 1e-9
        if not pos.any():
            return None, 0.0
        mags = np.where(pos, np.abs(self.coefs), -1.0)
        idx = int(np.argmax(mags))
        return float(self.freqs[idx]), float(np.abs(self.coefs[idx]))

    def max_significant_f(self, rel: float = 1e-3) -> float | None:
        """幅度不低于最大分量 rel 倍的最高频率。"""
        if self.freqs.size == 0:
            return None
        mags = np.abs(self.coefs)
        top = mags.max()
        if top <= 0:
            return None
        mask = (mags >= top * rel) & (self.freqs > 1e-9)
        if not mask.any():
            return None
        return float(self.freqs[mask].max())

    def thd(self) -> float:
        """总谐波失真（相对基波的幅度比），不含 DC。"""
        f0, a0 = self.fundamental()
        if not f0 or a0 <= 0:
            return 0.0
        pos = self.freqs > 1e-9
        mask = pos & (np.abs(self.freqs - f0) > f0 * 1e-6)
        if not mask.any():
            return 0.0
        return float(np.sqrt((np.abs(self.coefs[mask]) ** 2).sum()) / a0)

    def largest_spur(self):
        """最大杂散（基波以外）的幅度与频率。"""
        f0, _ = self.fundamental()
        if not f0:
            return None, 0.0
        pos = self.freqs > 1e-9
        mask = pos & (np.abs(self.freqs - f0) > f0 * 1e-6)
        if not mask.any():
            return None, 0.0
        mags = np.where(mask, np.abs(self.coefs), -1.0)
        idx = int(np.argmax(mags))
        return float(self.freqs[idx]), float(mags[idx])

    def components(self):
        order = np.argsort(-np.abs(self.coefs))
        return [
            (float(self.freqs[i]), float(np.abs(self.coefs[i])),
             float(np.angle(self.coefs[i])))
            for i in order
            if self.coefs[i] != 0
        ]

    def __repr__(self):
        parts = ", ".join(
            f"{f:.6g}Hz@{a * 1e3:.4g}mV" for f, a, _ in self.components()[:4]
        )
        return f"Spectrum({self.freqs.size} 分量: {parts})"
