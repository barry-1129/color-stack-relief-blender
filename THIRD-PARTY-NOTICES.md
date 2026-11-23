# 第三方内容与许可说明

本仓库**主体**（`ColorStackRelief/` 下的 Python 代码、`tests/`、`calib/`、
`tools/`、`previews/`、`docs/`）由 barry 编写，以 **GPL-3.0-or-later** 发布，
全文见 [LICENSE](LICENSE)。

## 1. Bambu Studio 工艺预设快照

| 项 | 内容 |
|---|---|
| 文件 | `ColorStackRelief/resources/bambu_a1_template.json` |
| 性质 | 从 Bambu Studio 导出的「工程设置」快照（570 个工艺参数键） |
| 识别信息 | `printer_settings_id = "Bambu Lab A1 0.4 nozzle"`、`print_settings_id = "0.20mm Standard @BBL A1"`、`version = "02.07.01.57"` |
| 上游项目 | Bambu Studio — https://github.com/bambulab/BambuStudio |
| 上游许可 | **GNU Affero General Public License v3.0（AGPL-3.0）** |

**为什么随包分发**：3MF 的 `Metadata/project_settings.config` 需要一整套工艺参数，
Bambu Studio 打开时才不会退回默认值。插件只在这份快照上**覆盖少量键**
（层高、墙圈数、填充、温度、耗材映射等，见 `core/exporters.py` 的
`bambu_process_overrides()`），其余保持原值。

**许可关系**：该文件不是本项目的原创内容，**仍受 AGPL-3.0 约束**，
不因被收录而改为 GPL。AGPL v3 第 13 条明确允许 AGPL 作品与 GPLv3 作品
组合成一个作品分发：组合作品中来自 AGPL 的部分继续适用 AGPL，其余部分
继续适用 GPLv3。本仓库即按此方式处理 —— 仅该 JSON 适用 AGPL-3.0，
其余代码适用 GPL-3.0-or-later。

**对应源码**：AGPL 要求提供对应源码，Bambu Studio 的完整源码见上方仓库链接。
本项目对该 JSON 未做修改（仅作为数据被读取与覆盖键值）。

**如果你要再分发**：请保留本说明与 `LICENSE`，不要声称该 JSON 是 GPL 授权。

## 2. 商标

「Bambu」「Bambu Lab」「Bambu Studio」「拓竹」是其各自所有者的商标。
本项目与 Bambu Lab / 拓竹科技**没有隶属、赞助或背书关系**；
文档与界面中出现这些名称，仅用于说明 3MF / OBJ 格式与打印流程的兼容性。

## 3. 不做随包分发的内容

以下内容**不在仓库里**（见 `.gitignore`），也不在发布包内：

- Bambu Studio 的 C++ 源码片段与逆向分析笔记（`_*.cpp`、`_*.hpp`、`ANALYSIS-*.md`）；
- 用户实拍照片与第三方 3MF 参考件（`tests/photos/`、`tests/ref/`）；
- 历史标定实验的中间产物（`tests/calib/`、`tests/out/`）。

## 4. 字体

`tests/`、`previews/` 里渲染示意图时调用**系统自带字体**
（Windows 的微软雅黑 / 黑体 / 宋体，macOS 的 PingFang，Linux 的 Noto），
不随仓库分发任何字体文件。
