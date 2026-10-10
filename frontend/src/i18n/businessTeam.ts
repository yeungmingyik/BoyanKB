const zh = {
  title: '团队邀请', workspace: '当前空间：{name}', description: '邀请员工加入当前空间，一起使用授权资料。',
  viewer: '只读员工', permissions: '员工可查看授权资料、进行对话、使用业务技能和下载审校通过的文件；不能编辑知识库或管理权限。',
  invite: '邀请员工', link: '分享邀请链接', email: '邀请已注册员工', emailLabel: '员工的注册邮箱', emailPlaceholder: '请输入已注册账号的邮箱',
  linkHint: '新员工可通过链接注册并加入本空间。请只发给需要加入的员工。', emailHint: '对方需已有账号，登录后可接受邀请。新员工请使用邀请链接。',
  createLink: '创建邀请链接', createEmail: '创建员工邀请', created: '邀请已创建', copy: '复制链接', copied: '链接已复制', copyFailed: '复制失败，请手动选中链接复制。',
  linkOnce: '链接仅在本次创建后显示。离开或刷新页面后不会再次展示，可撤销旧邀请后重新创建。', emailCreated: '员工登录后可在邀请通知中接受邀请。',
  members: '空间员工', membersEmpty: '还没有只读员工加入。', invitations: '我创建的邀请', invitationsEmpty: '你尚未创建邀请。', invitee: '指定员工', joined: '已加入 {count} 人',
  expires: '有效期至 {date}', createdAt: '创建于 {date}', revoke: '撤销邀请', revokeTitle: '撤销这条邀请？', revokeHint: '撤销后无法再通过这条邀请加入。已加入员工的权限不变。', revoked: '邀请已撤销',
  refresh: '刷新', cancel: '取消', unavailable: '当前账号未获授权管理本空间的团队邀请。', loadFailed: '未能加载当前空间的团队信息，请重试。', mutationFailed: '操作未完成，请刷新邀请列表核对结果后重试。',
  denied: '登录状态或团队管理授权已失效。请重新登录；若仍无权限，请联系管理员。', partial: '当前显示 {count} 条邀请，共 {total} 条。', invalidLink: '邀请已创建，但链接无法显示，请撤销后重试。',
  status: { pending: '待加入', accepted: '已接受', declined: '已拒绝', revoked: '已撤销', expired: '已过期' },
}

const en: typeof zh = {
  title: 'Team invitations', workspace: 'Workspace: {name}', description: 'Invite employees to use the resources shared with this workspace.',
  viewer: 'Read-only employee', permissions: 'Employees can read shared resources, chat, use business skills and download approved documents. They cannot edit knowledge bases or manage permissions.',
  invite: 'Invite employees', link: 'Share an invitation link', email: 'Invite an existing account', emailLabel: 'Registered email', emailPlaceholder: 'Enter the employee’s registered email',
  linkHint: 'New employees can register and join through this link. Share it only with intended employees.', emailHint: 'The employee needs an existing account and can accept after signing in. Use a link for new employees.',
  createLink: 'Create invitation link', createEmail: 'Create invitation', created: 'Invitation created', copy: 'Copy link', copied: 'Link copied', copyFailed: 'Could not copy. Select and copy the link manually.',
  linkOnce: 'This link is shown only after creation. Leaving or refreshing hides it; revoke the old invitation before creating a replacement.', emailCreated: 'The employee can accept the invitation after signing in.',
  members: 'Workspace employees', membersEmpty: 'No read-only employees have joined yet.', invitations: 'Invitations I created', invitationsEmpty: 'You have not created any invitations.', invitee: 'Invited employee', joined: '{count} joined',
  expires: 'Expires {date}', createdAt: 'Created {date}', revoke: 'Revoke invitation', revokeTitle: 'Revoke this invitation?', revokeHint: 'This invitation will no longer allow joining. Employees who already joined keep their access.', revoked: 'Invitation revoked',
  refresh: 'Refresh', cancel: 'Cancel', unavailable: 'You are not authorized to manage team invitations for this workspace.', loadFailed: 'Could not load the team. Please retry.', mutationFailed: 'The operation did not complete. Refresh the invitation list to check the result before retrying.',
  denied: 'Your session or team management authorization is no longer active. Sign in again, then contact an administrator if access is still unavailable.', partial: 'Showing {count} of {total} invitations.', invalidLink: 'The invitation was created, but the link cannot be displayed. Revoke it and try again.',
  status: { pending: 'Pending', accepted: 'Accepted', declined: 'Declined', revoked: 'Revoked', expired: 'Expired' },
}

export const businessTeamMessages = { 'zh-CN': zh, 'en-US': en, 'ru-RU': en, 'ko-KR': en, 'ja-JP': en }
