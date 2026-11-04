"""滤波器：1~8 阶，支持 Butterworth / Chebyshev，或直接给 s 域传函。"""

from __future__ import annotations

import numpy as np

from ..core import filterdesign
from ..core import devices
from ..core.block import Block, num, register, sdomain_H

K_BOLTZMANN = 1.380649e-23
T_NOM = 300.0


@register("filter")
class Filter(Block):
    def __init__(self, params, ctx):
        super().__init__(params, ctx)
        self.mode = str(self.params.get("mode", "design")).lower()

        dev = devices.load(self.params["device"]) if self.params.get("device") else {}
        cfg = (dev.get("opamp") or dev) if dev else {}
        self.name = dev.get("name") or ""
        self.en = 0.0
        self.in_noise = 0.0
        if self.mode == "tf":
            def _coef(v, dflt):
                if isinstance(v, (list, tuple)):
                    items = list(v)
                else:
                    raw = str(v or "").replace("，", ",").replace(" ", "")
                    items = [x for x in raw.split(",") if x != ""]
                out = []
                for x in items:
                    try:
                        out.append(float(x))
                    except (TypeError, ValueError):
                        raise ValueError(f"传函系数 {x!r} 不是数字")
                return out or list(dflt)

            self.num_s = _coef(self.params.get("num"), [1.0])
            self.den_s = _coef(self.params.get("den"), [1.0, 1.0])
            self.fc = self.num("fc", 0.0)
            self.label = "自定义传函"
            self._pz = None
        elif self.mode == "design":
            self._design()
        elif self.mode == "netlist":
            # 按元件值：Z1..Z5 直接给，响应由节点法算
            from ..core.netlist import parse_elem

            # 多级：zstages=[{topo,z:[5 个写法]}, ...]；单级时退回 ztopo/z1..z5
            raw = self.params.get("zstages")
            if isinstance(raw, list) and raw:
                self._stages = [{"topo": str(st.get("topo")
                                                 or str(st.get("combo", "sk")).split("_")[0]
                                                 or "sk").lower(),
                                 "z": [parse_elem(x) for x in (st.get("z") or [])]}
                                for st in raw]
            else:
                self._stages = [{
                    "topo": str(self.params.get("ztopo") or "sk").lower(),
                    "z": [parse_elem(self.params.get(f"z{i}")) for i in range(1, 6)],
                }]
            self._ztopo = self._stages[0]["topo"]
            self._z = self._stages[0]["z"]

            from ..core.topologies import match as _match

            for _si, _st in enumerate(self._stages, 1):
                _ft, _err = _match(_st["topo"], [k for k, _v in _st["z"]])
                if _err:
                    _pre = f"第 {_si} 级：" if len(self._stages) > 1 else ""
                    raise ValueError(_pre + _err)
            self.num_s, self.den_s = [1.0], [1.0]
            self.fc = 0.0
            self.label = "按元件值"
            self._pz = None
        else:
            raise ValueError(f"滤波器 mode={self.mode} 不支持（可用 tf / design / netlist）")

        if self.mode == "netlist":
            from ..core.netlist import solve

            def _cascade(f):
                h = None
                for st in self._stages:
                    v = solve(st["topo"], st["z"], f)
                    h = v if h is None else h * v
                return h

            self.Hf = _cascade
        else:
            self.Hf = sdomain_H(self.num_s, self.den_s)
        self.en = self.num("en", None) or num(cfg.get("en")) or 0.0
        self.in_noise = self.num("in", None) or num(cfg.get("in")) or 0.0

        r = self.num("R", None)
        self.noise_psd = self.num("noise_psd", None)
        if r is not None:
            self.psd_flat = 4 * K_BOLTZMANN * T_NOM * r
        elif self.noise_psd is not None:
            self.psd_flat = self.noise_psd ** 2
        else:
            self.psd_flat = 0.0

    def _design(self):
        ftype = str(self.params.get("type", "lowpass")).lower()
        order = int(self.num("order", 2))
        fc = self.num("fc", 1e3)
        q = self.num("Q", 0.7071)
        gain = self.num("gain", 1.0)
        resp = str(self.params.get("response", "butterworth")).lower()
        ripple = self.num("ripple_db", 0.5)
        atten = self.num("atten_db", 40.0)
        f_lo = self.num("f_lo", None)
        f_hi = self.num("f_hi", None)

        if ftype == "bandpass" and f_lo and f_hi:
            # 带通的 order 是**总阶数**：拆成 HP(ceil) + LP(floor)，
            # 与设计器里的曲线算法完全一致（否则曲线和实际滤波对不上）
            hn = (order + 1) // 2
            ln = max(order - hn, 1)
            ph, zh, kh = filterdesign.design_pz("highpass", hn, f_lo, resp, ripple, gain)
            pl, zl, kl = filterdesign.design_pz("lowpass", ln, f_hi, resp, ripple, gain)
            poles = np.concatenate([ph, pl])
            zeros = np.concatenate([zh, zl])
            k = kh * kl
        else:
            poles, zeros, k = filterdesign.design_pz(
                ftype, order, fc, resp, ripple, gain, q, f_lo, f_hi, atten
            )
        self._pz = (poles, zeros, k)
        self.num_s = (np.real(np.poly(zeros)) * k if zeros.size else np.array([k])).tolist()
        self.den_s = np.real(np.poly(poles)).tolist()
        if ftype == "bandpass" and f_lo and f_hi:
            fc = float(np.sqrt(float(f_lo) * float(f_hi)))
        self.fc = fc

        cn = {"lowpass": "低通", "highpass": "高通", "bandpass": "带通"}
        rn = {"butterworth": "巴特沃斯", "chebyshev1": "切比雪夫 I",
              "chebyshev2": "切比雪夫 II", "bessel": "贝塞尔"}
        self.label = f"{cn.get(ftype, ftype)} {order} 阶 {rn.get(resp, '')}".strip()

    def H(self, f):
        if self._pz is not None:
            return filterdesign.pz_response(*self._pz, f)
        return self.Hf(f)

    def psd(self, f):
        f = np.asarray(f, dtype=float)
        psd = self.psd_flat or 0.0
        if self.en:
            psd = psd + self.en ** 2      # 运放电压噪声（输出参考，噪声增益按 1 估）
        if not psd:
            return np.zeros(f.shape)
        return np.full(f.shape, psd)

    def process(self, inputs):
        out = inputs[0].filtered(self.Hf)
        return out, {"bw": self.fc or None, "peak_out": out.peak(), "label": self.label}

    def describe(self):
        return f"滤波器  {self.label}，fc = {self.fc / 1e3:.4g} kHz"
