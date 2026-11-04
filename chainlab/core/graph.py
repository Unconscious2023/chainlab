"""数据流图：节点 + 连线，支持分叉（一个源接多个块）与汇合（多路求和）。

单链只是图的一个特例，旧配置里的 `chain:` 会被自动转成图。
"""

from __future__ import annotations

import numpy as np

from .block import Ctx, get_class
from .spectral import Spectrum


class GraphError(ValueError):
    """配置错误。工具应当拒绝执行，而不是给一个看起来能用的结果。"""


# 旧名字，保持向后兼容
ChainError = GraphError


class Edge:
    __slots__ = ("src", "dst", "div", "port")

    def __init__(self, src: str, dst: str, port: int = 0):
        self.src, self.dst, self.port = src, dst, port
        self.div = 1.0


class Node:
    __slots__ = ("id", "type", "obj", "ins", "outs", "diag", "sig")

    def __init__(self, nid: str, ntype: str, obj):
        self.id, self.type, self.obj = nid, ntype, obj
        self.ins: list[Edge] = []
        self.outs: list[Edge] = []
        self.diag: dict = {}
        self.sig: Spectrum | None = None

    @property
    def is_sink(self):
        return self.obj.is_sink

    @property
    def is_adc(self):
        return self.obj.is_adc

    @property
    def is_dac(self):
        return self.obj.is_dac


class Graph:
    def __init__(self, cfg: dict, ctx: Ctx):
        self.ctx = ctx
        self.nodes: dict[str, Node] = {}
        self.order: list[str] = []
        # 可以有多个 ADC/DAC；adc 保留为「第一个 ADC」，兼容旧代码与默认指标
        self.adcs: list[Node] = []
        self.dacs: list[Node] = []
        self.adc: Node | None = None
        self.notes: list[str] = []

        raw_nodes, raw_edges = _parse(cfg)

        for nid, ntype, params in raw_nodes:
            if nid in self.nodes:
                raise GraphError(f"节点 id 重复：{nid}")
            cls = get_class(ntype)
            obj = cls(params or {}, ctx)
            self.nodes[nid] = Node(nid, ntype, obj)
            if getattr(cls, "is_adc", False):
                self.adcs.append(self.nodes[nid])
            if getattr(cls, "is_dac", False):
                self.dacs.append(self.nodes[nid])
        self.adc = self.adcs[0] if self.adcs else None

        for src, dst, port in raw_edges:
            for end in (src, dst):
                if end not in self.nodes:
                    raise GraphError(f"连线引用了不存在的节点：{end}")
            e = Edge(src, dst, port)
            self.nodes[src].outs.append(e)
            self.nodes[dst].ins.append(e)

        self._ensure_adc(ctx)
        self._ensure_sink(ctx)

        self._validate()
        self.order = self._topo()
        self._dividers()

    # ------------------------------------------------------------ 自动补齐
    def _ensure_adc(self, ctx):
        if self.adcs:
            for adc in self.adcs:
                if not adc.ins:
                    raise GraphError(f"ADC「{adc.id}」没有任何输入")
            return
        from ..blocks.adc import IdealADC

        tails = [
            n for n in self.nodes.values()
            if not n.is_sink and not any(not self.nodes[e.dst].is_sink for e in n.outs)
        ]
        multi_dac = False
        if len(tails) != 1:
            # 多个末端都是 DAC 时不算歧义：DAC 本身就是可观测点，指标用其中
            # 第一个并联一个理想 ADC 来算。
            dac_tails = [n for n in tails if n.is_dac]
            if dac_tails and len(dac_tails) == len(tails):
                tail = dac_tails[0]
                multi_dac = True
            else:
                raise GraphError(
                    "找不到唯一可以接 ADC 的末端节点，请显式画一个 ADC 并连上"
                )
        else:
            tail = tails[0]
        nid = _fresh_id(self.nodes, "adc")
        node = Node(nid, "adc", IdealADC({}, ctx))
        node.obj.auto_added = True      # 用于结果里提示「这不是真实 ADC」
        self.nodes[nid] = node
        if tail.is_dac:
            # 末端已经是 DAC：观测端本来就可以直接观测 DAC，别把它拆下来。
            # 理想 ADC 只并联在 DAC 上，用来算指标。
            ne = Edge(tail.id, nid, 0)
            tail.outs.append(ne)
            node.ins.append(ne)
            extra = (f"末端有多个 DAC，指标按 {tail.id} 计算；" if multi_dac else "")
            ctx.note(
                "链路未指定 ADC，已自动补一个理想 ADC（无限位、无量化噪声）；"
                f"{extra}该 ADC 并联在 DAC 输出上，观测端仍按连线观测 DAC"
            )
            self.adcs.append(node)
            self.adc = node
            self._auto_sink_src = tail.id
            return
        # 末端原先直连观测端的边，改由 ADC 发出
        for e in list(tail.outs):
            if self.nodes[e.dst].is_sink:
                tail.outs.remove(e)
                self.nodes[e.dst].ins.remove(e)
                ne = Edge(nid, e.dst, e.port)
                node.outs.append(ne)
                self.nodes[e.dst].ins.append(ne)
        ne = Edge(tail.id, nid, 0)
        tail.outs.append(ne)
        node.ins.append(ne)
        self.adcs.append(node)
        self.adc = node
        ctx.note("链路未指定 ADC，已自动补一个理想 ADC（无限位、无量化噪声）")

    def _ensure_sink(self, ctx):
        if any(n.is_sink for n in self.nodes.values()):
            return
        from ..blocks.sinks import Scope

        nid = _fresh_id(self.nodes, "scope")
        node = Node(nid, "scope", Scope({}, ctx))
        self.nodes[nid] = node
        # 末端是 DAC 时，自动补的观测端直接看 DAC（就是用户看到的那个输出）
        src = getattr(self, "_auto_sink_src", None) or self.adc.id
        e = Edge(src, nid, 0)
        self.nodes[src].outs.append(e)
        node.ins.append(e)
        ctx.note("未指定观测端，已自动加一个示波器")

    # ------------------------------------------------------------ 校验
    def _check_current_source(self):
        for n in self.nodes.values():
            if not getattr(n.obj, "is_current", False):
                continue
            if not n.outs:
                continue
            # 只要同一节点上有一条支路接了负载/TIA，电流就已经转成电压，
            # 其余支路（放大器、滤波器…）都是合法的
            kinds = {self.nodes[e.dst].type for e in n.outs}
            if kinds & {"tia", "load"}:
                continue
            bad = sorted(kinds)[0]
            raise GraphError(
                f"电流源「{n.id}」接到了 {bad}：电流源必须接跨阻放大器（TIA）"
                f"或负载电阻，否则 I×Z 会把后级直接打饱和"
            )

    def _validate(self):
        self._check_current_source()
        for n in self.nodes.values():
            nin = getattr(n.obj, "n_in", 1)
            nout = getattr(n.obj, "n_out", 1)
            if n.obj.is_sink:
                nin = 1
            got = len(n.ins)
            if nin is None:
                if got < 1:
                    raise GraphError(f"「{n.type}」至少需要一路输入")
            elif got != nin:
                raise GraphError(
                    f"「{n.type}」需要 {nin} 路输入，实际接了 {got} 路"
                )
            if nout == 0 and n.outs:
                raise GraphError(f"「{n.type}」是终点，不能往外连")
            if n.is_adc and any(not self.nodes[e.dst].is_sink for e in n.outs):
                raise GraphError("ADC 之后只能接观测端")
        # 观测端可以指定看哪个 ADC/DAC
        for n in self.nodes.values():
            if not n.is_sink:
                continue
            want = str(n.obj.params.get("source") or "").strip()
            if not want:
                continue
            if want not in self.nodes:
                raise GraphError(f"「{n.id}」指定的数据来源「{want}」不存在")
            if not (self.nodes[want].is_adc or self.nodes[want].is_dac):
                raise GraphError(
                    f"「{n.id}」的数据来源「{want}」不是 ADC 或 DAC，不能作为观测点"
                )

    def _topo(self) -> list[str]:
        indeg = {nid: len(n.ins) for nid, n in self.nodes.items()}
        queue = [nid for nid, d in indeg.items() if d == 0]
        out: list[str] = []
        while queue:
            nid = queue.pop(0)
            out.append(nid)
            for e in self.nodes[nid].outs:
                indeg[e.dst] -= 1
                if indeg[e.dst] == 0:
                    queue.append(e.dst)
        if len(out) != len(self.nodes):
            raise GraphError("图里有环：信号绕回去了")
        return out

    def _dividers(self):
        """按「并联负载」算分压：多条支路会一起把前级拉低。"""
        for n in self.nodes.values():
            if not n.outs:
                continue
            if getattr(n.obj, "is_current", False):
                for e in n.outs:      # 电流源输出已按负载换算，不再分压
                    e.div = 1.0
                continue
            zout = max(n.obj.zout, 0.0)
            inv = 0.0
            for e in n.outs:
                inv += 1.0 / max(self.nodes[e.dst].obj.zin, 1e-9)
            zpar = 1.0 / inv if inv > 0 else float("inf")
            k = zpar / (zout + zpar) if (zout + zpar) > 0 else 1.0
            for e in n.outs:
                e.div = k
        # ADC 看到的是它那一路的源阻抗（多个 ADC 各自取自己的输入支路）
        for adc in self.adcs:
            for e in adc.ins:
                adc.obj.source_impedance = self.nodes[e.src].obj.zout
                break

    # ------------------------------------------------------------ 传播
    def propagate(self):
        for nid in self.order:
            n = self.nodes[nid]
            if n.is_sink:
                continue
            inputs = [self.nodes[e.src].sig.scaled(e.div) for e in sorted(n.ins, key=lambda e: e.port)]
            if n.type == "add":
                n.obj._n_in = len(inputs)
            out, diag = n.obj.process(inputs)
            if getattr(n.obj, "is_current", False):
                # 电流源：幅度是电流，按负载阻抗换成电压
                z = self.load_impedance(n)
                out = out.scaled(z)
                cpar = getattr(n.obj, "c_par", 0.0) or 0.0
                if cpar > 0 and z > 0:
                    fc = 1.0 / (2 * np.pi * z * cpar)
                    out = out.filtered(lambda f: 1.0 / (1 + 1j * np.asarray(f) / fc))
            n.diag = dict(diag or {})
            n.sig = out
        return self.adc.sig

    # ------------------------------------------------------------ 到 ADC 的传递
    def transfers(self, f, to: str | None = None):
        """每个节点「输出 → 指定 ADC 输入」的复频响（分叉汇合时按路径相干相加）。

        to 默认取第一个 ADC；多 ADC 时每个 ADC 的噪声预算各算各的。
        """
        f = np.asarray(f, dtype=float)
        target = to or (self.adc.id if self.adc is not None else None)
        acc: dict[str, np.ndarray] = {}
        for nid in reversed(self.order):
            n = self.nodes[nid]
            if n.is_adc:
                acc[nid] = (np.ones(f.shape, dtype=complex) if nid == target
                            else np.zeros(f.shape, dtype=complex))
                continue
            if n.is_sink:
                continue
            total = np.zeros(f.shape, dtype=complex)
            for e in n.outs:
                dst = self.nodes[e.dst]
                if dst.is_sink:
                    continue
                h = dst.obj.H(f) if not dst.is_adc else 1.0
                pg = dst.obj.port_gain(e.port, len(dst.ins))
                total = total + e.div * pg * h * acc.get(dst.id, 0.0)
            acc[nid] = total
        return acc

    # ------------------------------------------------------------ 阻抗
    def load_impedance(self, node) -> float:
        """节点输出端看到的负载阻抗（各路 zin 并联）。"""
        if not node.outs:
            return 1e12
        inv = sum(1.0 / max(self.nodes[e.dst].obj.zin, 1e-9) for e in node.outs)
        return 1.0 / inv if inv > 0 else 1e12

    def source_impedance(self) -> dict:
        """每条边接收端看到的等效源阻抗（戴维南）。

        返回 {(src, dst, port): Z}。Z = 前级 zout ∥ 同节点其他支路的 zin。
        电流噪声 in·Z 和热噪声 4kTR 都从这里取。
        """
        out = {}
        for n in self.nodes.values():
            if not n.outs:
                continue
            zout = max(n.obj.zout, 0.0)
            if zout <= 0:
                for e in n.outs:                     # 理想电压源，节点是刚性的
                    out[(e.src, e.dst, e.port)] = 0.0
                continue
            for e in n.outs:
                inv = 1.0 / zout
                for o in n.outs:
                    if o is e:
                        continue
                    inv += 1.0 / max(self.nodes[o.dst].obj.zin, 1e-9)
                out[(e.src, e.dst, e.port)] = 1.0 / inv if inv > 0 else float("inf")
        return out

    # ------------------------------------------------------------ 时域
    def needs_time_domain(self) -> bool:
        """有硬非线性（限幅削顶等）就必须走时域。"""
        for n in self.nodes.values():
            if n.is_sink or n.is_adc:
                continue
            if getattr(n.obj, "force_time", False):
                return True
            if n.obj.is_nonlinear:
                return True
        return False

    @staticmethod
    def pick_osr(fs_adc: float, f_hi: float) -> int:
        need = int(np.ceil(2.0 * max(f_hi, fs_adc / 4) / max(fs_adc, 1e-9)))
        osr = 1
        while osr < max(4, need):
            osr *= 2
        return int(min(osr, 64))

    def time_run(self, fs: float, n: int):
        """在 fs 上跑整条链，返回第一个 ADC 处的电压序列。"""
        return self.time_run_all(fs, n)[self.adc.id]

    def time_run_all(self, fs: float, n: int) -> dict[str, np.ndarray]:
        """在 fs 上跑整条链，返回每个节点（含所有 ADC/DAC）的电压序列。"""
        from .timedomain import clipped_fraction

        t = np.arange(n) / fs
        vals: dict[str, np.ndarray] = {}
        for nid in self.order:
            node = self.nodes[nid]
            if node.is_sink:
                continue
            if len(node.ins) == 0:
                y = node.obj.time_gen(t)
            else:
                ins = [vals[e.src] * e.div for e in sorted(node.ins, key=lambda e: e.port)]
                y = node.obj.time_apply(ins, fs, t)
            y = np.asarray(y, dtype=float)
            if getattr(node.obj, "is_current", False):
                z = self.load_impedance(node)
                cpar = getattr(node.obj, "c_par", 0.0) or 0.0
                if cpar > 0 and z > 0:
                    fc = 1.0 / (2 * np.pi * z * cpar)
                    a = 1.0 - np.exp(-2 * np.pi * fc / fs)
                    acc = 0.0
                    buf = np.empty_like(y, dtype=float)
                    for i in range(y.size):
                        acc += a * (y[i] - acc)
                        buf[i] = acc
                    y = buf
                y = y * z
            vals[nid] = y
            lo = node.obj.num("v_lo", None)
            hi = node.obj.num("v_hi", None)
            node.diag = {
                "peak_out": float(np.max(np.abs(y))) if y.size else 0.0,
                "clipped": clipped_fraction(y, lo, hi),
                "bw": node.obj.num("fc", None) or node.obj.num("bw", None),
                "in_max_f": None,
            }
        return vals

    # ------------------------------------------------------------ 展示
    def clip_bounds(self):
        """整条模拟链最紧的一组输出限幅（取各块上下限的交集）。"""
        lo = hi = None
        for n in self.nodes.values():
            if n.is_sink or n.is_adc:
                continue
            a = n.obj.num("v_lo", None)
            b = n.obj.num("v_hi", None)
            if a is not None:
                lo = a if lo is None else max(lo, a)
            if b is not None:
                hi = b if hi is None else min(hi, b)
        return (lo, hi) if (lo is not None or hi is not None) else None

    def blocks(self):
        return [self.nodes[i] for i in self.order if not self.nodes[i].is_sink]

    def sinks(self):
        return [n for n in self.nodes.values() if n.is_sink]

    def has_reconstruction(self, node) -> bool:
        """DAC 之后有没有接重建滤波器（模拟域里的滤波器块）。

        DAC 的零阶保持会把 sinc 包络和 k·fs ± f 的镜像一起送出去；只有在
        下游（进入 ADC 之前）确实有滤波器时，才算做了重建滤波。
        """
        seen = set()
        stack = [e.dst for e in node.outs]
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            n = self.nodes[nid]
            if n.is_sink:
                continue
            if n.type == "filter":
                return True
            if n.is_adc:
                continue        # 过了 ADC 就是数字域，后面的滤波器不算重建滤波
            stack.extend(e.dst for e in n.outs)
        return False

    def describe(self) -> list[str]:
        lines = []
        for n in self.blocks():
            tag = "" if n.id.startswith("n") else f"[{n.id}] "
            lines.append(tag + n.obj.describe())
        for n in self.sinks():
            src = str(n.obj.params.get("source") or "").strip()
            if not src and n.ins:
                up = self.nodes[n.ins[0].src]
                if up.is_adc or up.is_dac:
                    src = up.id
            lines.append(f"观测  {n.obj.describe()}" + (f"（数据来源 {src}）" if src else ""))
        return lines


# ------------------------------------------------------------------ 解析

def _fresh_id(nodes, base):
    i = 1
    while f"{base}{i}" in nodes:
        i += 1
    return f"{base}{i}"


def _parse(cfg):
    """返回 ([(id, type, params)], [(from, to, port)])。

    支持两种写法：
      nodes: [{id: src, type: source, params: {...}}, ...] + edges
      chain: [{source: {...}}, ...]     # 旧的单链写法，自动连成一条线
    """
    if "nodes" in cfg:
        raw = []
        for item in cfg["nodes"]:
            if not isinstance(item, dict) or "type" not in item:
                raise GraphError(f"nodes 每项需要 id 和 type：{item!r}")
            nid = str(item.get("id") or f"n{len(raw)}")
            raw.append((nid, str(item["type"]), item.get("params") or {}))
        edges = []
        for e in cfg.get("edges") or []:
            if isinstance(e, dict):
                edges.append((str(e["from"]), str(e["to"]), int(e.get("port", 0))))
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                edges.append((str(e[0]), str(e[1]), int(e[2]) if len(e) > 2 else 0))
            else:
                raise GraphError(f"连线格式不对：{e!r}")
        return raw, edges

    chain = cfg.get("chain") or []
    if not chain:
        raise GraphError("配置里既没有 nodes 也没有 chain")

    raw, edges, sinks = [], [], []
    last = None
    adc_id = None
    for i, item in enumerate(chain):
        if not isinstance(item, dict) or len(item) != 1:
            raise GraphError(
                f"chain 每一项必须是单键字典，例如 `- amp: {{G: 8}}`，收到：{item!r}"
            )
        (ntype, params), = item.items()
        cls = get_class(ntype)
        nid = f"n{i}"
        raw.append((nid, ntype, params or {}))
        if getattr(cls, "is_sink", False):
            sinks.append(nid)
            continue
        if last is not None:
            edges.append((last, nid, 0))
        last = nid
        if getattr(cls, "is_adc", False):
            adc_id = nid
    for s in sinks:
        edges.append((adc_id or last, s, 0))
    return raw, edges
