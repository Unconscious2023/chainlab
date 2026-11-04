"""界面用的模块与字段描述（中文标签 + 默认值 + 显示条件 + 端口数）。"""

from __future__ import annotations

import yaml

from ..core import devices

_DEV = devices.DEVICE_DIR

LIMIT_FIELDS = [
    {"key": "v_hi", "label": "输出上限", "type": "number", "default": "", "unit": "V"},
    {"key": "v_lo", "label": "输出下限", "type": "number", "default": "", "unit": "V"},
]


def _devices() -> dict:
    out = {}
    for p in sorted(_DEV.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        name = d.get("name") or p.stem
        cls = d.get("class")
        src = d.get("source", "")
        if isinstance(src, dict):
            src = "；".join(f"{k}：{v}" for k, v in src.items())
        meta = {"noise_density": d.get("noise_density"), "class": cls,
                "verified": d.get("verified", True),
                "note": d.get("note", ""), "part": d.get("part", ""),
                "source": src}
        if cls == "opamp":
            meta["opamp"] = d.get("opamp") or {}
        if cls in ("adc", "dac"):
            sec = d.get(cls) or {}
            opts = sec.get("n_bits_options") or [
                sec.get("n_bits_default", sec.get("bits", 12))]
            enob = {}
            for b, pb in (sec.get("per_bits") or {}).items():
                enob[str(b)] = (pb or {}).get("enob")
            meta["n_bits_options"] = opts
            meta["n_bits_default"] = sec.get("n_bits_default", opts[0])
            meta["enob_by_bits"] = enob
            meta["differential"] = bool(sec.get("differential", False))
            meta["bits"] = sec.get("bits")
            meta["fs_max"] = sec.get("fs_max")
            meta["settling"] = sec.get("settling")
            vref = sec.get("vref")
            if vref is not None and not isinstance(vref, (list, tuple)):
                try:
                    meta["vref_single"] = float(vref)
                except (TypeError, ValueError):
                    meta["vref_single"] = None
            if cls == "dac":
                meta["i_out_fs"] = sec.get("i_out_fs")
        out[name] = meta
    return out


def schema() -> dict:
    devs = _devices()
    opamps = [[n, n] for n, m in devs.items() if m["class"] == "opamp"]
    adcs = [[n, n] for n, m in devs.items() if m["class"] == "adc"]
    dacs = [[n, n] for n, m in devs.items() if m["class"] == "dac"]
    muls = [[n, n] for n, m in devs.items() if m["class"] == "multiplier"]

    return {
        "meta_defaults": {"name": "我的链路", "fs": "100k", "periods": "10"},
        "palette_order": ["source", "amp", "filter", "add", "adc", "dac",
                          "mult", "dcblock", "integrator", "tia",
                          "vco", "pll", "load"],
        "sink_order": ["scope", "specan", "algo"],
        "device_meta": devs,
        "blocks": {
            "source": {
                "label": "信号源", "glyph": "∿", "n_in": 0, "n_out": 1,
                "fields": [
                    {"key": "mode", "label": "源类型", "type": "select", "default": "voltage",
                     "options": [["voltage", "电压源"], ["current", "电流源"]]},
                    {"key": "type", "label": "波形", "type": "select", "default": "sine",
                     "options": [["sine", "正弦"], ["square", "方波"], ["triangle", "三角波"],
                                 ["dc", "直流"], ["am", "调幅"], ["fm", "调频"],
                                 ["noise", "白噪声"]]},
                    {"key": "psd", "label": "噪声谱密度", "type": "number", "default": "20n",
                     "unit": "V/√Hz", "show_if": {"key": "type", "value": ["noise"]}},
                    {"key": "rms", "label": "或直接给 RMS", "type": "number", "default": "",
                     "show_if": {"key": "type", "value": ["noise"]}},
                    {"key": "noise_f_lo", "label": "下限频率（留空 = 0）", "type": "number",
                     "default": "", "unit": "Hz", "show_if": {"key": "type", "value": ["noise"]}},
                    {"key": "noise_f_hi", "label": "上限频率（留空 = 满带宽）", "type": "number",
                     "default": "", "unit": "Hz", "show_if": {"key": "type", "value": ["noise"]}},
                    {"key": "f", "label": "频率", "type": "number", "default": "1k", "unit": "Hz",
                     "show_if": {"key": "type", "value": ["sine", "square", "triangle", "am", "fm"]}},
                    {"key": "amp", "label": "幅度", "type": "number", "default": "100m", "unit": "Vpk",
                     "show_if": {"key": "type", "value": ["sine", "square", "triangle", "am", "fm"]}},
                    {"key": "phase", "label": "相位", "type": "number", "default": "0", "unit": "°",
                     "show_if": {"key": "type", "value": ["sine", "square", "triangle", "am", "fm"]}},
                    {"key": "offset", "label": "直流偏置 / 电平", "type": "number", "default": "0",
                     "unit": "V",
                     "show_if": {"key": "type",
                                 "value": ["sine", "square", "triangle", "dc", "am", "fm"]}},
                    {"key": "fm", "label": "调制频率", "type": "number", "default": "100",
                     "unit": "Hz", "show_if": {"key": "type", "value": ["am", "fm"]}},
                    {"key": "depth", "label": "调制度", "type": "number", "default": "0.5",
                     "show_if": {"key": "type", "value": ["am"]}},
                    {"key": "beta", "label": "调制指数 β", "type": "number", "default": "1",
                     "show_if": {"key": "type", "value": ["fm"]}},
                    {"key": "n_harm", "label": "谐波数", "type": "number", "default": "15",
                     "show_if": {"key": "type", "value": ["square", "triangle"]}},
                    {"key": "zout", "label": "输出阻抗", "type": "number", "default": "0", "unit": "Ω"},
                    {"key": "c_par", "label": "结电容（电流源）", "type": "number", "default": "",
                     "unit": "F", "show_if": {"key": "mode", "value": "current"}},
                ],
            },
            "amp": {
                "label": "放大器", "glyph": "▷", "n_in": 1, "n_out": 1,
                "fields": [
                    {"key": "device", "label": "器件", "type": "select", "default": "OPA656",
                     "options": [["", "自定义"]] + opamps},
                    {"key": "G", "label": "增益 G", "type": "number", "default": "2"},
                    {"key": "gbw", "label": "增益带宽积 GBW", "type": "number", "default": "230M", "unit": "Hz"},
                    {"key": "en", "label": "输入噪声密度 en", "type": "number", "default": "7n",
                     "unit": "V/√Hz"},
                    {"key": "in", "label": "电流噪声密度 in", "type": "number", "default": "",
                     "unit": "A/√Hz"},
                    {"key": "v_swing", "label": "输出摆幅", "type": "number", "default": "3.3", "unit": "V"},
                    {"key": "i_out_max", "label": "最大输出电流", "type": "number", "default": "70m",
                     "unit": "A"},
                    {"key": "g_min", "label": "最小稳定增益", "type": "number", "default": "1"},
                    {"key": "sr", "label": "压摆率 SR", "type": "number", "default": "2.3",
                     "unit": "V/µs"},
                    {"key": "use_sr", "label": "启用压摆率限制（走时域）", "type": "bool",
                     "default": False},
                    {"key": "zin", "label": "输入阻抗", "type": "number", "default": "1e6", "unit": "Ω"},
                    {"key": "zout", "label": "输出阻抗", "type": "number", "default": "0.1", "unit": "Ω"},
                ] + LIMIT_FIELDS,
            },
            "filter": {
                "label": "滤波器", "glyph": "⌁", "n_in": 1, "n_out": 1, "designer": True,
                "fields": [
                    {"key": "device", "label": "运放（用于噪声）", "type": "select",
                     "default": "", "options": [["", "理想（无噪声）"], ["__custom__", "自定义"]] + opamps},
                    {"key": "en", "label": "输入噪声密度 en", "type": "number",
                     "default": "", "unit": "V/√Hz"},
                    {"key": "in", "label": "电流噪声密度 in", "type": "number",
                     "default": "", "unit": "A/√Hz"},
                    {"key": "mode", "label": "输入方式", "type": "select", "default": "design",
                     "options": [["design", "按参数设计"], ["tf", "传递函数"],
                                 ["netlist", "按元件值"]]},
                    {"key": "ztopo", "label": "拓扑（按元件值）", "type": "select", "default": "sk",
                     "options": [["sk", "Sallen-Key"], ["mfb", "MFB"]],
                     "show_if": {"key": "mode", "value": "netlist"}},
                    {"key": "z1", "label": "Z1", "type": "text", "default": "",
                     "modal": True,
                     "show_if": {"key": "mode", "value": "netlist"}},
                    {"key": "z2", "label": "Z2", "type": "text", "default": "",
                     "modal": True,
                     "show_if": {"key": "mode", "value": "netlist"}},
                    {"key": "z3", "label": "Z3", "type": "text", "default": "",
                     "modal": True,
                     "show_if": {"key": "mode", "value": "netlist"}},
                    {"key": "z4", "label": "Z4", "type": "text", "default": "",
                     "modal": True,
                     "show_if": {"key": "mode", "value": "netlist"}},
                    {"key": "z5", "label": "Z5（MFB）", "type": "text", "default": "",
                     "modal": True, "show_if": {"key": "mode", "value": "netlist"}},
                    {"key": "type", "label": "类型", "type": "select", "default": "lowpass",
                     "options": [["lowpass", "低通"], ["highpass", "高通"], ["bandpass", "带通"]],
                     "modal": True},
                    {"key": "response", "label": "响应", "type": "select", "default": "butterworth",
                     "options": [["butterworth", "巴特沃斯"], ["chebyshev1", "切比雪夫 I"],
                                 ["chebyshev2", "切比雪夫 II"], ["bessel", "贝塞尔"]],
                     "modal": True},
                    {"key": "order", "label": "阶数", "type": "select", "default": "2",
                     "options": [[str(i), f"{i} 阶"] for i in range(1, 9)],
                     "modal": True},
                    {"key": "ripple_db", "label": "通带纹波", "type": "number", "default": "0.5",
                     "unit": "dB", "modal": True},
                    {"key": "atten_db", "label": "阻带衰减", "type": "number", "default": "40",
                     "unit": "dB", "modal": True},
                    {"key": "fc", "label": "截止频率 fc", "type": "number", "default": "50k",
                     "unit": "Hz", "modal": True},
                    {"key": "f_lo", "label": "下限截止频率", "type": "number", "default": "",
                     "unit": "Hz", "modal": True},
                    {"key": "f_hi", "label": "上限截止频率", "type": "number", "default": "",
                     "unit": "Hz", "modal": True},
                    {"key": "Q", "label": "品质因数 Q", "type": "number", "default": "0.7071",
                     "modal": True},
                    {"key": "num", "label": "分子系数（s 域）", "type": "list", "default": "1",
                     "show_if": {"key": "mode", "value": ["tf"]}},
                    {"key": "den", "label": "分母系数（s 域）", "type": "list", "default": "1, 1",
                     "show_if": {"key": "mode", "value": ["tf"]}},
                    {"key": "zin", "label": "输入阻抗", "type": "number", "default": "1e6", "unit": "Ω"},
                    {"key": "zout", "label": "输出阻抗", "type": "number", "default": "10", "unit": "Ω"},
                ] + LIMIT_FIELDS,
            },
            "add": {
                "label": "加法器", "glyph": "⊕", "n_in": None, "n_out": 1, "summer": True,
                "fields": [
                    {"key": "n_in", "label": "端口数", "type": "number", "default": "2"},
                    {"key": "device", "label": "运放", "type": "select", "default": "",
                     "options": [["", "理想（无噪声）"], ["__custom__", "自定义"]] + opamps},
                    {"key": "en", "label": "输入噪声密度 en", "type": "number", "default": "",
                     "unit": "V/√Hz"},
                    {"key": "in", "label": "电流噪声密度 in", "type": "number", "default": "",
                     "unit": "A/√Hz"},
                    {"key": "zin", "label": "输入阻抗", "type": "number", "default": "1e6", "unit": "Ω"},
                    {"key": "dc", "label": "直流偏置", "type": "number", "default": "1.65", "unit": "V"},
                    {"key": "v_swing", "label": "输出摆幅", "type": "number", "default": "3.3", "unit": "V"},
                    {"key": "i_out_max", "label": "最大输出电流", "type": "number", "default": "20m",
                     "unit": "A"},
                    {"key": "zout", "label": "输出阻抗", "type": "number", "default": "10", "unit": "Ω"},
                ] + LIMIT_FIELDS,
            },
            "mult": {
                "label": "乘法器", "glyph": "×", "n_in": 2, "n_out": 1,
                "formula": "y = (x₁ + x₁os)(x₂ + x₂os) / U + z",
                "fields": [
                    {"key": "device", "label": "器件", "type": "select", "default": "AD835",
                     "options": [["", "自定义"]] + muls},
                    {"key": "U", "label": "标度 U", "type": "number", "default": "1", "unit": "V"},
                    {"key": "x1os", "label": "X 路馈通", "type": "number", "default": "0", "unit": "V"},
                    {"key": "x2os", "label": "Y 路馈通", "type": "number", "default": "0", "unit": "V"},
                    {"key": "z", "label": "Z 求和输入端", "type": "number", "default": "0", "unit": "V"},
                    {"key": "en", "label": "输出噪声密度", "type": "number", "default": "",
                     "unit": "V/√Hz", "hint": "留空则用器件手册值（AD835 50n，AD633 447n）"},
                ] + LIMIT_FIELDS,
            },
            "noise": {
                "label": "白噪声", "glyph": "≈", "n_in": 1, "n_out": 1,
                "fields": [
                    {"key": "psd", "label": "噪声谱密度", "type": "number", "default": "20n",
                     "unit": "V/√Hz"},
                    {"key": "rms", "label": "或直接给 RMS（覆盖上面的谱密度）", "type": "number",
                     "default": ""},
                    {"key": "f_lo", "label": "下限频率（留空 = 0）", "type": "number", "default": "",
                     "unit": "Hz"},
                    {"key": "f_hi", "label": "上限频率（留空 = 满带宽）", "type": "number", "default": "",
                     "unit": "Hz"},
                ],
            },
            "adc": {
                "label": "ADC", "glyph": "▮", "n_in": 1, "n_out": 1,
                "fields": [
                    {"key": "device", "label": "型号", "type": "select", "default": "STM32F407",
                     "options": [["", "自定义"]] + adcs},
                    {"key": "n_bits", "label": "位数", "type": "dynamic_select", "default": ""},
                    {"key": "vref", "label": "参考电压", "type": "number", "default": "3.3", "unit": "V"},
                    {"key": "r_in", "label": "ADC 输入电阻（高速 ADC 常为 50 Ω）", "type": "number",
                     "default": "", "unit": "Ω"},
                    {"key": "input_mode", "label": "输入方式", "type": "select", "default": "single",
                     "options": [["single", "单端"], ["differential", "差分"]]},
                    {"key": "sample_mode", "label": "采样周期", "type": "select", "default": "solve",
                     "options": [["solve", "按源阻抗反算"], ["cycles", "手填周期数"]]},
                    {"key": "rain", "label": "源阻抗 R_AIN（留空＝继承前级）", "type": "number",
                     "default": "", "unit": "Ω",
                     "show_if": {"key": "sample_mode", "value": ["solve"]}},
                    {"key": "cycles", "label": "采样周期数", "type": "number", "default": "28",
                     "show_if": {"key": "sample_mode", "value": ["cycles"]}},
                    {"key": "n_samples", "label": "采样点数（给后级算法）", "type": "number",
                     "default": "4096"},
                    {"key": "enob", "label": "有效位数 ENOB（按型号自动）", "type": "number", "default": ""},
                ],
            },
            "dac": {
                "label": "DAC", "glyph": "▯", "n_in": 1, "n_out": 1,
                "formula": "每 Ts = 1/fs 锁存一次码值并保持到下一次更新（零阶保持）；"
                           "频谱按 sinc(f/fs) 衰减，镜像位于 k·fs ± f",
                "fields": [
                    {"key": "device", "label": "型号", "type": "select", "default": "AD9764",
                     "options": [["", "自定义"]] + dacs},
                    {"key": "n_bits", "label": "位数", "type": "dynamic_select", "default": ""},
                    {"key": "vref", "label": "参考电压（留空＝按器件）", "type": "number",
                     "default": "", "unit": "V"},
                    {"key": "fs", "label": "更新率 fs（留空＝器件手册上限）", "type": "number",
                     "default": "", "unit": "Hz"},
                    {"key": "settling", "label": "建立时间（留空＝按器件）", "type": "number",
                     "default": "", "unit": "s"},
                    {"key": "output_mode", "label": "输出方式", "type": "select", "default": "",
                     "options": [["", "按器件默认"], ["single", "单端"],
                                 ["differential", "差分"]]},
                    {"key": "r_load", "label": "负载电阻（电流输出型）", "type": "number",
                     "default": "50", "unit": "Ω"},
                    {"key": "vfs", "label": "满量程跨度（留空＝按器件与负载）", "type": "number",
                     "default": "", "unit": "V"},
                    {"key": "noise_density", "label": "输出噪声密度（留空＝按器件）",
                     "type": "number", "default": "", "unit": "V/√Hz"},
                ] + LIMIT_FIELDS,
            },
            "tia": {
                "label": "跨阻放大", "glyph": "I→V", "n_in": 1, "n_out": 1,
                "formula": "V_out = −I_in·Rf，虚地输入",
                "fields": [
                    {"key": "device", "label": "运放", "type": "select", "default": "OPA656",
                     "options": [["", "理想（无噪声）"], ["__custom__", "自定义"]] + opamps},
                    {"key": "en", "label": "输入噪声密度 en", "type": "number", "default": "",
                     "unit": "V/√Hz"},
                    {"key": "in", "label": "电流噪声密度 in", "type": "number", "default": "",
                     "unit": "A/√Hz"},
                    {"key": "rf", "label": "反馈电阻 Rf", "type": "number", "default": "100k",
                     "unit": "Ω"},
                    {"key": "cf", "label": "反馈电容 Cf", "type": "number", "default": "1p",
                     "unit": "F"},
                    {"key": "cin", "label": "输入总电容 Cin", "type": "number", "default": "5p",
                     "unit": "F"},
                    {"key": "gbw", "label": "运放 GBW", "type": "number", "default": "230M",
                     "unit": "Hz"},
                ] + LIMIT_FIELDS,
            },
            "dcblock": {
                "label": "隔直电容", "glyph": "⊣⊢", "n_in": 1, "n_out": 1,
                "formula": "H(s) = sRC/(1 + sRC)，一阶高通",
                "fields": [
                    {"key": "c", "label": "C", "type": "number", "default": "100n", "unit": "F"},
                    {"key": "r", "label": "对地电阻 R（留空 = 无穷大 / 理想隔直）",
                     "type": "number", "default": "1e8", "unit": "Ω"},
                ],
            },
            "integrator": {
                "label": "积分器", "glyph": "∫", "n_in": 1, "n_out": 1,
                "formula": "H(s) = −1/(s·R·C)",
                "fields": [
                    {"key": "r", "label": "R", "type": "number", "default": "10k", "unit": "Ω"},
                    {"key": "c", "label": "C", "type": "number", "default": "10n", "unit": "F"},
                ] + LIMIT_FIELDS,
            },
            "vco": {
                "label": "压控振荡器", "glyph": "∿", "n_in": 1, "n_out": 1,
                "formula": "f(t) = f0 + Kv·v(t)，输出 sin(2π∫f dt)",
                "fields": [
                    {"key": "f0", "label": "自由振荡频率 f0", "type": "number", "default": "10k",
                     "unit": "Hz"},
                    {"key": "kv", "label": "灵敏度 Kv", "type": "number", "default": "10k",
                     "unit": "Hz/V"},
                    {"key": "amp", "label": "输出幅度", "type": "number", "default": "500m",
                     "unit": "Vpk"},
                    {"key": "phase", "label": "初相", "type": "number", "default": "0", "unit": "°"},
                ] + LIMIT_FIELDS,
            },
            "pll": {
                "label": "锁相环", "glyph": "⚙", "n_in": 1, "n_out": 1,
                "formula": "鉴相 Kd → 环路滤波 F(s) → VCO Kv/s，内部闭环",
                "fields": [
                    {"key": "kd", "label": "鉴相增益 Kd", "type": "number", "default": "1",
                     "unit": "V/rad"},
                    {"key": "kv", "label": "VCO 灵敏度 Kv", "type": "number", "default": "10k",
                     "unit": "Hz/V"},
                    {"key": "f0", "label": "VCO 自由振荡 f0", "type": "number", "default": "10k",
                     "unit": "Hz"},
                    {"key": "n_div", "label": "分频比 N", "type": "number", "default": "1"},
                    {"key": "r1", "label": "环路滤波 R1", "type": "number", "default": "1k",
                     "unit": "Ω"},
                    {"key": "r2", "label": "环路滤波 R2", "type": "number", "default": "1k",
                     "unit": "Ω"},
                    {"key": "c", "label": "环路滤波 C", "type": "number", "default": "100n",
                     "unit": "F"},
                    {"key": "v_max", "label": "VCO 调谐电压上限", "type": "number", "default": "5",
                     "unit": "V"},
                    {"key": "amp", "label": "输出幅度", "type": "number", "default": "500m",
                     "unit": "Vpk"},
                ] + LIMIT_FIELDS,
            },
            "load": {
                "label": "负载", "glyph": "⏚", "n_in": 1, "n_out": 0,
                "fields": [
                    {"key": "r", "label": "负载电阻", "type": "number", "default": "50", "unit": "Ω"},
                ],
            },
            "scope": {
                "label": "示波器", "glyph": "▤", "n_in": 1, "n_out": 0,
                "fields": [
                    {"key": "source", "label": "数据来源", "type": "node_source",
                     "default": ""},
                    {"key": "t_start", "label": "起始时间", "type": "number", "default": "0", "unit": "s"},
                    {"key": "t_end", "label": "终止时间", "type": "number", "default": "", "unit": "s"},
                    {"key": "n_points", "label": "显示点数", "type": "number", "default": "1200"},
                ],
            },
            "specan": {
                "label": "频谱仪", "glyph": "▥", "n_in": 1, "n_out": 0,
                "fields": [
                    {"key": "source", "label": "数据来源", "type": "node_source",
                     "default": ""},
                    {"key": "n_fft", "label": "FFT 点数", "type": "select", "default": "65536",
                     "options": [["1024", "1024"], ["4096", "4096"], ["16384", "16384"],
                                 ["65536", "65536"], ["262144", "262144"]]},
                    {"key": "window", "label": "窗函数", "type": "select", "default": "hann",
                     "options": [["hann", "Hann"], ["blackman", "Blackman"], ["none", "不加窗"]]},
                ],
            },
            "algo": {
                "label": "算法", "glyph": "ƒ", "n_in": 1, "n_out": 0,
                "fields": [
                    {"key": "path", "label": "Python 文件", "type": "text",
                     "default": "./algo/peak_detect.py"},
                ],
            },
        },
    }
