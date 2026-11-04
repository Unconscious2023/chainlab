"""运行入口：读配置 → 建图 → 传播 → 采样 → 指标 → 观测。"""

from __future__ import annotations

import pathlib
import re

import numpy as np
import yaml

from .block import Ctx, num
from .spectral import Spectrum
from .graph import Graph, GraphError
from .metrics import analyze, noise_budget
from . import noisegen


def load(path):
    p = pathlib.Path(path)
    if not p.exists():
        raise GraphError(f"找不到配置文件：{p}")
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise GraphError(f"配置文件不是合法 YAML：{p}\n{exc}") from None
    return cfg, p.parent



def _spectrum_of(x, fs):
    """把一段时域样本转成谱表示，供指标计算使用。"""
    n = len(x)
    if n < 8:
        return Spectrum.empty()
    spec = np.fft.rfft(x) / n
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    coefs = 2.0 * spec
    coefs[0] = spec[0]
    keep = np.abs(coefs) > max(1e-15, np.abs(coefs).max() * 1e-9)
    keep[0] = True
    return Spectrum(freqs[keep], coefs[keep])


# 节点类型 → 中文名（提示文案用）
_CN = {
    "source": "信号源", "amp": "放大器", "filter": "滤波器", "add": "加法器",
    "mult": "乘法器", "noise": "白噪声源", "adc": "ADC", "dac": "DAC",
    "scope": "示波器", "specan": "频谱仪", "algo": "算法", "load": "负载",
    "tia": "跨阻放大器", "dcblock": "隔直电容", "integrator": "积分器",
    "vco": "压控振荡器", "pll": "锁相环",
}


def _cn(kind: str) -> str:
    return _CN.get(kind, kind)


def _hz(v: float) -> str:
    for scale, pre in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= scale * 0.999:
            return f"{v / scale:.4g} {pre}Hz"
    return f"{v:.4g} Hz"


def run(cfg: dict, base_dir=None) -> dict:
    meta = cfg.get("meta") or {}
    ctx = Ctx(cfg, base_dir)
    ctx.fs = num(meta.get("fs"), None)

    periods = num(meta.get("periods"), None)
    tw = meta.get("t_window")
    if isinstance(tw, str):
        m = re.match(r"\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*(.*)$", tw.strip())
        if m:
            val = float(m.group(1))
            unit = m.group(2).strip()
            # 单位认不出来时按"周期数"处理——这是实际最常用的写法
            if unit in ("", "周期", "cyc", "cycles") or not re.match(r"^[a-zµμ]+$", unit):
                periods = val
            else:
                ctx.t_seconds = num(tw, None)
        elif tw:
            ctx.t_seconds = num(tw, None)
    elif isinstance(tw, (int, float)):
        ctx.t_seconds = float(tw)
    ctx.periods = periods

    graph = Graph(cfg, ctx)
    primary = graph.adc
    if primary is None:
        raise GraphError("至少需要一个 ADC：它是模拟域与数字域的分界点")
    want = str(meta.get("metrics_adc") or "").strip()
    if want:
        node = graph.nodes.get(want)
        if node is None or not node.is_adc:
            raise GraphError(f"指标 ADC「{want}」不是本图里的 ADC")
        primary = node
    adc = primary.obj

    fs = adc.fs or ctx.fs
    if not fs:
        raise GraphError("未指定采样率：在 meta.fs 或 adc.fs 里给一个值")
    ctx.fs = fs

    graph.propagate()
    sig = graph.nodes[primary.id].sig
    f0, _ = sig.fundamental()

    if ctx.t_seconds:
        T = ctx.t_seconds
    elif ctx.periods and f0:
        T = ctx.periods / f0
    else:
        T = 10.0 / (f0 or 1e3)
        ctx.note(f"未指定时窗长度，按 {T * 1e3:.4g} ms 处理")

    # ADC 上给了采样点数就以它为准（后级算法需要固定长度）
    n_override = adc.num("n_samples", None)
    if n_override:
        n = max(int(n_override), 32)
        T = n / fs
    else:
        n = max(int(round(fs * T)), 32)

    def n_of(node, fs_i):
        """每个采样/更新节点按自己的速率出样本；显式 n_samples 优先。"""
        want = node.obj.num("n_samples", None)
        if want:
            return max(int(want), 32)
        return max(int(round(fs_i * T)), 32)

    def noise_model(node, fs_i):
        """折算到该 ADC 输入的噪声预算 + 用于合成有色噪声的函数。"""
        a_noise = node.obj.adc_noise()
        rows_i = noise_budget(graph, fs_i, to=node.id)["rows"]
        total_i = float(np.sqrt(sum(r["noise"] ** 2 for r in rows_i) + a_noise ** 2))

        def psd_i(f):
            f = np.asarray(f, dtype=float)
            transfer = graph.transfers(f, to=node.id)
            total = np.full(f.shape, a_noise ** 2 / max(fs_i / 2, 1e-9))
            for nd in graph.blocks():
                if nd.is_adc or nd.is_dac:
                    continue
                p = np.asarray(nd.obj.psd(f), dtype=float)
                if not np.any(p > 0):
                    continue
                t = transfer.get(nd.id)
                if t is not None:
                    total = total + p * np.abs(t) ** 2
            return total

        def make(npts):
            if total_i <= 0:
                return None
            y = noisegen.synthesize(psd_i, fs_i, npts, ctx.seed)
            s = float(np.std(y))
            return y * (total_i / s) if s > 1e-30 else None

        return a_noise, total_i, make

    captures: dict[str, tuple] = {}       # 节点 id → (码值/电压, meta)
    engine = str(meta.get("engine", "auto")).lower()
    td = engine == "time" or (engine != "analytic" and graph.needs_time_domain())

    if td:
        f_hi = sig.max_significant_f() or ((f0 or fs / 10.0) * 10.0)
        osr = graph.pick_osr(fs, f_hi)
        n_int = 2 * n * osr                      # 前半段当地址预热，丢掉滤波器起动瞬态
        x_full = graph.time_run_all(fs * osr, n_int)
        fs_int = fs * osr
        t_int = np.arange(n_int) / fs_int
        mode = f"时域（fs_int = {osr}×{fs / 1e3:.4g} kHz，{n_int} 点）"

        for node in graph.adcs:
            a_i = node.obj
            fs_i = a_i.fs or fs
            n_i = n_of(node, fs_i)
            if abs(fs_i - fs) <= 1e-9 * max(fs, 1.0):
                x_td = x_full[node.id][-n_i * osr:][::osr]
            else:
                # 多个 ADC 速率不同时，从内部时域波形按时间插值取点
                x_td = np.interp(np.arange(n_i) / fs_i, t_int, x_full[node.id])
            _, _, make_i = noise_model(node, fs_i)
            captures[node.id] = a_i.capture_array(x_td, limits=None, noise=make_i(n_i))

        # DAC 也按自己的更新率从内部波形取点（量化在 capture 里完成）
        for node in graph.dacs:
            d_i = node.obj
            fs_d = d_i.fs or fs
            n_d = n_of(node, fs_d)
            x_d = np.interp(np.arange(n_d) / fs_d, t_int, x_full[node.id])
            nd = getattr(d_i, "noise_density", 0.0) or 0.0
            captures[node.id] = d_i.capture_array(
                x_d, noise_v=nd * np.sqrt(max(fs_d / 2.0, 1e-9)))
        # 指标要从实际时域波形算，否则削顶产生的谐波看不见
        sig = _spectrum_of(x_full[primary.id][-n * osr:][::osr], fs)
    else:
        mode = "全解析（稳态）"
        for node in graph.adcs:
            a_i = node.obj
            fs_i = a_i.fs or fs
            n_i = n_of(node, fs_i)
            _, _, make_i = noise_model(node, fs_i)
            captures[node.id] = a_i.capture(
                node.sig, n_i, limits=graph.clip_bounds(), noise=make_i(n_i))
        for node in graph.dacs:
            d_i = node.obj
            fs_d = d_i.fs or fs
            n_d = n_of(node, fs_d)
            nd = getattr(d_i, "noise_density", 0.0) or 0.0
            captures[node.id] = d_i.capture(
                node.sig, n_d, noise_v=nd * np.sqrt(max(fs_d / 2.0, 1e-9)))

    codes, adc_meta = captures[primary.id]

    def sink_upstream(sink_node):
        """观测端在连线上的上游节点（观测端只允许一路输入）。"""
        if not sink_node.ins:
            return None, None
        e = sink_node.ins[0]
        return e.src, graph.nodes[e.src]

    def resolve_source(sink_node):
        """观测端的数据来源：显式 source 优先，否则取连线上游的 ADC/DAC。

        返回 (来源 id 或 None, 推导方式, 上游 id, 上游类型, 说明)。
        不再回落到「第一个 ADC」：多个 ADC/DAC 时那样会把所有示波器都指到
        同一个节点上，看起来有数据、其实观测点接错了。
        """
        want = str(sink_node.obj.params.get("source") or "").strip()
        up_id, up = sink_upstream(sink_node)
        up_type = up.type if up is not None else None
        if want:
            if want not in captures:
                raise GraphError(f"「{sink_node.id}」的数据来源「{want}」不可观测")
            note = "手动指定"
            if up_id and up_id != want:
                note += f"；连线上游为 {up_id}"
            return want, "manual", up_id, up_type, note
        if up is not None and (up.is_adc or up.is_dac):
            return up_id, "wire", up_id, up_type, "连线上游"
        return None, "none", up_id, up_type, ""

    for spec_node in (s for s in graph.sinks() if s.type == "specan"):
        src = resolve_source(spec_node)[0]
        if src:
            n_src = len(captures[src][0])
            ctx.note(
                f"频谱仪「{spec_node.id}」分析 {src} 的 {n_src} 点记录，分辨率 = fs / {n_src}"
            )
    metrics = analyze(graph, sig, adc_meta["adc_noise"], fs, adc=primary)

    warnings = list(adc_meta.get("warnings") or [])
    errors: list[str] = []

    # ---- 观测端：每个 sink 各算各的，按节点 id 存放
    sinks: dict[str, dict] = {}
    sink_info: list[dict] = []
    for node in graph.sinks():
        src, how, up_id, up_type, why = resolve_source(node)
        if src is None:
            if up_type:
                msg = (
                    f"「{node.id}」的上游是{_cn(up_type)}（{up_id}），不能直接观测："
                    f"请把数据来源设为某个 ADC/DAC，或把观测端接到 ADC/DAC 之后"
                )
            else:
                msg = f"「{node.id}」没有输入连线，无法确定数据来源"
            data = {"错误": msg}
            warnings.append(msg)
        else:
            data = node.obj.observe(*captures[src])
            data["_source_text"] = f"{src}（{why}）"
        data["_source"] = src
        data["_sink"] = node.id
        data["_kind"] = node.type
        data["_source_mode"] = how
        data["_upstream"] = up_id
        data["_upstream_type"] = up_type
        data["_source_note"] = why
        sinks[node.id] = data
        sink_info.append({
            "id": node.id, "type": node.type, "source": src,
            "source_mode": how, "upstream": up_id, "upstream_type": up_type,
            "note": why,
        })

    # 兼容旧调用方：某种观测端只有一个时，额外给一个以类型名为键的别名
    for node in graph.sinks():
        if sum(1 for s in graph.sinks() if s.type == node.type) == 1:
            sinks.setdefault(node.type, sinks[node.id])

    for node in graph.nodes.values():
        for e in node.outs:
            if e.div < 0.99:
                warnings.append(
                    f"{node.id} → {e.dst}：分压损失 {(1 - e.div) * 100:.1f}%，后级输入阻抗偏低"
                )

    for node in graph.blocks():
        for msg in node.obj.check():
            errors.append(msg)
        try:
            warnings.extend(node.obj.warnings())
        except Exception:  # noqa: BLE001
            pass
        d = node.diag
        if d.get("swing_exceeded"):
            warnings.append(
                f"{node.type}：输出峰值 {d['peak_out']:.4g} V 超过摆幅 "
                f"{d['v_swing']:.4g} V，会削顶（解析层只告警，波形削顶需时域层）"
            )
        bw = d.get("bw")
        hi = d.get("in_max_f")
        if bw and np.isfinite(bw) and hi and hi > bw:
            warnings.append(
                f"{node.type}：输入最高有效分量 {hi:.4g} Hz 超过本块带宽 {bw:.4g} Hz，会衰减"
            )
        lim_hi = node.obj.num("v_hi", None)
        lim_lo = node.obj.num("v_lo", None)
        peak = d.get("peak_out")
        if peak is not None and ((lim_hi is not None and peak > lim_hi)
                                 or (lim_lo is not None and -peak < lim_lo)):
            warnings.append(
                f"{node.id}（{node.type}）：输出峰值 {peak:.4g} V 超出限幅区间 "
                f"[{lim_lo if lim_lo is not None else '-∞'}, "
                f"{lim_hi if lim_hi is not None else '+∞'}]，波形会被削平"
            )

    # DAC 的零阶保持：没有重建滤波器时，镜像和 sinc 包络会原样出现在输出上
    for node in graph.dacs:
        d = node.obj
        fs_d = getattr(d, "fs", None)
        if not fs_d:
            continue
        if td and fs_d > fs_int * (1 + 1e-9):
            warnings.append(
                f"{node.id}（DAC）更新率 {_hz(fs_d)} 高于内部时域网格 "
                f"{_hz(fs_int)}：零阶保持只能按网格分辨率近似（输出仍按更新率采样）"
            )
        if graph.has_reconstruction(node):
            continue
        warnings.append(
            f"{node.id}（DAC，{d.name}）输出为零阶保持（ZOH），其后未接重建滤波器："
            f"频谱按 sinc(f/fs) 衰减，第一零点在 fs = {_hz(fs_d)}；"
            f"镜像分量位于 k·fs ± f，会直接出现在输出上（fs 不够高时落进带内）。"
            f"请在 DAC 之后加低通重建滤波器。"
        )

    # 自动补的 ADC 会在报错前先提示，否则用户会以为 ENOB 是真实器件的结果
    for _n in graph.adcs:
        if getattr(_n.obj, "auto_added", False):
            warnings.append(
                f"链路里没有 ADC，已自动补一个理想 ADC（无限位、无量化噪声）——"
                f"本次 ENOB/SNR 只反映模拟链路与量化前的噪声，不代表任何真实 ADC"
            )

    # 每次运行都列出「这次没建模的东西」，避免把结果当成全真
    checks = []
    for node in graph.blocks():
        if node.type == "filter" and getattr(node.obj, "en", 0):
            checks.append("滤波器只算了运放电压噪声，反馈网络电阻的热噪声未建模")
        if node.type == "amp" and not getattr(node.obj, "switch_sr", False):
            checks.append("压摆率 SR 未启用：大信号边沿不会变斜（勾选后走时域）")
        if getattr(node.obj, "is_current", False):
            if not getattr(node.obj, "c_par", 0):
                checks.append("电流源未给结电容：高频不会衰减（可填 结电容 参数）")
            else:
                checks.append("电流源结电容按一阶 RC 处理，未含引线电感与分布参数")
    if checks:
        for c in dict.fromkeys(checks):
            warnings.append(f"【未建模】{c}")

    # 分叉里的死支：连了但通不到任何 ADC/DAC 观测点
    obs_ids = {n.id for n in graph.adcs} | {n.id for n in graph.dacs}

    def reaches_observer(start: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur in obs_ids:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            for e in graph.nodes[cur].outs:
                stack.append(e.dst)
        return False

    for node in graph.blocks():
        if node.is_adc or node.is_dac or not node.outs:
            continue                            # 终端块（如负载）本来就不到观测点
        if not reaches_observer(node.id):
            warnings.append(
                f"节点「{node.id}」（{node.type}）没有连到 ADC/DAC，"
                f"它的输出不会体现在结果里"
            )

    # 输出电流能力：下一级的输入阻抗决定峰值电流
    for node in graph.blocks():
        i_max = node.obj.num("i_out_max", None)
        peak = node.diag.get("peak_out")
        if not i_max or peak is None or not node.outs:
            continue
        zin = min(graph.nodes[e.dst].obj.zin for e in node.outs)
        need = peak / max(zin, 1e-9)
        if need > i_max:
            warnings.append(
                f"{node.id}（{node.type}）：峰值电流 {need * 1e3:.1f} mA 超过器件上限 "
                f"{i_max * 1e3:.1f} mA（负载 {zin:g} Ω），波形会被压扁，需加缓冲器"
            )

    for node in list(graph.adcs) + list(graph.dacs):
        meta_i = captures[node.id][1]
        label = "ADC" if node.is_adc else "DAC"
        if meta_i.get("limit_clipped", 0) > 0:
            warnings.append(
                f"{node.id}（{label}）前级限幅削平了 "
                f"{meta_i['limit_clipped'] * 100:.2f}% 的样本"
                f"（解析层只在采样点近似；要看谐波请设限幅让引擎切到时域）"
            )
        if meta_i.get("clipped", 0) > 0:
            warnings.append(
                f"{node.id}（{label}）有 {meta_i['clipped'] * 100:.2f}% 的样本超出量程，"
                f"已按端点削平（码值 {meta_i.get('code_min')}~{meta_i.get('code_max')}）"
            )

    return {
        "name": meta.get("name") or "未命名链路",
        "mode": mode,
        "fs": fs,
        "n_samples": n,
        "t_seconds": T,
        "periods": (T * (f0 or 0)) if f0 else 0.0,
        "chain": graph.describe(),
        "budget": [{"kind": r["kind"], "id": r["id"], "noise": r["noise"],
                    "missing": r.get("missing", "")} for r in metrics["rows"]],
        "metrics": {k: v for k, v in metrics.items() if k != "rows"},
        "noise_total": metrics["noise_total"],
        "adc": adc_meta,
        "adc_id": primary.id,
        "adcs": [_summary(n, *captures[n.id]) for n in graph.adcs],
        "dacs": [_summary(n, *captures[n.id]) for n in graph.dacs],
        "sources": [n.id for n in graph.adcs] + [n.id for n in graph.dacs],
        "sinks": sinks,
        "sink_order": [n.id for n in graph.sinks()],
        "sink_info": sink_info,
        "warnings": warnings,
        "errors": errors,
        "risks": list(ctx.risks),
        "graph": {
            "nodes": [
                {"id": n.id, "type": n.type, "kind": _kind(n)} for n in graph.nodes.values()
            ],
            "edges": [
                {"from": e.src, "to": e.dst, "port": e.port}
                for n in graph.nodes.values()
                for e in n.outs
            ],
        },
    }


def _summary(node, codes, meta) -> dict:
    """单个 ADC/DAC 输出端的简要统计（不返回整段波形，避免 JSON 过大）。"""
    from ..blocks.sinks import volts

    v = np.asarray(volts(codes, meta), dtype=float)
    return {
        "id": node.id,
        "type": node.type,
        "name": meta.get("name"),
        "fs": float(meta.get("fs") or 0.0),
        "n_bits": meta.get("n_bits"),
        "n_samples": int(v.size),
        "rms": float(np.sqrt(np.mean((v - v.mean()) ** 2))) if v.size else 0.0,
        "peak": float(np.max(np.abs(v))) if v.size else 0.0,
        "clipped": float(meta.get("clipped") or 0.0),
        "ideal": bool(meta.get("ideal")),
        "zoh": bool(meta.get("zoh")),
        "update_fs": float(meta.get("update_fs") or meta.get("fs") or 0.0),
    }


def _kind(node) -> str:
    if node.obj.is_sink:
        return "sink"
    if node.obj.is_adc:
        return "adc"
    if node.obj.is_dac:
        return "dac"
    return "block"
