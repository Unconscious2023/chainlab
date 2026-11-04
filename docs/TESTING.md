# 测试

## 三套测试

```bash
python chainlab/tests/test_m1.py        # 行为断言（19 项）
python chainlab/tests/test_manual.py    # 与手册数值对照（5 项）
python chainlab/tests/test_devices.py   # 器件区间体检
```

| 套件 | 断言什么 | 什么时候加 |
|---|---|---|
| `test_m1.py` | 数值行为：SNR、ENOB、噪声、混叠、削顶、ZOH、多 sink 来源… | 每次改引擎/块都要加 |
| `test_manual.py` | 和真实手册的对照（例如 OPA656 噪声预算） | 新增器件或改变噪声模型时 |
| `test_devices.py` | 每个器件 yaml 的 en/in/gbw/sr 是否在合理区间 | 加了新器件自动覆盖 |

## 写用例的规矩

```python
@case("A13 OPA695 在 G=8 用查表带宽，不是 GBW/G")
def _a13():
    a = Multiplier({"device": "OPA695", "G": 8}, Ctx({}))
    assert abs(a.fc - 450e6) < 1e6, a.fc     # 断言数值 + 容差
```

1. **断言数值**，不要只断言 `ok=True`。
2. 期望值必须有出处：手册表格、手算公式、TI 示例。
3. 容差写清楚（相对误差或绝对误差）。
4. 失败信息里带上实际值（`assert cond, actual`）。

## 界面改动怎么验

`index.html` 是单文件，没有构建步骤，但**服务启动时把 HTML 读进内存**——改完必须重启：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Start-Sleep 2
Start-Process python -ArgumentList "-m","chainlab.ui","--no-browser" -WindowStyle Hidden
Start-Sleep 4
```

截图（无需浏览器插件）：

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$t = Join-Path $env:TEMP ("s" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $t | Out-Null
& $edge --headless=new --disable-gpu --hide-scrollbars --virtual-time-budget=12000 `
        --window-size=1500,1000 --user-data-dir="$t" `
        --screenshot="out\shot.png" "http://127.0.0.1:8765/"
```

然后看 `out/shot.png`。**如果截图内容和上一版一模一样，先怀疑服务没重启**（用文件哈希判断，别靠眼睛）。

## 接口级冒烟

`/api/run` 是主入口，建议改后端后跑一遍代表性配置：

```python
import json, urllib.request
body = {"meta": {"fs": 100000},
        "nodes": [{"id": "s1", "type": "source", "params": {"type": "sine", "f": "1k", "amp": "100m"}},
                  {"id": "c1", "type": "adc", "params": {"device": "STM32F407", "fs": "100k"}}],
        "edges": [{"from": "s1", "to": "c1", "port": 0}]}
req = urllib.request.Request("http://127.0.0.1:8765/api/run",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
print(json.loads(urllib.request.urlopen(req).read())["result"]["metrics"])
```

## 覆盖到的主要场景

- 解析/时域两条路径一致性（小信号 vs 大信号削顶）
- 混叠（`fs=50k` 时 32 kHz 落到 18 kHz）
- 电流源 + 负载 + 结电容的一阶滚降
- MFB 低通对照 TI 三行数据（500k / 5M / 20M）
- MFB 高通对照 TI 示例（fc=1k → R1=75k、R2=336k）
- 带通 2/4/6/8 阶的拆分与响应
- 元件值的标准组合校验（非标准必须报错）
- 多 ADC / 多 DAC / 多 sink 的独立观测
- DAC 零阶保持（相邻更新点之间为常值）
- 无 ADC 时必须给出"已自动补理想 ADC"的警告
