# 内置智能体提示词中文覆盖

中文提示词通过镜像中的配置覆盖生效，不修改上游 `config/prompt_templates` 原文件，也不修改既有 `boyan/v1` 业务预设或九项技能的内容字节。

| 覆盖文件 | 镜像目标 | 模板数 |
| --- | --- | --- |
| `branding/prompt_templates/agent_system_prompt.yaml` | `/app/config/prompt_templates/agent_system_prompt.yaml` | 7 |
| `branding/prompt_templates/system_prompt.yaml` | `/app/config/prompt_templates/system_prompt.yaml` | 6 |

`docker/Dockerfile.app-boyankb` 在最终镜像复制上述文件。应用从配置文件所在目录的 `prompt_templates` 读取模板，按原 ID 解析内置智能体引用；快速问答的 `default_kb` 来自第二份文件。镜像构建并重新部署后生效。若部署另行挂载整个 `/app/config` 或模板目录，挂载文件会覆盖镜像中的文件，需要核对实际挂载路径。

## 范围

模板的默认名称、描述、中文语言元数据及完整提示词正文均已中文化，身份统一为 BoyanKB。保留其他语言的名称和描述选项。正文采用中文，不根据界面语言自动翻译；原有 `{{language}}` 变量和用户显式语言要求仍保留。

| 模板 ID | 中文默认名称 |
| --- | --- |
| `pure_agent` | 纯智能体 |
| `progressive_rag_agent` | 渐进式知识检索智能体 |
| `data_analyst` | 数据分析师 |
| `wiki_researcher` | 维基研究员 |
| `wiki_fixer` | 维基修复系统提示词 |
| `hybrid_rag_wiki_agent` | 知识检索与维基混合智能体 |
| `skill_installer` | 技能安装系统提示词 |
| `default_kb` | 知识库问答助手 |
| `expert_assistant` | 领域专家助手 |
| `customer_service` | 客服助手 |
| `technical_support` | 技术支持 |
| `pure_chat` | 通用对话 |
| `web_search_assistant` | 网络搜索助手 |

保留 ID、mode、default、知识库和网络搜索能力字段、所有模板变量、工具参数名、文档句柄、SQL 表名和来源引用格式。内容保留原有检索、事实核验、只读查询、修复授权范围与技能安装隔离规则。`skill_installer` 的翻译不会启用沙箱或 shell，也不会改变工作空间的脚本禁用政策。

## 与业务预设的区别

原生内置智能体使用原生配置和权限流程。提示词中文化不会将其变为合作伙伴受管智能体，也不会自动加入业务工作流的人类确认、固定渲染器、受限技能安装索引或审校发布规则。

“业务导师、落地方案官、内容审校官”属于合作伙伴开通流程创建的三个受管智能体，其绑定和 `boyan/v1` 内容不在本次覆盖范围内。总部没有开通记录时，不会因为中文覆盖获得这三个智能体或九项业务技能的可用状态。

已经存入数据库的自定义智能体提示词不会被此覆盖批量改写；现存对象若保存了旧版完整文本，仍保留该文本。只有引用启动时模板的内置智能体，以及后续在编辑器中选取模板的行为，使用新内容。

## 验证

`internal/config/branding_prompt_templates_test.go` 检查 13 个模板均能被实际加载器读取、默认及中文元数据一致、正文中文与 BoyanKB 身份、变量集合不变，以及其他机器字段和语言元数据不变；同时核验关键工具名、来源句柄、只读和隔离规则仍存在。

本轮 `internal/config` 全包测试已通过，耗时 1.694 秒。

```text
go test -ldflags="-X google.golang.org/protobuf/reflect/protoregistry.conflictPolicy=warn" ./internal/config -run ChinesePromptOverrides -count=1
```

需要人工验证：完成镜像构建和部署后，在内置智能体编辑页及新建智能体模板选择中检查名称、描述和正文；确认快速问答的知识库模板和智能推理模板均为中文。不要仅根据仓库覆盖文件存在就认定运行实例已加载。测试智能体改名及业务技能目录的独立验收见 [业务技能目录接口](skills-catalog.md)。
