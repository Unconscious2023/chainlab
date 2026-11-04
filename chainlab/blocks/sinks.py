"""观测端：示波器、频谱仪、算法。挂在 ADC/DAC 之后，不参与链路传递。

`source` 参数指定观测哪个 ADC/DAC（如 adc1 / dac1）；留空时按连线取上游的
那个 ADC/DAC —— 上游接的是 DAC 就观测 DAC，不会再统一落到第一个 ADC。
上游不是 ADC/DAC 时由运行器报错，不做静默替换。
"""

from __future__ import annotations

import importlib.util
import pathlib

import numpy as np

from ..core.block import Sink, num, register


def volts(codes, meta):
    if meta.get("ideal"):
        return np.asarray(codes, dtype=float)
    return (np.asarray(codes, dtype=float) - meta["offset_code"]) * meta["lsb"]


@register("scope")
class Scope(Sink):
    def observe(self, codes, meta):
        fs = meta["fs"]
        v = volts(codes, meta)
        if v.size == 0:
            return {"错误": "没有样本"}

        t_all = np.arange(v.size) / fs
        t0 = float(num(self.params.get("t_start"), 0.0) or 0.0)
        t1 = self.params.get("t_end")
        t1 = float(num(t1)) if t1 not in (None, "") else t_all[-1]
        mask = (t_all >= t0 - 1e-15) & (t_all <= t1 + 1e-15)
        if not mask.any():
            return {"错误": f"时间窗 [{t0:g}, {t1:g}] s 内没有样本（总时长 {t_all[-1]:g} s）"}

        tw = t_all[mask]
        vw = v[mask]
        centered = vw - vw.mean()

        # 显示点数 = 真的只画这么多个点（从时间窗起点连续取），不做均匀抽稀
        n_show = int(num(self.params.get("n_points"), 0) or 0) or 1200
        n_show = max(2, min(n_show, int(tw.size)))
        plot = {
            "t": [float(x) for x in tw[:n_show]],
            "v": [float(x) for x in vw[:n_show]],
            "t0": float(tw[0]),
            "t1": float(tw[n_show - 1]),
            "fs": fs,
            # DAC 的输出是零阶保持：画成阶梯，不能把相邻帧用直线连起来
            "step": bool(meta.get("zoh")),
            "update_fs": meta.get("update_fs"),
        }

        return {
            "起始时间": float(tw[0]),
            "终止时间": float(tw[-1]),
            "样本数": int(vw.size),
            "显示点数": int(len(plot["t"])),
            "窗口样本数": int(tw.size),
            "峰峰值": float(np.ptp(vw)),
            "交流 RMS": float(np.sqrt(np.mean(centered ** 2))),
            "最小值": float(vw.min()),
            "最大值": float(vw.max()),
            "过零次数": int(np.count_nonzero(np.diff(np.signbit(centered)))),
            "削顶占比": float(meta.get("clipped", 0.0)),
            "_plot": plot,
        }

    def describe(self):
        src = str(self.params.get("source") or "").strip()
        return f"示波器（{src}）" if src else "示波器"


@register("specan")
class SpectrumAnalyzer(Sink):
    def describe(self):
        src = str(self.params.get("source") or "").strip()
        return f"频谱仪（{src}）" if src else "频谱仪"

    def observe(self, codes, meta):
        fs = meta["fs"]
        v = volts(codes, meta)
        n = v.size
        n_req = int(self.num("n_fft", 65536))
        n_req = max(n_req, 16)
        n_fft = max(min(n_req, n), 16)

        notes = []
        if n < n_req:
            notes.append(
                f"实际样本只有 {n} 点，FFT 按 {n_fft} 点计算；"
                f"要真正的 {n_req} 点谱，得把时窗拉长（样本数 = fs × T）"
            )

        x = v - v.mean()
        wname = str(self.params.get("window", "hann")).lower()
        if wname in ("none", "rect", "矩形"):
            w = np.ones(n)
        elif wname in ("blackman", "bh"):
            w = np.blackman(n)
        else:
            w = np.hanning(n)

        buf = np.zeros(n_fft)
        buf[:n] = x * w

        spec = np.abs(np.fft.rfft(buf))
        amp = 2.0 * spec / max(w.sum(), 1e-30)
        freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs)

        full = meta.get("full_scale_amp")
        if not full:
            full = meta["vref"] if meta.get("differential") else meta["vref"] / 2.0
        amp = np.maximum(amp, 1e-30)
        dbfs = 20 * np.log10(amp / full)

        # 忽略直流附近
        keep = freqs > 3 * fs / max(n, 1)
        if not keep.any():
            keep = np.ones_like(freqs, dtype=bool)
        f_keep, db_keep = freqs[keep], dbfs[keep]

        idx = int(np.argmax(db_keep))
        f_peak, db_peak = float(f_keep[idx]), float(db_keep[idx])

        bin_width = fs / n_fft
        resolution = fs / n  # 真实分辨率受实际样本数限制

        noise_floor_bin = float(np.median(db_keep))
        noise_floor_hz = noise_floor_bin - 10 * np.log10(bin_width)

        # 画图用：峰值保持降采样。直接抽样会把窄峰整个跳过，谱看起来像没有信号。
        n_bins = 1200
        if len(f_keep) > n_bins:
            edges = np.linspace(0, len(f_keep), n_bins + 1).astype(int)
            pf, pd = [], []
            for a, b in zip(edges[:-1], edges[1:]):
                if b <= a:
                    continue
                seg = db_keep[a:b]
                j = int(np.argmax(seg))
                pf.append(float(f_keep[a + j]))
                pd.append(float(seg[j]))
        else:
            pf = [float(x) for x in f_keep]
            pd = [float(x) for x in db_keep]

        plot = {"f": pf, "db": pd, "fs": fs}

        return {
            "样本数": n,
            "FFT 点数": n_fft,
            "谱线间隔": bin_width,
            "真实分辨率": resolution,
            "峰值频率": f_peak,
            "峰值电平(dBFS)": db_peak,
            "噪底(dBFS/bin)": noise_floor_bin,
            "噪底(dBFS/Hz)": noise_floor_hz,
            "提示": notes,
            "_plot": plot,
        }


@register("algo")
class Algo(Sink):
    def describe(self):
        return "算法"

    def observe(self, codes, meta):
        p = self.params.get("path")
        if not p:
            return {"状态": "未配置 path，仅透传", "样本数": int(np.size(codes))}
        path = pathlib.Path(p)
        if not path.is_absolute() and self.ctx.base_dir:
            path = pathlib.Path(self.ctx.base_dir) / path
        if not path.exists():
            return {"错误": f"找不到算法文件：{path}"}

        spec = importlib.util.spec_from_file_location("chainlab_user_algo", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "run", None) or getattr(mod, "algo", None)
        if fn is None:
            return {"错误": f"{path.name} 里没有 run() 或 algo() 函数"}

        out = fn(np.asarray(codes), meta["fs"])
        return {"结果": out if isinstance(out, dict) else {"返回值": out}}
