"""加法器：多路求和 + 偏置 + 噪声 + 纹波。

端口 i 的增益取 k[i]（k 给标量时各端口同增益）。
"""

from __future__ import annotations

import numpy as np

from ..core import devices
from ..core.block import Block, num, register
from ..core.spectral import Spectrum


@register("add")
class Add(Block):
    n_in = None  # 端口数由连线决定

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.k_raw = self.params.get("k", 1.0)
        self.n_fixed = self.num("n_in", None)
        self.dc = self.num("dc", 0.0)
        dev = devices.load(self.params["device"]) if self.params.get("device") else {}
        cfg = (dev.get("opamp") or dev) if dev else {}
        self.name = dev.get("name") or ""
        self.en = self.num("en", cfg.get("en", 0.0))
        self.in_noise = (self.num("in", None) or num(cfg.get("in")) or 0.0)
        self.z_in = self.num("zin", 1e6)
        self.noise_psd = self.num("noise_psd", 0.0)
        r = self.params.get("ripple")
        self.ripple = None
        if isinstance(r, dict) and r:
            self.ripple = (num(r.get("f"), 100.0), num(r.get("amp"), 0.0))

    def gains(self, n: int) -> list[float]:
        k = self.k_raw
        if isinstance(k, (list, tuple)):
            g = [num(x, 1.0) for x in k]
            while len(g) < n:
                g.append(g[-1] if g else 1.0)
            return g[:n]
        return [num(k, 1.0)] * n

    def port_gain(self, port: int, n_in: int) -> float:
        g = self.gains(max(n_in, 1))
        return g[port] if port < len(g) else g[-1]

    def psd(self, f):
        f = np.asarray(f, dtype=float)
        if self.en:
            return np.full(f.shape, self.en ** 2)
        if not self.noise_psd:
            return np.zeros(f.shape)
        return np.full(f.shape, self.noise_psd ** 2)

    def _pad(self, inputs, filler):
        """端口数固定时，没接的端口按 0 处理。"""
        if not self.n_fixed:
            return inputs
        n = int(self.n_fixed)
        out = list(inputs[:n])
        while len(out) < n:
            out.append(filler)
        return out

    def process(self, inputs):
        if not inputs:
            raise ValueError("加法器至少需要一路输入")
        inputs = self._pad(inputs, Spectrum.empty())
        g = self.gains(len(inputs))
        out = inputs[0].scaled(g[0])
        for s, k in zip(inputs[1:], g[1:]):
            out = out.plus(s.scaled(k))
        if len(inputs) > 1:
            out = out.merged()
        out = out.with_dc(self.dc)
        if self.ripple and self.ripple[1]:
            out = out.with_tone(self.ripple[0], self.ripple[1])
        return out, {
            "bw": float("inf"),
            "peak_out": out.peak(),
            "dc": self.dc,
            "n_in": len(inputs),
            "label": "加法/求和",
        }

    def time_apply(self, inputs, fs, t):
        from ..core.timedomain import clamp

        inputs = self._pad(inputs, np.zeros_like(inputs[0]))
        g = self.gains(len(inputs))
        y = np.zeros_like(inputs[0])
        for sig, k in zip(inputs, g):
            y = y + k * sig
        y = y + self.dc
        if self.ripple and self.ripple[1]:
            y = y + self.ripple[1] * np.cos(2 * np.pi * self.ripple[0] * t)
        return clamp(y, self.num("v_lo", None), self.num("v_hi", None))

    def describe(self):
        s = f"加法器  {getattr(self, '_n_in', 0)} 路求和，直流 {self.dc:.4g} V"
        if self.ripple and self.ripple[1]:
            s += f"，纹波 {self.ripple[0]:.4g} Hz / {self.ripple[1] * 1e3:.3g} mV"
        return s


def _merge(sig):
    """把相同频率的分量合并，保持谱规模不膨胀。"""
    from ..core.spectral import Spectrum

    if sig.freqs.size <= 1:
        return sig
    order = np.argsort(sig.freqs)
    f = sig.freqs[order]
    c = sig.coefs[order]
    scale = max(abs(f).max(), 1e-9)
    key = np.round(f / (scale * 1e-12)).astype(np.int64)
    _, idx = np.unique(key, return_index=True)
    out_f, out_c = [], []
    for i in idx:
        same = key == key[i]
        out_f.append(f[same][0])
        out_c.append(c[same].sum())
    return Spectrum(np.array(out_f), np.array(out_c))
