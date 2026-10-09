# 场景与格式选择

场景名称使用 courseware、event-plan、local-promotion-plan、cooperation-proposal、faq-guide。Skill 安装标识分别为 boyan-courseware、boyan-event-plan、boyan-local-promotion-plan、boyan-cooperation-proposal、boyan-faq-guide。格式名称使用 docx、pptx、xlsx、md，Skill 标识为 boyan-docx、boyan-pptx、boyan-xlsx、boyan-md。

| 场景 | 必填输入 | 必需结构 | 推荐格式 |
|---|---|---|---|
| courseware | audience、duration、objectives、localization；localization 可为“无” | 目标、背景、3～5 个知识模块、案例或演示、互动练习、总结、后续行动 | pptx，可选 docx |
| event-plan | city、event_type、purpose、audience、headcount、budget、schedule | 目标、对象、形式、流程、筹备、场地物料、预算、人员、至少 3 项风险、评估 | docx、xlsx，可选 pptx |
| local-promotion-plan | city、business、target_customers、goal、budget_range、period | 目标、客户、价值、渠道、执行、资源预算、评估、风险 | docx 或 pptx，可选 xlsx |
| cooperation-proposal | customer、needs、business、goal | 背景、需求、业务介绍、合作内容、实施、职责、预期价值、沟通事项 | docx 或 pptx |
| faq-guide | audience、topic | 范围、问题分类、问答、注意事项、来源 | md 或 docx |

输入完整只说明可以制作大纲，不代表事实已经核实或用户确认。人物、学校、企业与政策仅使用有权资料中的真实信息，不自行添加名单。必填输入可由用户明确标记待确认，但不能空缺后偷偷推定。

课件使用教学模板，一页一个观点、一般不超过六行，提供讲师备注和预计时长。没有总部案例时标记【待补充案例】，可以提出明确标注的教学演示，不包装成真实合作案例。

合作提案和本地推广面向学校、政府或企业时使用 business 商务模板；突出需求、方案、执行、双方边界与下一步，不使用未经核实的成果数字。活动方案使用 operations 活动执行模板，流程表包括时间、内容、责任和物料。FAQ 使用 business 商务参考模板。课件使用 teaching 教学模板。模板 ID 为系列加格式，例如 teaching-pptx、business-docx、operations-xlsx，固定目录为 branding/templates/，模板版本为 boyan-v1。

Word 使用标题样式、规范列表、可读表格和来源附录。PPT 保留讲师备注、页码及资料来源，不通过缩小字体塞入过量文字。Excel 区分用户输入、预算假设和公式结果，含数量、单价、金额、合计及来源，检查公式计算与合计；无法确认报价时标注【待核实】。Markdown 使用规范标题、列表、表格及来源链接，不嵌入脚本或远程追踪资源。

先调用受控 boyan_document 的 read_skill 操作，skill_name 使用上方 boyan- 前缀的场景与格式名称。指南必须已经在当前租户安装且通过版本及 SHA 校验；读取失败时明确说明，不假装已加载。propose 提交 scene、format、outline_markdown、requirements、document 与 sources。document 包含 title、sections；每个 section 可包含 heading、body、bullets、tables（headers 与 rows）、notes。sources 只包含检索返回的 chunk_id，服务端读取真实来源，不接受模型自己撰写的来源正文。xlsx 可附 budget.items，每项 label、quantity、unit_price；金额为已说明的预算假设或真实有依据报价。

status 查询服务端状态；generate 使用已确认的 job_id 与 outline_hash。参数的具体字段以工具 Schema 为准。模板由服务端白名单和场景决定，不接受任意模板路径、外部 URL、Shell 命令或代码。没有工具或必要模板时明确说明暂时不能生成正式文件，可以保留待确认大纲。只有 published 状态且提供真实下载地址时才交付；awaiting_confirmation 等待确认，approved 才可请求生成，generating/reviewing 表示处理中，blocked/failed 不得交付为通过版本，superseded 表示需使用新版本。
