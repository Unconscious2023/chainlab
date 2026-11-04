# 器件库格式

器件放在 `chainlab/devices/*.yaml`，每个文件一个器件。启动时 `devices.load()` 扫描整个目录；`schema()` 把它们按 `class` 分组塞进前端下拉框。

## 通用字段

```yaml
name: OPA656              # 显示名，也是查找键（大小写不敏感）
class: opamp              # opamp / adc / dac / multiplier
part: OPA656              # 手册上的完整型号/描述
verified: true            # 关键参数是否逐项核对过
source: |                 # 必填：每个数的出处（文件名:行号「原文」或 URL+表号）
  OPA656 数据手册 Table 1「Input Voltage Noise f > 100kHz 7 nV/√Hz」
note: 低噪 FET 输入
```

**规则**

1. 没核对的数**不要写**；缺项就省略该键，`verified: false`，并在 `note` 说明缺什么。
2. `source` 是给人和 AI 复核用的，写清楚"哪份手册、哪张表、原文片段"。
3. 单位用 SI 前缀字符串：`7n`、`1.3f`、`50p`、`230M`、`3.9G`、`4.7k`。

## opamp

```yaml
opamp:
  en: 7n          # 输入电压噪声密度 V/√Hz
  in: 1.3f        # 输入电流噪声密度 A/√Hz
  gbw: 230M       # 增益带宽积 Hz
  sr: 290         # 压摆率 V/µs
  v_swing: 3.3    # 输出摆幅 V（可选）
  i_out_max: 70m  # 最大输出电流 A（可选）
  g_min: 1        # 最小稳定增益（可选）
  bw_at_gain:     # 电流反馈型：按增益查带宽（可选，替代 gbw）
    1: 1700M
    2: 1400M
    8: 450M
```

## adc

```yaml
adc:
  n_bits_default: 12
  n_bits_options: [12]
  per_bits:
    12:
      enob: 10.4          # 有效位数（满量程折算用）
      conv_cycles: 12     # 一次转换占多少 ADCCLK
      sample_cycles: [3, 15, 28, 56, 84, 112, 144, 480]
  f_adc: 30M              # ADC 时钟上限
  radc: 6k                # 输入电阻（无则留空 → 视为无穷大）
  cadc: 4p                # 输入电容
  vref: [1.8, 3.6]        # 参考电压范围
  differential: false
  sampling_model: sar     # sar / pipeline（pipeline 不占建立时间）
```

## dac

```yaml
dac:
  bits: 14
  fs_max: 125M            # 更新率上限（面板可手动改小）
  settling: 35n
  vref: 1.2
  output_formula: "差分电流 × 负载"
  noise_density: 50p
```

## multiplier

```yaml
noise_density: 50n        # 输出噪声密度 V/√Hz
multiplier:
  U: 1
  bw: 250M
  feedthrough_x: 5m       # 馈通（折合到 1 V 输入）
  feedthrough_y: 1m
  slew: 1000
```

## 加完器件之后

```bash
python chainlab/tests/test_devices.py     # 区间体检（en/in/gbw/sr 是否落在合理范围）
python -c "from chainlab.ui.schema import schema; print([k for k,m in schema()['device_meta'].items()])"
```

界面刷新即出现（`schema()` 每次启动扫描目录）。
