"""中文报告输出。面向学生的一切文字都用中文。"""

from __future__ import annotations

import math

KIND_CN = {
    "source": "信号源",
    "amp": "放大器",
    "filter": "滤波器",
    "add": "加法器",
    "mult": "乘法器",
    "buf": "缓冲器",
    "adc": "ADC",
    "adc_ideal": "ADC",
    "dac": "DAC",
    "scope": "示波器",
    "specan": "频谱仪",
    "algo": "算法",
}

_PREFIX = [
    (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
]


def eng(v, unit="", digits=4):
    if v is None:
        return "—"
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return "∞" if v > 0 else "—"
    if v == 0:
        return f"0 {unit}".strip()
    for scale, pre in _PREFIX:
        if abs(v) >= scale * 0.999:
            return f"{v / scale:.{digits}g} {pre}{unit}".strip()
    return f"{v:.{digits}g} {unit}".strip()


def db(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞"
    return f"{v:.2f} dB"


def _rule(ch="─", n=64):
    return ch * n


def format_report(res: dict) -> str:
    out = []
    add = out.append

    add(_rule("═"))
    add(f"信号链路仿真 v1.1  —  {res['name']}")
    add(_rule("═"))

    # ---- 运行信息
    add("")
    add("【运行信息】")
    add(f"  模式      {res['mode']}")
    add(f"  采样率    {eng(res['fs'], 'Hz')}   （严格按设定，不做相干吸附）")
    add(f"  时窗      {res['t_seconds'] * 1e3:.4g} ms（{res['periods']:.4g} 个信号周期）")
    add(f"  样本数    {res['n_samples']}")
    if res["adc"].get("noise_injected"):
        add(
            f"  噪声      {eng(res['adc']['noise_injected'], 'Vrms')}"
            f"（已按预算表叠加到采样序列，故谱上能看到噪底）"
        )

    # ---- 链路
    add("")
    add("【链路】")
    for i, line in enumerate(res["chain"], 1):
        add(f"  {i}. {line}")

    # ---- 误差预算
    add("")
    add("【误差预算  折算到 ADC 输入】")
    rows = res["budget"]
    total = res["noise_total"]
    for r in rows:
        if r["noise"] <= 0:
            continue
        pct = 100.0 * r["noise"] ** 2 / total ** 2 if total > 0 else 0.0
        name = KIND_CN.get(r["kind"], r["kind"])
        pct_s = f"{pct:5.1f}" if pct >= 0.1 else f"{pct:5.2f}"
        add(f"  {name:<8} {eng(r['noise'], 'Vrms'):>14}   {pct_s} %")
    if total > 0:
        add(f"  {'合计':<8} {eng(total, 'Vrms'):>14}   100.0 %")

    # ---- 稳态指标
    m = res["metrics"]
    add("")
    add("【稳态指标】")
    add(f"  基波      {eng(m['fund_hz'], 'Hz')}，{eng(m['fund_amp'], 'Vpk')}")
    add(f"  交流 RMS  {eng(m['ac_rms'], 'V')}  （不含直流）")
    add(f"  SNR       {db(m['snr_db'])}")
    add(f"  THD       {(m['thd'] * 100):.2f} %  （{db(20 * math.log10(m['thd'])) if m['thd'] > 0 else '∞'}）")
    add(f"  SINAD     {db(m['sinad_db'])}")
    add(f"  有效位数  {m['enob']:.2f} bit")
    add(f"  SFDR      {db(m['sfdr_db'])}")

    # ---- ADC
    info = res["adc"].get("info") or {}
    if info:
        add("")
        add("【ADC 时序】")
        add(f"  源阻抗 R_AIN  {eng(info['rain'], 'Ω')}")
        add(f"  采样周期      {info['cycles']:g} 个 ADCCLK  →  {eng(info['t_sample'], 's')}")
        add(f"  转换时间      {eng(info['t_conv'], 's')}")
        add(f"  合计          {eng(info['t_total'], 's')}   上限 {eng(1 / res['fs'], 's')}")
        add(f"  建立误差      {info['err_lsb']:.3g} LSB")
        add(f"  该配置最高    {eng(info['fs_max'], 'Hz')}")
        lsb = res["adc"].get("lsb") or 0.0
        if lsb:
            add(f"  量化噪声      {eng(lsb / 12 ** 0.5, 'Vrms')}（LSB/√12，仅理想量化）")
            add(f"  ENOB 折算噪声 {eng(res['adc']['adc_noise'], 'Vrms')}（含全部 ADC 误差，取大者为准）")

    # ---- 观测（每个示波器/频谱仪各自一段）
    order = res.get("sink_order") or list(res["sinks"])
    for sid in order:
        data = res["sinks"].get(sid)
        if not isinstance(data, dict):
            continue
        kind = data.get("_kind") or sid
        add("")
        src = data.get("_source")
        title = KIND_CN.get(kind, kind)
        add(f"【{title} {sid}】" + (f"  数据来源：{src}" if src else ""))
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                for item in v:
                    add(f"  · {item}")
                continue
            if isinstance(v, float):
                add(f"  {k:<14} {v:.6g}")
            else:
                add(f"  {k:<14} {v}")

    # ---- 提示与风险
    if res["warnings"]:
        add("")
        add("【提示】")
        for w in res["warnings"]:
            add(f"  · {w}")

    if res["errors"]:
        add("")
        add("【错误】")
        for e in res["errors"]:
            add(f"  · {e}")

    if res["risks"]:
        add("")
        add("【未建模的效应（仿真不覆盖，需实测确认）】")
        for r in res["risks"]:
            add(f"  · {r}")

    add("")
    add(_rule("═"))
    return "\n".join(out)
