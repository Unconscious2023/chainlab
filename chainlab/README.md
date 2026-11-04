> 完整使用说明见仓库根目录 **使用说明.md**（模块参考、滤波器设计器、噪声模型、未建模清单）。以下为开发向摘要。

# chainlab v1.3 — 信号链路预算 + 算法能力边界

行为级、模块级、以快为先。**不是**电路仿真器：不做晶体管级、不做寄生、不做容感动态。

数据流是**图**，不是一条线：

```
源 ─┬─ 放大8× ─┐
    └─ 放大2× ─┴─ 加法器 ─ ADC ─┬─ 示波器
                                └─ 频谱仪
```

ADC 有且仅有一个，是模拟域与数字域的分界点。

---

## 快速开始

**界面**：双击 `打开界面.bat`，浏览器打开 `http://127.0.0.1:8765/`。

| 操作 | 说明 |
|---|---|
| 加模块 | 点左侧模块库 |
| 连线 | 从节点右侧圆点拖到另一个节点左侧圆点 |
| 删线 | 点连线中间的小圆点 |
| 移动 / 删除节点 | 拖标题 / 点 × |
| 缩放 | 画布上滚轮 |
| 调结果区高度 | 拖动结果区上沿那条细线 |
| 切图表 | 结果区页签（画布上每个示波器 / 频谱仪各一个，标题为「类型 + 节点 id」） |

**双击跑一条配置**：`运行示例.bat`；把任意 YAML 拖到它上面就跑那个配置。

```bash
python -m chainlab.ui
python -m chainlab.cli run chainlab/examples/amp8_chain.yaml
python chainlab/tests/test_m1.py
```

依赖：Python 3.11+、numpy、pyyaml。

---

## 配置写法

```yaml
meta: {name: 两路求和, fs: 100k, t_window: 10周期}

nodes:
  - {id: src, type: source, params: {type: sine, f: 1k, amp: 100m}}
  - {id: a1,  type: amp,    params: {device: OPA227, G: 8, v_hi: 2.0}}
  - {id: a2,  type: amp,    params: {device: OPA227, G: 2}}
  - {id: sum, type: add,    params: {n_in: 2, k: [1, 1], dc: 1.65}}
  - {id: adc, type: adc,    params: {device: STM32H743, n_bits: 16, vref: 3.3}}
  - {id: sp,  type: specan, params: {n_fft: 65536}}

edges:
  - [src, a1]
  - [src, a2]
  - [a1, sum, 0]
  - [a2, sum, 1]
  - [sum, adc]
  - [adc, sp]
```

旧的 `chain:` 单链写法仍然有效，会被自动连成一条线。

---

## 模块

| 模块 | 端口 | 要点 |
|---|---|---|
| `source` | 0→1 | sine / square / triangle / dc / am / fm |
| `amp` | 1→1 | 器件档；`bw_at_gain` 查表；最小稳定增益检查 |
| `filter` | 1→1 | **1~8 阶**，巴特沃斯 / 切比雪夫 I；带通仍限 2 阶 |
| `add` | **N→1** | `n_in` 定端口数，`k` 逐口增益 |
| `adc` | 1→1 | 具体型号 + 位数可选；吞吐/建立双判定 |
| `dac` | 1→1 | 更新率可设（留空取器件上限）；输出零阶保持（ZOH），示波器按阶梯绘制 |
| `scope` | 1→0 | 起止时间、显示点数；数据来源留空时按连线推导（ADC 或 DAC） |
| `specan` | 1→0 | 独立长度采集，峰值保持显示 |
| `algo` | 1→0 | Python 文件 |

**所有模拟块都有输出限幅** `v_hi` / `v_lo`。限幅在采样点施加，同时给出削平占比；中间级的谐波再生没有建模，会出现在「未建模」清单里。

**示波器**可指定 `t_start` / `t_end` / `n_points`：

```yaml
- {id: sc, type: scope, params: {t_start: 2m, t_end: 6m, n_points: 800}}
```

---

## 器件

| 类别 | 已有 |
|---|---|
| 运放 | OPA227, OPA656, OPA695, OPA847, NE5532, OPA2188, OP07, OP27 |
| ADC | STM32F407（12）｜STM32H743（16/14/12）｜STM32H723-ADC12（16/14/12）｜STM32H723-ADC3（12/10/8/6） |
| DAC | AD9764（14/125 MSPS）｜DAC904（14/165 MSPS）｜STM32H723-DAC（12/1 MSPS）｜STM32H743-DAC（12）｜STM32F407-DAC（12、双通道、1 MSPS、建立 3 µs 典型） |

`STM32H743` 的转换周期标了 `verified: false`（按 16 bit 8.5 / 14 bit 7.5 / 12 bit 6.5 ADCCLK 录入，待对照 RM0433 核对），界面会在参数面板里提示。改一个 YAML 就能修正，不用动代码。

---

## 引擎

**两层，自动切换**：任一模拟块设了输出限幅（v_hi / v_lo）就进时域，否则走解析层。

- **解析层**：信号用谱系数表示，逐块乘复频响。不过采样，采样点直接求值。
- **解析层也能给长记录**：频谱仪要 65536 点，就直接在 65536 个采样点上求值，不需要真跑时域。
- **分叉汇合**：噪声按「该节点 → ADC」的传递函数积分，多路径按复频响相干相加。
- **混叠**：`f_k mod fs` 精确折叠并报告。
- **阻抗**：每条连线一次分压，`zout` 对 `zin`，损失 >1% 提示。
- **噪声注入**：预算表总噪声叠加到采样序列，谱上噪底与预算一致。

---

## 尚未实现

- 反馈环（PLL 需要）
- `mult`（AD835 / AD633）、`buf`（BUF634 / THS3091）
- 滤波器 E6/E12 吸附、外部波特图拟合、带通高阶
- 参数扫描、边界图导出
- 多通道、差分（字段留了）
- 编辑器撤销/重做、自动布局

---

## 加器件 / 加模块

器件：`devices/` 下加 YAML，不改代码。

```yaml
name: OPA695
class: opamp
opamp: {sr: 4300, en: 1.8n, g_min: 1, bw_at_gain: {1: 950M, 8: 130M}}
```

模块：

```python
@register("mymod")
class MyMod(Block):
    n_in, n_out = 1, 1
    def process(self, inputs):          # 按端口顺序
        out = inputs[0].filtered(self.H)
        return out, {"bw": ..., "peak_out": out.peak()}
```

三条规矩：纯函数无状态；`diag` 用固定字段；多入块要同时实现 `port_gain()`。

---

## 目录

```
chainlab/
├── core/       spectral block graph metrics runner report devices serialize filterdesign
├── blocks/     source amp filter add adc sinks
├── devices/    5 个运放 + 2 个 ADC
├── examples/   amp8_chain sum_two_paths overload_adc opamp_unstable
├── ui/         index.html schema.py server.py
├── tests/      test_m1.py
└── cli.py
```
