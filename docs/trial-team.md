# 伙伴负责人邀请员工

“团队邀请”入口位于左侧导航，页面路径为 `/platform/team`。只有后端确认当前用户是当前空间已开通业务预设的指定团队负责人时才显示入口。前端不使用 Owner/Admin 角色推断授权，也不会把负责人伪装成 Owner。手动访问页面时，未授权账号只看到无权限提示，不展示成员或管理操作。

负责人和员工使用空间的 Viewer 权限。员工可查看获授权的资料、进行对话、使用业务技能及下载审校通过的文件；不能编辑知识库、修改 Agent、调整共享授权或提升成员角色。业务事实检索、生成文件大纲确认、自动审校及下载授权仍由原有服务端流程执行。团队邀请能力仅增加邀请本空间 Viewer 的权限，不扩大知识库写入范围。

## 使用流程

1. 管理员完成空间开通并配置指定负责人。负责人登录后选择该空间，打开“团队邀请”，核对页面显示的空间名称。
2. 新员工选择“分享邀请链接”，点击“创建邀请链接”，再主动点击“复制链接”发送给需要加入的员工。员工打开链接后注册或登录，按已有邀请流程加入本空间。
3. 已有账号的员工可使用“邀请已注册员工”，输入其注册邮箱。邀请创建后，对方登录并在邀请通知中接受；页面不会声称已经发送邮件。
4. 在“空间员工”查看本空间的 active Viewer 姓名；在“我创建的邀请”查看本人邀请的状态、有效期和链接加入人数。需要停止新员工通过旧邀请加入时，点击“撤销邀请”并确认。撤销邀请不会移除已经加入的员工。

邀请角色固定为只读员工，没有角色选择器。页面不提供编辑成员、删除成员或改写知识库的入口。成员列表不包含总部 Owner 信息或邮箱；邀请列表不返回 token、URL 或受邀邮箱，仅显示可用的受邀人姓名和状态。列表目前返回最新 100 条，超过时页面提示显示数量和总数，后端未提供分页查询。

新建链接只在本次响应后保留在页面内存里，不写入 localStorage、日志或路由参数，也不会自动复制。离开、刷新、切换空间或授权失效后，页面清除链接。需要再次分发时可撤销旧邀请并创建新链接。复制按钮只接受当前站点下的 `/register?token=...`，拒绝其他域名、重复 token、额外跳转参数和脚本 URL。持有链接的人可以按链接加入，负责人应只向预期员工分发。

## 请求与权限边界

前端读取 `GET /api/v1/business-workflow/team` 的 `available` 决定是否展示入口，成员管理请求仅使用同一 `/team` 范围内的邀请接口。请求明确捕获发起时的用户身份和空间 ID，并使用独立 transport，避免全局 Axios 拦截器在切空间后覆盖 `X-Tenant-ID`。切空间会立即清除状态；旧请求返回后不能覆盖新空间数据或重新显示旧邀请链接。已发出的邀请请求仍针对点击时的原空间，切换页面不会撤回已经完成的服务端操作。

创建或撤销失败时不自动重试，避免重复邀请；页面要求先刷新列表核对结果。401/403 会移除本地管理状态。服务端每次核对负责人绑定与 Viewer 成员状态，才是最终授权边界；隐藏入口并不替代后端验证。负责人资格撤销后旧团队邀请的接受行为也必须由服务端拒绝。

## 验证

本轮未创建真实邀请、未写业务数据、未部署。中文页面和共享 i18n 键已接入；其他未单独翻译的语言使用英语团队文案。8 项前端状态与 transport 测试、13 项 i18n 检查通过，真实浏览器验证等待最终镜像部署。

已有依赖镜像下可执行：

```powershell
docker run --rm --network none --mount type=bind,source=D:/BoyanKB/WeKnora/frontend/src,target=/workspace/frontend/src,readonly boyankb/frontend-business-skills-check:local npm test -- src/composables/businessTeam.test.ts
docker run --rm --network none --mount type=bind,source=D:/BoyanKB/WeKnora/frontend/src,target=/workspace/frontend/src,readonly boyankb/frontend-business-skills-check:local npm run check-i18n
docker run --rm --network none --mount type=bind,source=D:/BoyanKB/WeKnora/frontend/src,target=/workspace/frontend/src,readonly boyankb/frontend-business-skills-check:local npm exec -- vue-tsc --noEmit -p tsconfig.app.json
```

部署后的浏览器验收应覆盖：指定负责人入口可见、普通员工及其他空间入口隐藏、桌面和 390px 页面可滚动且不溢出、主动复制、切空间时旧响应不回显，以及创建和撤销的真实服务端边界。创建真实邀请需使用明确授权的验收账号及空间。

主要文件为 `frontend/src/api/business-team.ts`、`frontend/src/api/team-transport.ts`、`frontend/src/composables/businessTeam.ts`、`frontend/src/stores/businessTeam.ts`、`frontend/src/views/team/TeamInvitations.vue`。导航、路由和 i18n 仅做团队入口所需的局部接入；平台容器的窄屏最小宽度调整仅作用于团队页。
