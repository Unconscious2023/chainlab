# 这里为什么不带 PDF

器件库里的每个数值都在对应 yaml 的 `source:` 字段写明了出处（器件型号 + 手册表号/章节 + 原文片段）。
原厂手册体积很大（本机收集的 17 份共 165 MB），而且版权归厂商所有，所以不随仓库分发。

需要核对时，从厂商官网下载对应手册即可：

| 器件 | 手册 | 官方入口 |
|---|---|---|
| OPA656 / OPA657 | SBOS197 | https://www.ti.com/product/OPA656 |
| OPA847 | SBOS251 | https://www.ti.com/product/OPA847 |
| OPA855 | SBOSA19 | https://www.ti.com/product/OPA855 |
| OPA188 / OPA2140 / OPA828 / OPA690 / OPA695 / THS3091 | 见产品页 | https://www.ti.com/amplifier |
| AD8021 / AD8042 / AD8099 / AD8655 / ADA4528-1 | 见产品页 | https://www.analog.com |
| AD9226 / AD9238 / AD9268 | 见产品页 | https://www.analog.com |
| AD9764 / DAC904 | 见产品页 | https://www.analog.com |
| AD633 / AD835 | 见产品页 | https://www.analog.com |
| NE5532 / LM324 / OP07 / OP27 | 见产品页 | https://www.ti.com |
| STM32F407 / H723 / H743 | DS8626 / RM0468 / RM0433 | https://www.st.com |
| 二阶 MFB 滤波器设计 | SBOA588、SBOA231 | https://www.ti.com |

> 表格里的型号对应 `chainlab/devices/*.yaml` 的 `part` 字段。
