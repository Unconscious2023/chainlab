# AGENTS.md — 给 AI / 开发者的接手手册

本文件面向**要继续开发这个项目的 AI 代理或人**。先读它，再动代码。
用户手册是 `使用说明.md`；本文件讲的是「内部怎么运转、哪里不能碰、怎么扩展」。

---

## 0. 一分钟版

- **这是什么**：行为级信号链路预算工具。输入一张数据流图 + 采样率，输出噪声预算、SNR、ENOB、频谱/波形。
- **这不是什么**：SPICE、电路仿真、波形精度工具。不要往里加晶体管、寄生、容感时域积分。
- **技术栈**：Python 3.11+ / numpy / pyyaml；前端是**单文件** `chainlab/ui/index.html`（内联 CSS+JS+SVG），后端是 stdlib `http.server` + JSON API。无框架、无构建步骤。
- **跑起来**：`python -m chainlab.ui`（默认 8765，被占用会顺延）。
- **测试**：`python chainlab/tests/test_m1.py`（19）、`test_manual.py`（5）、`test_devices.py`。

---

## 1. 不可破坏的设计律

改代码前必须遵守，破坏其中任何一条都会让工具「看起来能跑但结论是错的」。

| # | 律 | 原因 |
|---|---|---|
| 1 | **采样率由用户给，不做相干采样吸附** | 一旦偷偷改 fs 让整周期，频谱会对得很漂亮但与实际不符 |
| 2 | **解析层只算稳态** | 不模拟上电、瞬态、建立过程；这些走时域层或明确说"未建模" |
| 3 | **ADC 是模拟/数字分界**，数量 ≥1；DAC ≥0 | 指标（SNR/ENOB/噪声预算）默认按所选基准 ADC 计算 |
| 4 | **sink（示波器/频谱仪）不是 block** | 不占链路、不参与传播、不能当中间节点 |
| 5 | **器件值必须可溯源**：yaml 里每个数带 `source:`；没核对的标 `verified: false` | 这是本工具相对"拍脑袋估算"的唯一价值 |
| 6 | **不编造公式** | MFB 低通/高通都是对着 TI 手册 + 数值反算验过的；新拓扑没有可信资料就别做，写明"未实现" |
| 7 | **未建模项必须显式输出** | 每次运行列出没建模的东西，宁可说"这里不准"，也不能让人以为全真 |
| 8 | **滤波器设计器与链路仿真必须用同一套算法** | 历史 bug：设计器画 8 阶带通、链路却只支持 4 阶，曲线与实际不符 |

---

## 2. 代码地图

```
chainlab/
  core/
    block.py        Block 基类、@register、num()（SI 前缀解析）、sdomain_H()
    graph.py        Graph：建图、校验、阻抗/分压、propagate、transfers、time_run、多 ADC/DAC
    spectral.py     Spectrum：Σ Re(c_k·e^{j2πf_k t})，只存频点和复系数
    timedomain.py   时域工具：clamp、clipped_fraction
    metrics.py      noise_budget()、SNR/ENOB
    runner.py       run(cfg)：编排——建图 → 传播/采样 → 每个 ADC/DAC 抓数 → 指标 → 观测端 → 警告
    filterdesign.py 极点零点、SK/MFB 求解、E 系列、级联分解、mfb_actual（仅 MFB LP 的 GBW 修正）
    netlist.py      元件值 → 节点法（MNA）求 H(jω)；parse_elem("R1k")
    topologies.py   标准拓扑白名单（SK/MFB × LP/HP/BP）+ match()
    devices.py      读 devices/*.yaml（按文件名小写或 name 匹配）
    report.py/html.py  CLI 的文本/HTML 报告
    serialize.py    配置读写
  blocks/           每种块一个模块：source amp filter add mult adc dac calc sinks load noise
  devices/*.yaml    器件库（opamp / adc / dac / multiplier）
  ui/
    index.html      唯一前端（约 2500 行，内联）
    schema.py       参数定义 → 界面与 API 都由它生成
    server.py       HTTP JSON API + 静态页
  tests/            test_m1.py / test_manual.py / test_devices.py
  examples/*.yaml   示例链路
```

---

## 3. 数据流（必须理解这一段）

```
cfg(dict) ──► Graph(cfg, ctx)
                 │  nodes: {id: Node(obj=Block)}, edges: [{src,dst,port}]
                 │  order: 拓扑排序（必须无环）
                 ▼
        ┌────────┴─────────┐
   解析路径              时域路径（needs_time_domain 为真）
   propagate():           time_run():
     逐节点 process()       逐节点 time_apply()
     电流源×load_impedance    非线性点积（clamp/SR/量化）
     图上的分压 e.div         按 OSR 超采样，丢弃前半段，再抽取
        └────────┬─────────┘
                 ▼
        ADC 采样（t_n = n/fs）→ 量化 → Spectrum.from_samples()
                 ▼
        metrics.noise_budget + analyze → SNR / ENOB / 预算表
                 ▼
        sinks：scope（时域窗）/ specan（FFT）
```

关键约定：
- **信号在解析层是 `Spectrum`**（一组复幅度正弦），不是数组；只在需要时求值。
- **每个 Block 只关心自己的 `process(inputs)`**，返回 `(Spectrum, diag)`。
- **分压 `e.div` 由 Graph 统一算**（`zout ∥ 下游 zin`），块内部不重复做。
- **时域是单向开关**：只要一个块需要时域，整条链路都走时域。

---

## 4. 关键接口

### 4.1 Block

```python
@register("amp")
class Amp(Block):
    n_in = 1          # 输入端口数；None = 由连线决定
    n_out = 1
    def __init__(self, params, ctx): ...
    def process(self, inputs):        # 解析层：返回 (Spectrum, diag)
    def psd(self, f):                 # 噪声：输入参考功率谱密度 V²/Hz
    def time_apply(self, inputs, fs, t)   # 时域层（可选）
    def time_gen(self, t)             # 无输入的源（可选）
```

诊断字段（`diag`）会被写进结果：`peak_out`、`clipped`、`bw`、`n_in`、`label`。

### 4.2 Graph 提供给你的东西

```python
graph.load_impedance(node)   # 该节点输出的负载（各路 zin 并联）
graph.source_impedance()     # {(src,dst,port): zsrc}
graph.needs_time_domain()    # 是否需要时域
graph.transfers(f, to=adc_id)  # 每个节点到指定 ADC 的复频响
```

### 4.3 run() 返回

```python
{"ok": True, "result": {
    "name", "mode", "fs", "n_samples", "t_seconds", "periods",
    "chain", "budget": [{"kind","id","noise","missing"}],
    "metrics": {"noise_total","adc_noise","signal_rms","snr_db","enob",
                "enob_full","thd","sfdr_db","fund_hz","fund_amp","fs_ratio","adc_id"},
    "adcs": [...], "dacs": [...], "sources": [...],
    "sinks": {sink_id: {"_kind","_source","_source_mode","_plot",...}},
    "sink_order": [sink_id...],
    "warnings": [...], "errors": [...], "risks": [...]
}}
```

---

## 5. 噪声模型（改动前务必读）

- 每个块给 `psd(f)`，**输入参考**；`noise_output_referred=True` 的块（TIA）例外，按输出参考处理。
- 电流噪声：`in_noise` 存在时，`psd += (in·Zsrc)²`，`Zsrc` 取该块各路输入的源阻抗。
- 热噪声：只有 `thermal_zout=True` 的无源块（源/负载/隔直）加 `4kT·Re(Z)`；有源级的输出热噪声已含在 `en` 里，**不能重复计**。
- 积分**只到 fs/2**；超过的部分按混叠折回处理，不是忽略。
- `noise_budget` 的 `missing` 字段用于标记「选了器件但手册没给 en/in」或「未连到 ADC」，界面必须显示，不能静默当 0。

---

## 6. 滤波器子系统（最脆的一块）

三个入口，**必须是同一套数学**：

| 入口 | 用什么算 |
|---|---|
| 设计器预览 `/api/filter/preview`（按参数） | `design_pz` |
| 链路仿真 `Filter(mode="design")` | `design_pz`（带通的 order 是**总阶数**，内部拆 HP+LP） |
| 按元件值 `Filter(mode="netlist")` | `netlist.solve`（MNA），组合受 `topologies.STD` 白名单约束 |

要点：
- `netlist.solve(topo, z, f)` 支持 `sk`/`mfb`，`z` 是 5 个 `(kind, value)`，`kind=None` 表示开路。
- **SK 的 Z2 为开路 = 一阶 RC**（退化），走分压公式，不走 MNA。
- 组合校验：`topologies.match(topo, kinds)` 返回 `(ftype, err)`；**白名单之外一律报"不是标准格式"**，不要偷偷算。
- MFB 高通的 Z 映射与低通不同（3 电容 + 2 电阻）：`R C C R R` 是带通，`C C C R R` 是高通，`R R R C C` 是低通。
- `mfb_actual()` **只对 MFB 低通成立**（B2/B1/B0 由低通传函推出），高通调用它算出来的数是错的。
- 带通的 `order` 在不同函数里含义不同：`design_pz("bandpass", n)` 的 n 是**每侧**阶数（产生 2n 极点）；`Filter` 与设计器的 n 是**总阶数**。历史 bug 就出在这里，改动时保持注释与拆分逻辑。

---

## 7. 扩展配方

### 7.1 加一个器件（最常见）

1. 在 `chainlab/devices/` 新建 `<小写名>.yaml`：

```yaml
name: OPA1612
class: opamp          # opamp / adc / dac / multiplier
part: OPA1612
verified: true        # 关键项都核对过才写 true
source: |
  OPA1612 datasheet Table 6「Input voltage noise 1.1 nV/√Hz」…
note: 低噪精密，双通道
opamp:
  en: 1.1n
  in: 1.7p
  gbw: 40M
  sr: 27
```

2. **不要写没核对的数**；缺项就不写该键，`verified: false`，note 写清缺什么。
3. 重启服务，器件会出现在下拉框（`schema()` 每次启动扫描 devices/）。
4. 跑 `python chainlab/tests/test_devices.py` 做区间体检。

### 7.2 加一个块

1. `chainlab/blocks/<name>.py`：`@register("<name>")`，实现 `process`，需要时实现 `psd`/`time_apply`。
2. `chainlab/blocks/__init__.py` 里 import（否则不会注册）。
3. `chainlab/ui/schema.py` 的 `blocks` 里加字段定义（界面自动生成）。
4. 若是观测端，加进 `sink_order`；若参与噪声，确认 `psd` 的量纲。
5. 加测试用例（见 §8）。

### 7.3 加一个滤波器拓扑

1. 在 `core/topologies.py` 的 `STD` 加一行 `(topo, ftype): {z1..z5 的元件类型}`。
2. 若与 `sk`/`mfb` 的节点连接不同，在 `core/netlist.py` 的 `solve()` 里加分支（按节点建 MNA）。
3. **必须**用独立方法验算（手算公式 / 手册示例 / 另一个求解器），并把对照值写进 `test_manual.py`。

### 7.4 加一个 UI 字段

只改 `chainlab/ui/schema.py`：加 `{"key","label","type","default",...}`。
`show_if` 控制联动显示，`modal: true` 表示不在右侧面板显示（设计器专用）。

---

## 8. 测试

```bash
python chainlab/tests/test_m1.py       # 行为断言，19 项，加用例就加在这里
python chainlab/tests/test_manual.py   # 与手册数值对照，5 项
python chainlab/tests/test_devices.py  # 器件区间体检
```

新增用例的规矩：
- 断言**数值**，不要只断言 `ok`（`assert abs(x - 期望) < 容差`）。
- 期望值要有来源：手册表格、手算公式、TI 示例。
- 涉及界面/接口的，补一个 `/api/run` 级别的冒烟。

---

## 9. 坑（都踩过，别再踩）

| 坑 | 表现 | 正解 |
|---|---|---|
| **改了 `index.html` 但页面没变** | 浏览器/截图还是旧的 | 服务启动时把 HTML 读进内存 → **必须重启服务** |
| **.bat 里写中文** | `'b.ui' 不是内部或外部命令` | cmd 按 GBK 解析批处理；脚本正文用**纯 ASCII**，中文交给 Python 输出 |
| **PyYAML 缺失** | `ModuleNotFoundError: No module named 'yaml'` | 运行环境可能被重置；`pip install pyyaml`，或让用户用 `打开界面.bat`（它会检测并询问安装） |
| **DOM id 重复** | 页面白屏 | `index.html` 里 `const c=d.components;` 之类重复声明；加 id 前先搜索 |
| **测试钩子留在代码里** | 用户打开就自动弹窗 | 提交前搜 `__test`、`setTimeout(()=>` |
| **带通阶数语义混用** | 设计器 8 阶、链路报"最高 4 阶" | 见 §6 |
| **E 系列取整后 Q 偏移** | 设计 Q=0.707 实测 0.65 | 这是真实的，不要"修正"回去；界面显示"实际 Q" |
| **电流源未接负载** | I×Z 打饱和 | `graph._check_current_source` 已经拦；放宽时注意保留这条 |
| **CSV/HTML 报告与 sinks 不同步** | 报告少了观测端 | 报告按 `sink_order` 遍历，不要按类型合并 |

---

## 10. 提交前自检

- [ ] `test_m1.py` / `test_manual.py` / `test_devices.py` 全绿
- [ ] 改过 `index.html` → 重启服务 + 截图确认（用 headless Edge，见 `docs/TESTING.md`）
- [ ] 没有调试钩子、没有硬编码本机路径
- [ ] 新增器件带 `source:`，未核对的标 `verified: false`
- [ ] 新行为写进 `使用说明.md`；改动影响架构的写进本文件
- [ ] `git status` 干净（`out/`、`.server.pid`、`*.log` 不应被跟踪）

---

## 11. 还没有做的事（欢迎接手）

1. DAC 噪声并入 ADC 噪声预算
2. 双通道 ADC/DAC 的通道级建模
3. 多 ADC 指标并列表格（当前只显示基准 ADC）
4. 元件容差蒙特卡洛（现在只算标称值）
5. 滤波器反馈电阻热噪声（当前只算运放 en）
6. 电流源结电容的精确时域（现在是单极点 IIR 近似）
