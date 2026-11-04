"""模拟乘法器（AD835 / AD633 类）。

时域 y = (x1 + x1os)(x2 + x2os) / U + z。
频域上乘法是谱的卷积——仍是闭式的，不需要时域层。
"""

from __future__ import annotations

import numpy as np

from ..core import devices
from ..core.block import Block, register, num
from ..core.spectral import Spectrum


@register("mult")
class Multiplier(Block):
    n_in = 2
    n_out = 1

    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        from ..core import devices as _dev
        _dev_meta = _dev.load(self.params['device']) if self.params.get('device') else {}
        self.en = self.num('en', None) or num((_dev_meta.get('noise_density') or (_dev_meta.get('multiplier') or {}).get('noise_density'))) or 0.0
        dev = devices.load(self.params["device"]) if self.params.get("device") else {}
        cfg = dev.get("multiplier", dev) if dev else {}
        self.name = dev.get("name") or "自定义乘法器"
        self.U = self.num("U", cfg.get("U", 1.0))
        self.x1os = self.num("x1os", cfg.get("feedthrough_x", cfg.get("feedthrough", 0.0)))
        self.x2os = self.num("x2os", cfg.get("feedthrough_y", cfg.get("feedthrough", 0.0)))
        self.z = self.num("z", 0.0)
        self.v_hi = self.num("v_hi", cfg.get("v_swing", None))
        self.v_lo = self.num("v_lo", cfg.get("v_lo", None))
        self.fc = self.num("fc", cfg.get("bw", None))
        self._ctx = ctx
        if self.fc:
            ctx.risk("乘法器的通道带宽按一阶极点近似，未建模压摆/失真")

    def H(self, f):
        f = np.asarray(f, dtype=float)
        if not self.fc:
            return np.ones(f.shape, dtype=complex)
        return 1.0 / (1.0 + 1j * f / self.fc)

    def process(self, inputs):
        a, b = inputs[0], inputs[1]
        out = _product(a.with_dc(self.x1os), b.with_dc(self.x2os))
        out = out.scaled(1.0 / self.U)
        if self.z:
            out = out.with_dc(self.z)
        if self.fc:
            out = out.filtered(self.H)
        peak = out.peak()
        return out, {"bw": self.fc or float("inf"), "peak_out": peak, "label": "乘法器"}

    def time_apply(self, inputs, fs, t):
        from ..core.timedomain import clamp, lti

        x1, x2 = inputs[0], inputs[1]
        y = (x1 + self.x1os) * (x2 + self.x2os) / self.U + self.z
        if self.fc:
            y = lti(y, fs, self.H)
        return clamp(y, self.num("v_lo", None), self.num("v_hi", None))

    def describe(self):
        return f"乘法器  {self.name}，U = {self.U:g} V"


def _product(a: Spectrum, b: Spectrum) -> Spectrum:
    """两个实信号的乘积，返回正频率表示。"""
    fa = np.concatenate([a.freqs, -a.freqs])
    ca = np.concatenate([a.coefs, np.conj(a.coefs)]) / 2.0
    fb = np.concatenate([b.freqs, -b.freqs])
    cb = np.concatenate([b.coefs, np.conj(b.coefs)]) / 2.0

    f = (fa[:, None] + fb[None, :]).ravel()
    c = (ca[:, None] * cb[None, :]).ravel()

    neg = f < 0
    f[neg] = -f[neg]
    c[neg] = np.conj(c[neg])

    pos = f > 1e-12
    zero = np.abs(f) <= 1e-12
    out_f = np.concatenate([f[pos], f[zero]])
    out_c = np.concatenate([2.0 * c[pos], c[zero]])
    return _prune(Spectrum(out_f, out_c))


def _prune(sig: Spectrum, rel: float = 1e-7) -> Spectrum:
    """合并同频分量并丢掉可忽略的小分量，防止谱规模爆炸。"""
    if sig.freqs.size == 0:
        return sig
    order = np.argsort(sig.freqs)
    f = sig.freqs[order]
    c = sig.coefs[order]
    scale = max(abs(f).max(), 1.0)
    key = np.round(f / (scale * 1e-12)).astype(np.int64)
    uniq, start = np.unique(key, return_index=True)
    counts = np.diff(np.append(start, len(key)))
    sums = np.add.reduceat(c, start)
    freqs = f[start]
    mags = np.abs(sums)
    keep = mags >= max(mags.max() * rel, 1e-18)
    return Spectrum(freqs[keep], sums[keep])

    def psd(self, f):
        """乘法器输出噪声：按器件噪声密度（V/√Hz）当白噪声。"""
        import numpy as np

        f = np.asarray(f, dtype=float)
        if not self.en:
            return np.zeros(f.shape)
        return np.full(f.shape, self.en ** 2)
