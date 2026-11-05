# Doc Revision Review

比较两版 Word 或带文字层的 PDF，生成可离线打开、分享的单文件 HTML 修订对照。

将原页对比、文字差异和审阅说明集中在同一界面，适用于技术报告、方案及其他长文档的版本审阅。项目包含可供 AI Agent 使用的 Skill，以及可直接运行的 Python 脚本。

## 功能

- 原页双栏对比，保留页面中的排版、图表和视觉上下文。
- 在可可靠定位的原页文字上，以红色删除线和蓝色下划线标记变化。
- 通过修改导航定位差异，支持两侧页面联动滚动。
- 切换文字审阅视图，查看对应段落及文字变化。
- 支持加入有来源的修改说明和审阅意见。
- 导出单文件 HTML，供接收方在浏览器中离线阅读。
- 生成来源哈希、覆盖审计和构建记录，辅助核对比较结果。

## 支持范围

| 输入 | 支持情况 |
| --- | --- |
| 两份 DOCX | 支持；默认使用 Microsoft Word 转换原页 PDF |
| 两份带文字层的 PDF | 支持；基于文字层比较 |
| 扫描 PDF | 暂不支持 OCR |
| DOCX 与 PDF 混合输入 | 暂不支持 |
| 其他 Word 格式，如 `.doc` | 暂不支持直接输入 |

Word 转换采用只读方式，不接受原文件中的修订，也不保存原文件。

## 环境要求

当前使用环境：

- Python 3.13
- Python 依赖：见 `scripts/runtime/requirements.txt`
- DOCX 默认转 PDF：Windows、Windows PowerShell 5.1 和 Microsoft Word
- 浏览器自动检查：Playwright 和 Microsoft Edge

阅读生成的 HTML 无需安装 Python 或 Word。

PDF 输入可跳过 Word 转换；其他操作系统的兼容性尚需独立验证。

## 安装

下载或克隆仓库后，在仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r scripts/runtime/requirements.txt
```

如需执行浏览器自动检查，另行安装 Playwright，并确保已安装 Microsoft Edge：

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
```

以下命令均在仓库根目录执行，使用 PowerShell 语法。

## 快速开始

### 比较两份 Word

```powershell
$env:PYTHONUTF8 = '1'

.\.venv\Scripts\python.exe scripts/build_review.py `
  --old "D:/Documents/report-v1.docx" `
  --new "D:/Documents/report-v2.docx" `
  --old-version "v1" `
  --new-version "v2" `
  --title "报告修订对照" `
  --work "./working/review-001" `
  --out "./output/report-review-001.html"
```

将示例路径替换为实际文件路径。完成后，用浏览器打开生成的 HTML。

`--work` 必须是尚不存在的新目录，`--out` 必须是尚不存在的文件。再次生成时，请使用新的工作目录和输出文件名。

### 比较两份 PDF

```powershell
.\.venv\Scripts\python.exe scripts/build_review.py `
  --old "D:/Documents/report-v1.pdf" `
  --new "D:/Documents/report-v2.pdf" `
  --old-version "v1" `
  --new-version "v2" `
  --work "./working/review-002" `
  --out "./output/report-review-002.html"
```

两份 PDF 都应包含可提取的文字层。该模式不进行 OCR 或逐像素图像比较。

### 使用已有的 Word 对应 PDF

对于 DOCX 输入，可额外传入：

```powershell
--old-pdf "D:/Documents/report-v1.pdf" `
--new-pdf "D:/Documents/report-v2.pdf"
```

这会跳过 Word 转换。调用者需要确认两份 PDF 分别对应本次输入的旧稿和新稿。

## 检查结果

构建完成后，可运行浏览器检查：

```powershell
.\.venv\Scripts\python.exe scripts/check_review.py `
  "./output/report-review-001.html" `
  --old "D:/Documents/report-v1.docx" `
  --new "D:/Documents/report-v2.docx" `
  --evidence "./working/check-001"
```

检查涵盖修改导航、原页与文字视图切换、导出重开及离线行为，并生成检查记录和截图。

构建成功表示文件已生成；浏览器检查通过表示相关交互已验证。比较内容、重要变化和专业含义仍需结合原文复核。

## 输出文件

主交付文件为 `--out` 指定的单文件 HTML。

工作目录同时保留：

- `package.json`：比较数据及原页定位信息。
- `content-audit.json`：覆盖情况、复核候选及批注收录核对。
- `build-result.json`：运行时版本、来源哈希和构建状态。
- 转换产生的 PDF 等中间文件。

浏览器检查另外生成检查记录、截图和导出样本。

工作文件可能包含输入文档正文、批注及本机路径；分享时应按需要选择文件。

## 作为 AI Agent Skill 使用

`SKILL.md` 提供输入确认、执行、标注、复核和交付流程。支持此类 Skill 的 Agent 可从该文件读取使用方法，具体安装位置取决于所使用的工具。

调用示例：

> 使用 report-revision-comparison 比较这两版 Word，生成单文件离线 HTML，并检查重要变化和原页定位。

自动比较不会推断作者意图或生成专家意见。需要修改说明时，应依据实际差异和提供的材料注明来源。

## 已知限制

- 复杂表格、文本框、页眉页脚、既有修订及其他复杂对象，需要结合原页核对。
- 原页可显示图表，但文字差异结果不等同于图像内容比较。
- 章节重构、大面积重写及段落迁移，可能需要人工复核配对关系。
- 缺少可靠坐标时，部分变化不会绘制逐字标线。
- 源 Word 批注需要单独核对收录情况，不能默认全部进入 HTML。
- 审阅层的文字编辑不会回写 Word，也不会同步刷新原页 PDF。

## 项目结构

```text
.
├── SKILL.md
├── agents/
├── references/
│   ├── commands.md
│   ├── annotations.md
│   └── content-review.md
└── scripts/
    ├── build_review.py
    ├── audit_review.py
    ├── check_review.py
    └── runtime/
```

## 问题反馈

提交问题时，请说明输入格式、运行环境、执行命令和实际表现。建议使用可公开的最小复现文档，并检查日志、截图和附件中的私人信息。

## 许可证

本项目采用 [GNU Affero General Public License v3.0](LICENSE)。

允许使用、修改和商业使用。分发受本许可证覆盖的软件，
或提供适用的网络服务时，须履行对应源码提供等义务。

本项目使用 PyMuPDF，按其 AGPL v3 许可使用；
其他第三方依赖遵循各自许可证。

用户输入的文档及生成的报告，不会仅因使用本工具而自动适用 AGPL。
