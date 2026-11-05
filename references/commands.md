# 制作命令与依赖

Windows当前环境：Python3.13，Microsoft Word COM，Edge。依赖见../scripts/runtime/requirements.txt；浏览器检查另需playwright与Edge。缺失时先查本机可用运行环境，不改凭据或全局配置，不默默安装替代Office。命令使用绝对路径，PowerShell设置PYTHONUTF8=1；脚本内部定位运行时，不要求切换到原开发目录或配置PYTHONPATH。

## 从两份Word生成

```powershell
$env:PYTHONUTF8='1'
python E:/Nolva/skills/report-revision-comparison/scripts/build_review.py --old '旧稿绝对路径.docx' --new '新稿绝对路径.docx' --old-version v1 --new-version v2 --title '报告简称 · 修订对照' --work '新的working子目录' --out '交付目录/报告简称_修订对照_v1_至_v2_审阅r0.html'
```

--work必须尚不存在，--out不得已存在；失败中间文件保留，另选新工作子目录重试，不清理他人内容。Word由Windows PowerShell5.1只读打开并固定格式导出，不更新域、不接受修订、不保存原件。发现原始Word含既有修订/复杂对象，应按实际覆盖核对，不自动视为已接受最终文本。

提供--old-pdf和--new-pdf可跳过Word转换；仅在已确认是对应两版Word的PDF时使用。原始输入SHA-256转换前后核对；运行时清单校验不符时停止，回到维护方核对，不能直接改manifest掩盖差异。

两份PDF时仍用--old/--new及版本参数，输入应均为.pdf，仅比较文字层，不做像素差异或OCR。没有可靠逐字位置时仍可阅读并转文字审阅核对。

输出工作文件：base.json（DOCX提取基线）、package.json（原页/逐字映射包）、old.pdf/new.pdf（本次转换时）、build-result.json（构建/覆盖/来源/哈希）。构建时browser_verified=false表示尚未运行浏览器，并不代表检查失败。

另输出content-audit.json：分类型配对/定位覆盖、未配对与语义复核候选、源Word批注清单及HTML收录对账、嵌入资源与发布包一致性。它不自动导入批注或修正配对；content_verified默认false，按content-review.md完成适用的复核。检查文件含源批注等私有内容，只留本次工作区。

## 浏览器检查

```powershell
python E:/Nolva/skills/report-revision-comparison/scripts/check_review.py 'HTML绝对路径' --old '旧稿绝对路径.docx' --new '新稿绝对路径.docx' --evidence '新的working检查子目录'
```

脚本产出browser-result.json、原页/文字截图和导出重开样本。它检查可观测交互，不替代专业阅读；源图表/页眉页脚可能只在原页呈现，不能把结构化正文支持范围说成Word全结构无损。

默认先验证细节隐藏状态与可见修改子集，再打开细节检查全部修改导航；导出重开恢复默认隐藏。旧HTML的JS/CSS不同于当前发布包时，写出content-audit和失败原因后停止浏览器验收；对历史文件进行只读内容评估可单独用audit_review.py，不把历史界面不一致误称为文件损坏。

## 带标注再次生成

已验证的本次标注包用--package传入；同时给新的--work和新的--out。不会重新提取或转PDF。具体见annotations.md；正式交付前检查带标注的最终文件。

带标注重建时同时提供--old与--new原文件，可重新核对批注收录和源身份；不提供时明确标为未检查，不沿用旧计数冒充本次对账。采用当前发布包渲染新文件，保留原有HTML与审阅数据。
