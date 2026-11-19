# ACE-DAW Transfer · 更新记录 / Changelog

本地交付的日期按北京时间记录。 / Dates follow the local delivery record (UTC+8).

## 1.4.10 · 2026-11-20

中英界面即时切换并记忆选择。新增 Mac 双架构安装包、Windows MSI 和中英两册 PDF。两种 Mac 均完成安装、打开窗口和工程往返。

Live Chinese/English switching remembers the language. Adds a dual-architecture Mac installer, a Windows MSI and two PDF guides. Installation, window launch and project round trips passed on both Mac architectures.

## 1.4.9 · 2026-11-20

Stable。从 ACE 配乐直接新建有轨道、媒体池与色盘的 CPR/NPR。独立原生工程可保存最多 40 种源色。

Stable. Create an independent CPR/NPR from an ACE score, with tracks, a media pool and a project palette for up to 40 source colors.

## 1.4.8 · 2026-11-20

原生写回可直接打开含原配色的工程。原生 ACE 配乐的 XML 输出避免把配色空工程误认为音乐工程。

Native writeback opens with the original project colors. The XML path for original ACE scores removed confusion between an empty palette helper and a populated score.

## 1.4.7 · 2026-11-20

记忆三个选项和手动选择的输出格式，按来源筛选格式。自定义配色的轨道 XML 可搭配配色底板。

Remembers the three options and selected output format, and filters formats by source. Custom-color track archives can use a companion palette project.

## 1.4.6 · 2026-11-20

轨道和标记分别映射色号，更新媒体池路径。兼容 WAV 和视频原地引用，默认只输出一个 ACE 工程。

Maps track and marker colors separately and updates pool paths. Compatible WAV/video files stay in place. A transfer creates one ACE project by default.

## 1.4.5 · 2026-11-20

在磁盘离线生成 ACE 工程，改善标记与已匹配 VST3 状态的保存。可单独导出总线插入预置。

Writes ACE projects offline, improves marker and matched VST3 state persistence, and can save group insert presets separately.

## 1.4.4 · 2026-11-20

输出位置支持 C 盘、桌面和其他可写目录。

Allows any writable output folder, including the Windows system drive and desktop.

## 1.4.3 · 2026-11-20

窗口可接受 CPR、NPR、ACEP 和轨道 XML 的文件拖放。

Adds drag and drop for CPR, NPR, ACEP and track archive XML files.

## 1.4.2 · 2026-11-20

轨道 XML 写回保留浮点采样位置，使相邻剪辑维持共同边界。

Retains fractional sample positions in track archives so adjacent edits can share a boundary.

## 1.4.1 · 2026-11-20

增加非载入式 VST3 预置选项，改善极短片段和点标记进入 ACE 后的保存。

Adds a presets-only VST3 option and improves the persistence of very short clips and point markers in ACE.

## 1.4.0 · 2026-11-20

直接读取已保存 CPR/NPR 的编排、媒体池及静态混音。该版生成 ACE 工程仍需要 ACE Studio。

Reads arrangement, media pool and static mix data directly from CPR/NPR files. This version still used ACE Studio to create ACE projects.

## 1.3.2 · 2026-11-20

选择源文件后输出目录跟随工程所在文件夹，可再手动修改。

Defaults the output folder to the selected source file’s directory and allows a manual change.

## 1.3.1 · 2026-11-20

完善宿主导入与保存流程，Cubase 14 和 Nuendo 14 均已保存含三个插件的测试工程。

Refines the host import/save flow. Cubase 14 and Nuendo 14 both saved a three-insert test project.

## 1.3.0 · 2026-11-20

加入由 Cubase 14 或 Nuendo 14 协助处理 CPR/NPR 的模式，并保留轨道 XML。

Introduces host-assisted CPR/NPR transfers through Cubase 14 or Nuendo 14 alongside track archives.

## 1.2.0 · 2026-11-19

增加第三方 VST3 静态效果器链的身份、顺序与状态传递。目标系统需要相同插件。

Adds plug-in identity, order and state transfer for static third-party VST3 chains. The receiving system needs matching plug-ins.

## 1.1.0 · 2026-11-19

加入从轨道 XML 转入 ACE 的反向流程，发布双向工具名称与图标。

Adds the return path from track archive XML into ACE and introduces the bidirectional tool name and icon.

## 1.0.0 · 2026-11-19

Stable。首版从已保存的 ACE 工程生成可编辑的 Steinberg 轨道 XML，保留独立片段和支持的淡化。

Stable. The first version generates an editable Steinberg track archive directly from a saved ACE project, retaining separate clips and supported fades.

## 1.4.10 兼容范围 / Compatibility

Windows x64、macOS 15 及以上（Intel 与 Apple Silicon）。切换界面语言时，源文件、输出位置、格式和三个选项均保留。Windows MSI 可使用 Windows Installer 解包。Mac 安装包按处理器选择原生程序。

Windows x64 and macOS 15 or later on Intel or Apple Silicon. Switching the interface retains the source, destination, format and all three options. Windows Installer can extract the MSI. The Mac installer selects the native application for the processor.
