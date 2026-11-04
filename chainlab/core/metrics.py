"""指标：噪声预算与稳态指标。

噪声按 PSD 沿「该节点到 ADC」的传递函数积分。分叉再汇合时按路径相干相加，
否则两路同源噪声会被算小。
"""

from __future__ import annotations

import numpy as np


def _grid(fs: float, blocks) -> np.ndarray:
    """噪声积分网格：只算到 fs/2。

    高于 fs/2 的噪声会折叠回基带，但折叠量取决于前端抗混叠滤波，
    这里不含；只统计与信号同带的那部分，和 ADC 手册的 SNR 定义一致。
    """
    fmax = max(fs / 2.0, 20.0)
    return np.logspace(0.0, np.log10(fmax), 4000)


KT = 1.380649e-23 * 290.0        # 室温 kT，约 4e-21 J


def noise_budget(graph, fs: float, to: str | None = None) -> dict:
    f = _grid(fs, graph.blocks())
    transfer = graph.transfers(f, to=to)
    zsrc = graph.source_impedance()
    rows = []

    for n in graph.blocks():
        if n.is_adc:
            continue
        if n.is_dac:
            # DAC 的输出噪声只影响它自己的观测端；折算到 ADC 的预算里没有意义
            continue
        psd = np.asarray(n.obj.psd(f), dtype=float)

        # 电流噪声：in 流过源阻抗变成输入参考电压噪声
        i_noise = getattr(n.obj, "in_noise", None)
        # 输出参考的块（TIA）已经在自己的 psd 里含了 in·Rf，不能再套一遍 in·Z_src
        if i_noise and not getattr(n.obj, "noise_output_referred", False):
            zs = [zsrc.get((e.src, n.id, e.port), 0.0) for e in n.ins]
            zs = [z for z in zs if z is not None and np.isfinite(z)]
            if zs:
                psd = psd + (i_noise * max(zs)) ** 2

        # 选了真实器件但这颗料没给噪声数据 → 在预算表里标出来，
        # 否则它按 0 算，结果会偏乐观而看不出来
        miss = ""
        dev_name = getattr(n.obj, "name", "") or ""
        if dev_name and n.type in ('amp','filter','add','tia'):
            try:
                from . import devices as _dev

                _op = (_dev.load(dev_name) or {}).get("opamp") or {}
                lacking = [k for k in ("en", "in") if _op.get(k) in (None, "")]
                if lacking:
                    miss = f"{dev_name} 手册未给 {'/'.join(lacking)}，该项按 0 计"
            except Exception:  # noqa: BLE001
                pass
            if not miss and not np.any(psd > 0):
                miss = f"{dev_name} 噪声数据缺失"

        t = transfer.get(n.id)
        if t is None:
            rows.append({"kind": n.type, "id": n.id, "noise": 0.0,
                         "missing": "未连接到 ADC，未计入噪声"})
            continue
        if not np.any(psd > 0):
            rows.append({"kind": n.type, "id": n.id, "noise": 0.0,
                         "missing": miss})
            continue
        p = (np.trapezoid(psd, f)
             if getattr(n.obj, "noise_output_referred", False)
             else np.trapezoid(psd * np.abs(t) ** 2, f))
        rows.append({"kind": n.type, "id": n.id,
                     "noise": float(np.sqrt(max(p, 0.0))), "missing": miss})

    # 源阻抗的热噪声：4kTR，按节点计一次（不要在下级重复计）
    for n in graph.blocks():
        if n.is_adc:
            continue
        zout = n.obj.zout
        t = transfer.get(n.id)
        if not zout or zout <= 0 or t is None:
            continue
        # 有源级（运放/加法器/TIA…）输出的热噪声已经包含在它自己的 en 里，
        # 再把 zout 当电阻算一遍就是重复计数，只对无源节点（源、负载、隔直）计。
        if not getattr(n.obj, "thermal_zout", False):
            continue
        if getattr(n.obj, "is_current", False):
            # 电流源是诺顿等效：并联电流源 4kT/R，再乘负载阻抗
            zl = graph.load_impedance(n)
            psd = 4.0 * KT / zout * zl ** 2
            p = np.trapezoid(psd * np.abs(t) ** 2, f)
        else:
            p = np.trapezoid(4.0 * KT * zout * np.abs(t) ** 2, f)
        rows.append({"kind": "热噪声", "id": n.id, "noise": float(np.sqrt(max(p, 0.0)))})

    return {"rows": rows}


def analyze(graph, sig, adc_noise: float, fs: float, adc=None) -> dict:
    adc_node = adc or graph.adc
    rows = noise_budget(graph, fs, to=adc_node.id)["rows"]
    rows.append({"kind": "adc", "id": adc_node.id, "noise": float(adc_noise)})
    total = float(np.sqrt(sum(r["noise"] ** 2 for r in rows)))

    f0, a0 = sig.fundamental()
    fund_rms = a0 / np.sqrt(2.0) if f0 else 0.0
    thd = sig.thd()
    harm_rms = thd * fund_rms

    def db(x):
        return 20.0 * np.log10(x) if x > 0 else float("-inf")

    snr = db(fund_rms / total) if total > 0 else float("inf")
    sinad = db(fund_rms / np.hypot(total, harm_rms)) if (total or harm_rms) else float("inf")
    enob = (sinad - 1.76) / 6.02 if np.isfinite(sinad) else 99.0

    # 量程利用率：信号峰値 / (Vref/2)。ENOB 随信号变小而下降，
    # 数据手册的 ENOB 是在「接近满量程」下测的，两者要分开给。
    try:
        vref = float(adc_node.obj.vref)
        full_amp = vref if getattr(adc_node.obj, "diff", False) else vref / 2.0
    except Exception:  # noqa: BLE001
        vref = 0.0
        full_amp = 0.0
    fs_ratio = (a0 / full_amp) if (full_amp and a0) else 1.0
    enob_full = enob - 20.0 * np.log10(max(fs_ratio, 1e-9)) / 6.02 if fs_ratio > 0 else enob

    spur_f, spur_a = sig.largest_spur()
    sfdr = db(a0 / spur_a) if (spur_a > 0 and a0 > 0) else float("inf")

    rows_sorted = sorted(rows, key=lambda r: -r["noise"])
    return {
        "rows": rows_sorted,
        "noise_total": total,
        "adc_noise": adc_noise,
        "adc_id": adc_node.id,
        "signal_rms": sig.rms(),
        "ac_rms": sig.ac_rms(),
        "signal_peak": sig.peak(),
        "fund_hz": f0,
        "fund_amp": a0,
        "thd": thd,
        "snr_db": snr,
        "sinad_db": sinad,
        "enob": enob,
        "enob_full": float(enob_full),
        "fs_ratio": float(fs_ratio),
        "sfdr_db": sfdr,
        "spur_hz": spur_f,
    }
