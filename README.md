# ACE-DAW Transfer

## 产品介绍 / About the product

为影片重剪配乐时，可以把 ACE Studio 中的配乐转进 Cubase 或 Nuendo 调整剪辑，再把支持的修改带回 ACE。程序直接读写已保存的工程，转换时可以关闭三个宿主。兼容的音频和视频继续引用原文件，音频块保留各自的位置、源偏移、片段音量、静音和可编辑的默认 S 形淡化。轨道推子、标记和支持的 VST3 静态状态分别保存。

Move a score into Cubase or Nuendo for picture edits and take supported changes back to ACE Studio. The converter reads saved projects and can run with all three hosts closed. Compatible media are referenced in place. Clips retain placement, source offsets, gain, mute and editable default S-curves. Track levels, markers and supported static VST3 states are stored separately.

[通用迁移经验与淡化参数](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/blob/main/docs/Migration-notes-zh-CN.md) · [Build from source](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/blob/main/BUILD.md)

## 许可证 / License

### 禁止商业使用 / No commercial use

**Qing Audio 非商业源码共享许可证 1.0 / Qing Audio Non-Commercial Source-Share License 1.0**

本项目公开第一方源码。OSI 开源认证不适用于本许可。个人、学习、教育、研究、评估、爱好和其他非商业用途可依许可使用。商业使用须另获书面授权。分发原版、二进制版或修改版时，须免费提供完整对应源码，保留作者、版权及许可声明，醒目标明原项目、作者、来源链接、修改者、日期与修改内容。整个修改版须沿用同一许可证。第三方组件各自的许可继续适用。

First-party source is available for personal, educational, research, evaluation, hobby, charitable and other non-commercial uses. This license has no OSI open-source approval. Commercial use requires separate written permission. When distributing original, binary or modified versions, provide the complete corresponding source at no charge, preserve authorship, copyright and license notices, and identify the original project, author, source URL, modifier, date and changes. Keep the entire modified work under the same license. Third-party components retain their respective terms. [完整许可证 / Full license](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/blob/main/LICENSE) · [许可政策 / License policy](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/blob/main/LICENSE_POLICY_CHANGE.md)

## 最新下载与说明书 / Downloads and user guides

**1.4.10 · 2026-11-20**　[GitHub Release](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases/tag/v1.4.10)

| 文件 / File | 用途 / Use |
| --- | --- |
| [统一安装包 / Complete installer bundle (ZIP)](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases/download/v1.4.10/ACE-DAW-Transfer-1.4.10-Windows-macOS.zip) | 内含两个系统的安装程序和两册说明书。 / Both platform installers and both manuals. |
| [Windows x64 (MSI)](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases/download/v1.4.10/ACE-DAW-Transfer-1.4.10-Windows-x64.msi) | 双击安装，从开始菜单打开。 / Run the installer and open the app from Start. |
| [macOS 15+ (PKG)](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases/download/v1.4.10/ACE-DAW-Transfer-1.4.10-macOS.pkg) | 双击安装，从“应用程序”打开。按芯片自动选择 Apple Silicon 或 Intel。 / Open the installer, then launch from Applications. The package selects the native component. |
| [中文使用说明书 (PDF)](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases/download/v1.4.10/User-Guide-zh-CN.pdf) | 安装、首次转换、格式与配色、素材和插件故障处理。 |
| [English user guide (PDF)](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases/download/v1.4.10/User-Guide-en.pdf) | Installation, first transfer, format and color choices, media and plug-in troubleshooting. |

窗口右上角可切换中文和 English，程序记住上一次选择。选择 CPR 或 NPR 可直接打开工程。选择 XML 可向已有工程导入轨道。打开用户工程后的实际听感取决于素材、插件、许可与路由。部分底层诊断和报告保留原文。Windows MSI 未作发布者签名，macOS PKG 未经 Apple 公证。

Use the language menu in the upper-right corner to switch immediately. The choice persists. Choose CPR or NPR to open a standalone score, or XML to add tracks to a session. Playback depends on available media, plug-ins, licenses and routing. Some technical diagnostics and reports retain their original wording. The Windows MSI is unsigned and the macOS PKG is not Apple-notarized.

## 版本记录 / Version history

1.4.10 是首个 GitHub 公开发行版。此前各行按本地已交付版本记录，最近获指定的 Stable 为 1.4.9。 / 1.4.10 is the first public GitHub release. Earlier entries document local deliveries. The most recently designated Stable is 1.4.9.

| 版本 / Version | 中文 | English |
| --- | --- | --- |
| 1.4.10 | 中英界面即时切换并记忆选择。新增 Mac 双架构安装包、Windows MSI 和中英两册 PDF。两种 Mac 均完成安装、打开窗口和工程往返。 | Live Chinese/English switching remembers the language. Adds a dual-architecture Mac installer, a Windows MSI and two PDF guides. Installation, window launch and project round trips passed on both Mac architectures. |
| 1.4.9 | Stable。从 ACE 配乐直接新建有轨道、媒体池与色盘的 CPR/NPR。独立原生工程可保存最多 40 种源色。 | Stable. Create an independent CPR/NPR from an ACE score, with tracks, a media pool and a project palette for up to 40 source colors. |
| 1.4.8 | 原生写回可直接打开含原配色的工程。原生 ACE 配乐的 XML 输出避免把配色空工程误认为音乐工程。 | Native writeback opens with the original project colors. The XML path for original ACE scores removed confusion between an empty palette helper and a populated score. |
| 1.4.7 | 记忆三个选项和手动选择的输出格式，按来源筛选格式。自定义配色的轨道 XML 可搭配配色底板。 | Remembers the three options and selected output format, and filters formats by source. Custom-color track archives can use a companion palette project. |
| 1.4.6 | 轨道和标记分别映射色号，更新媒体池路径。兼容 WAV 和视频原地引用，默认只输出一个 ACE 工程。 | Maps track and marker colors separately and updates pool paths. Compatible WAV/video files stay in place. A transfer creates one ACE project by default. |
| 1.4.5 | 在磁盘离线生成 ACE 工程，改善标记与已匹配 VST3 状态的保存。可单独导出总线插入预置。 | Writes ACE projects offline, improves marker and matched VST3 state persistence, and can save group insert presets separately. |
| 1.4.4 | 输出位置支持 C 盘、桌面和其他可写目录。 | Allows any writable output folder, including the Windows system drive and desktop. |
| 1.4.3 | 窗口可接受 CPR、NPR、ACEP 和轨道 XML 的文件拖放。 | Adds drag and drop for CPR, NPR, ACEP and track archive XML files. |
| 1.4.2 | 轨道 XML 写回保留浮点采样位置，使相邻剪辑维持共同边界。 | Retains fractional sample positions in track archives so adjacent edits can share a boundary. |
| 1.4.1 | 增加非载入式 VST3 预置选项，改善极短片段和点标记进入 ACE 后的保存。 | Adds a presets-only VST3 option and improves the persistence of very short clips and point markers in ACE. |
| 1.4.0 | 直接读取已保存 CPR/NPR 的编排、媒体池及静态混音。该版生成 ACE 工程仍需要 ACE Studio。 | Reads arrangement, media pool and static mix data directly from CPR/NPR files. This version still used ACE Studio to create ACE projects. |
| 1.3.2 | 选择源文件后输出目录跟随工程所在文件夹，可再手动修改。 | Defaults the output folder to the selected source file’s directory and allows a manual change. |
| 1.3.1 | 完善宿主导入与保存流程，Cubase 14 和 Nuendo 14 均已保存含三个插件的测试工程。 | Refines the host import/save flow. Cubase 14 and Nuendo 14 both saved a three-insert test project. |
| 1.3.0 | 加入由 Cubase 14 或 Nuendo 14 协助处理 CPR/NPR 的模式，并保留轨道 XML。 | Introduces host-assisted CPR/NPR transfers through Cubase 14 or Nuendo 14 alongside track archives. |
| 1.2.0 | 增加第三方 VST3 静态效果器链的身份、顺序与状态传递。目标系统需要相同插件。 | Adds plug-in identity, order and state transfer for static third-party VST3 chains. The receiving system needs matching plug-ins. |
| 1.1.0 | 加入从轨道 XML 转入 ACE 的反向流程，发布双向工具名称与图标。 | Adds the return path from track archive XML into ACE and introduces the bidirectional tool name and icon. |
| 1.0.0 | Stable。首版从已保存的 ACE 工程生成可编辑的 Steinberg 轨道 XML，保留独立片段和支持的淡化。 | Stable. The first version generates an editable Steinberg track archive directly from a saved ACE project, retaining separate clips and supported fades. |

[逐版记录 / Changelog](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/blob/main/CHANGELOG.md)

## 历史下载 / Previous releases

本仓库自 1.4.10 起提供公开下载。 / Public downloads start at 1.4.10. [全部发行版 / All releases](https://github.com/Ziqing-Gu/ACE-DAW-Transfer/releases)
