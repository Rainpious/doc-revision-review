# 标注接口

运行时annotations.py仅接受本次基线的package_id与working_copy.state_hash，且包尚无审阅编辑operations。用于制作端补入原因/影响/意见，不等于浏览器补丁导入。首次build生成本次working/package.json，再从其中读关系、块和变更ID，不复用别的报告的业务标注或package ID。

```json
{"package_id":"从本次package复制","base_hash":"本次状态哈希","replace":{"reasons":[],"changes":[],"opinions":[],"decisions":[],"summary":{"text":"真实差异摘要","change_ids":[],"review_status":"unreviewed"}}}
```

replace只允许上述五类字段，可只提供需要替换的字段；每项完整结构见../scripts/runtime/schema/package.schema.json（生成源build_schema.py）。不得直接改块/关系来适配旧意见。需要调整配对时使用引擎事务而非绕过校验。

合并命令：

```powershell
python E:/Nolva/skills/report-revision-comparison/scripts/runtime/annotations.py '本次package.json' '本次annotations.json' '新的annotated.json'
python E:/Nolva/skills/report-revision-comparison/scripts/build_review.py --package 'annotated.json' --work '新的finalize工作子目录' --out '新的正式HTML路径'
```

新原因必须登记来源和依据，AI对照分析用明确AI来源、不虚构专家或作者动机；判断影响与意见处置各自保留复核状态。摘要可明确待确认，不将候选相似度转换为专业确认。生成后对最终HTML重新运行浏览器检查。
