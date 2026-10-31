//! Complete compile-time message catalogs for supported CLI locales.

use std::collections::BTreeMap;
use std::env;
use std::str::FromStr;

/// Locales shipped with the CLI.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum Locale {
    /// English fallback and default.
    #[default]
    En,
    /// Simplified Chinese.
    ZhCn,
}

impl Locale {
    /// Resolve the first supported locale from standard process variables.
    #[must_use]
    pub fn from_environment() -> Self {
        ["LC_ALL", "LC_MESSAGES", "LANG"]
            .into_iter()
            .filter_map(|name| env::var(name).ok())
            .find_map(|value| Self::from_str(&value).ok())
            .unwrap_or_default()
    }

    /// Return a stable BCP 47-style identifier.
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::En => "en",
            Self::ZhCn => "zh-CN",
        }
    }

    /// Return the localized catalog value for a stable message identifier.
    #[must_use]
    pub const fn message(self, key: MessageKey) -> &'static str {
        match (self, key) {
            (Self::En, MessageKey::VersionLine) => {
                "squid {version} ({target}; submission protocol {minimum}..={maximum})"
            }
            (Self::ZhCn, MessageKey::VersionLine) => {
                "squid {version}（{target}；投稿协议 {minimum}..={maximum}）"
            }
            (Self::En, MessageKey::CompletionRequiresHumanOutput) => {
                "shell completions are raw text and cannot use JSON output"
            }
            (Self::ZhCn, MessageKey::CompletionRequiresHumanOutput) => {
                "Shell 补全脚本是原始文本，不能使用 JSON 输出"
            }
            (Self::En, MessageKey::UnsupportedLocale) => "unsupported locale; use en or zh-CN",
            (Self::ZhCn, MessageKey::UnsupportedLocale) => "不支持该语言；请使用 en 或 zh-CN",
            (Self::En, MessageKey::LocalIoFailed) => "local input or output failed",
            (Self::ZhCn, MessageKey::LocalIoFailed) => "本地输入或输出失败",
            (Self::En, MessageKey::SuggestedCheckFilesystem) => {
                "check the destination path and filesystem permissions"
            }
            (Self::ZhCn, MessageKey::SuggestedCheckFilesystem) => "请检查目标路径和文件系统权限",
            (Self::En, MessageKey::ProfileAdded) => "added profile {name} for {origin}",
            (Self::ZhCn, MessageKey::ProfileAdded) => "已为 {origin} 添加配置 {name}",
            (Self::En, MessageKey::ProfileSelected) => "selected profile {name}",
            (Self::ZhCn, MessageKey::ProfileSelected) => "已选择配置 {name}",
            (Self::En, MessageKey::ProfileRemoved) => "removed local profile {name}",
            (Self::ZhCn, MessageKey::ProfileRemoved) => "已移除本地配置 {name}",
            (Self::En, MessageKey::ProfileRemovalCancelled) => "kept profile {name}",
            (Self::ZhCn, MessageKey::ProfileRemovalCancelled) => "已保留配置 {name}",
            (Self::En, MessageKey::ProfileListEmpty) => "no profiles configured",
            (Self::ZhCn, MessageKey::ProfileListEmpty) => "尚未配置任何配置文件",
            (Self::En, MessageKey::ProfileDetails) => {
                "{active}{name}\n  origin: {origin}\n  locale: {locale}\n  editor: {editor}\n  update checks: {update_checks}\n  custom CA: {ca_certificate}"
            }
            (Self::ZhCn, MessageKey::ProfileDetails) => {
                "{active}{name}\n  来源：{origin}\n  语言：{locale}\n  编辑器：{editor}\n  更新检查：{update_checks}\n  自定义 CA：{ca_certificate}"
            }
            (Self::En, MessageKey::ProfileActiveMarker) => "* ",
            (Self::ZhCn, MessageKey::ProfileActiveMarker) => "* ",
            (Self::En, MessageKey::ProfileInactiveMarker) => "  ",
            (Self::ZhCn, MessageKey::ProfileInactiveMarker) => "  ",
            (Self::En, MessageKey::ProfileDefaultLocale) => "automatic",
            (Self::ZhCn, MessageKey::ProfileDefaultLocale) => "自动",
            (Self::En, MessageKey::ProfileNoCustomCa) => "system trust store",
            (Self::ZhCn, MessageKey::ProfileNoCustomCa) => "系统信任库",
            (Self::En, MessageKey::Enabled) => "enabled",
            (Self::ZhCn, MessageKey::Enabled) => "已启用",
            (Self::En, MessageKey::Disabled) => "disabled",
            (Self::ZhCn, MessageKey::Disabled) => "已禁用",
            (Self::En, MessageKey::EditorTui) => "terminal UI",
            (Self::ZhCn, MessageKey::EditorTui) => "终端界面",
            (Self::En, MessageKey::EditorPrompt) => "prompts",
            (Self::ZhCn, MessageKey::EditorPrompt) => "逐项提示",
            (Self::En, MessageKey::ConfirmProfileRemoval) => {
                "remove local profile {name}? Type yes to continue: "
            }
            (Self::ZhCn, MessageKey::ConfirmProfileRemoval) => {
                "要移除本地配置 {name} 吗？请输入 yes 继续："
            }
            (Self::En, MessageKey::ProfileConfirmationRequired) => {
                "profile removal requires interactive confirmation"
            }
            (Self::ZhCn, MessageKey::ProfileConfirmationRequired) => "移除配置需要交互式确认",
            (Self::En, MessageKey::SuggestedUseYes) => {
                "review the profile name, then pass --yes in non-interactive use"
            }
            (Self::ZhCn, MessageKey::SuggestedUseYes) => {
                "请核对配置名称，然后在非交互环境中传入 --yes"
            }
            (Self::En, MessageKey::InvalidProfileName) => {
                "invalid profile name; start with a lowercase letter and use at most 32 lowercase letters, digits, '-' or '_'"
            }
            (Self::ZhCn, MessageKey::InvalidProfileName) => {
                "配置名称无效；请以小写字母开头，并仅使用最多 32 个小写字母、数字、- 或 _"
            }
            (Self::En, MessageKey::ProfileAlreadyExists) => "profile {name} already exists",
            (Self::ZhCn, MessageKey::ProfileAlreadyExists) => "配置 {name} 已存在",
            (Self::En, MessageKey::ProfileNotFound) => "profile {name} was not found",
            (Self::ZhCn, MessageKey::ProfileNotFound) => "找不到配置 {name}",
            (Self::En, MessageKey::NoActiveProfile) => "no active profile is configured",
            (Self::ZhCn, MessageKey::NoActiveProfile) => "尚未选择活动配置",
            (Self::En, MessageKey::ProfileTrustRequired) => {
                "adding an API origin requires an explicit --trust decision"
            }
            (Self::ZhCn, MessageKey::ProfileTrustRequired) => {
                "添加 API 来源必须通过 --trust 明确信任"
            }
            (Self::En, MessageKey::ProfileLocaleInvalid) => {
                "profile default locale must be en or zh-CN"
            }
            (Self::ZhCn, MessageKey::ProfileLocaleInvalid) => "配置的默认语言必须为 en 或 zh-CN",
            (Self::En, MessageKey::ProfileCaInvalid) => {
                "custom CA certificate must be a regular non-symlink file"
            }
            (Self::ZhCn, MessageKey::ProfileCaInvalid) => {
                "自定义 CA 证书必须是常规文件，不能是符号链接"
            }
            (Self::En, MessageKey::ProfileConfigInvalid) => {
                "local profile configuration is invalid or unsupported"
            }
            (Self::ZhCn, MessageKey::ProfileConfigInvalid) => "本地配置文件无效或不受支持",
            (Self::En, MessageKey::ProfileStorageFailed) => {
                "local profile configuration could not be read or written"
            }
            (Self::ZhCn, MessageKey::ProfileStorageFailed) => "无法读取或写入本地配置文件",
            (Self::En, MessageKey::LocalSecurityStateFailed) => {
                "local credentials or encrypted state could not be accessed"
            }
            (Self::ZhCn, MessageKey::LocalSecurityStateFailed) => "无法访问本地凭据或加密状态",
            (Self::En, MessageKey::ProfileSymlinkRejected) => {
                "refused a symbolic link in the CLI state path"
            }
            (Self::ZhCn, MessageKey::ProfileSymlinkRejected) => "已拒绝 CLI 状态路径中的符号链接",
            (Self::En, MessageKey::OriginInvalidUrl) => "API origin is not a valid absolute URL",
            (Self::ZhCn, MessageKey::OriginInvalidUrl) => "API 来源不是有效的绝对 URL",
            (Self::En, MessageKey::OriginSchemeInvalid) => {
                "API origin scheme must be http or https"
            }
            (Self::ZhCn, MessageKey::OriginSchemeInvalid) => "API 来源协议必须为 http 或 https",
            (Self::En, MessageKey::OriginCredentialsRejected) => {
                "API origin must not contain a username or password"
            }
            (Self::ZhCn, MessageKey::OriginCredentialsRejected) => "API 来源不能包含用户名或密码",
            (Self::En, MessageKey::OriginComponentsRejected) => {
                "API origin must not contain a path, query, or fragment"
            }
            (Self::ZhCn, MessageKey::OriginComponentsRejected) => {
                "API 来源不能包含路径、查询参数或片段"
            }
            (Self::En, MessageKey::OriginHostMissing) => "API origin must contain a host",
            (Self::ZhCn, MessageKey::OriginHostMissing) => "API 来源必须包含主机名",
            (Self::En, MessageKey::OriginPortMissing) => "API origin port could not be resolved",
            (Self::ZhCn, MessageKey::OriginPortMissing) => "无法确定 API 来源端口",
            (Self::En, MessageKey::OriginHttpsRequired) => {
                "HTTPS is required outside literal loopback development origins"
            }
            (Self::ZhCn, MessageKey::OriginHttpsRequired) => {
                "除字面量回环开发地址外，API 来源必须使用 HTTPS"
            }
            (Self::En, MessageKey::AuthApprovalInstructions) => {
                "approve this CLI device in your browser:\n  {url}\n  user code: {code}\n  public-key fingerprint: {fingerprint}\nwaiting for approval…"
            }
            (Self::ZhCn, MessageKey::AuthApprovalInstructions) => {
                "请在浏览器中批准此 CLI 设备：\n  {url}\n  用户代码：{code}\n  公钥指纹：{fingerprint}\n正在等待批准…"
            }
            (Self::En, MessageKey::AuthLoginSucceeded) => {
                "signed in device {device_id}; session expires {expires_at}"
            }
            (Self::ZhCn, MessageKey::AuthLoginSucceeded) => {
                "设备 {device_id} 已登录；会话到期时间为 {expires_at}"
            }
            (Self::En, MessageKey::AuthSessionRenewed) => {
                "renewed device {device_id}; session expires {expires_at}"
            }
            (Self::ZhCn, MessageKey::AuthSessionRenewed) => {
                "设备 {device_id} 的会话已续期；到期时间为 {expires_at}"
            }
            (Self::En, MessageKey::AuthLoggedOut) => "signed out this CLI session",
            (Self::ZhCn, MessageKey::AuthLoggedOut) => "已退出此 CLI 会话",
            (Self::En, MessageKey::AuthAlreadyLoggedOut) => "this profile is already signed out",
            (Self::ZhCn, MessageKey::AuthAlreadyLoggedOut) => "此配置已处于退出状态",
            (Self::En, MessageKey::AuthStatusSignedIn) => {
                "local session for device {device_id} expires {expires_at}"
            }
            (Self::ZhCn, MessageKey::AuthStatusSignedIn) => {
                "设备 {device_id} 的本地会话到期时间为 {expires_at}"
            }
            (Self::En, MessageKey::AuthStatusSignedOut) => "no local CLI session is available",
            (Self::ZhCn, MessageKey::AuthStatusSignedOut) => "没有可用的本地 CLI 会话",
            (Self::En, MessageKey::AuthFallbackWarning) => {
                "warning: credentials are stored in owner-readable files because native credential storage was unavailable"
            }
            (Self::ZhCn, MessageKey::AuthFallbackWarning) => {
                "警告：由于系统凭据存储不可用，凭据保存在仅所有者可读的文件中"
            }
            (Self::En, MessageKey::AuthWaitTimedOut) => {
                "browser approval did not complete before the local wait timeout"
            }
            (Self::ZhCn, MessageKey::AuthWaitTimedOut) => "浏览器批准未在本地等待期限内完成",
            (Self::En, MessageKey::AuthStateFailed) => {
                "CLI authorization state could not be read or written"
            }
            (Self::ZhCn, MessageKey::AuthStateFailed) => "无法读取或写入 CLI 授权状态",
            (Self::En, MessageKey::AuthLoginRequired) => "this profile is not signed in",
            (Self::ZhCn, MessageKey::AuthLoginRequired) => "此配置尚未登录",
            (Self::En, MessageKey::SuggestedLogin) => "run squid auth login, then retry",
            (Self::ZhCn, MessageKey::SuggestedLogin) => "请运行 squid auth login，然后重试",
            (Self::En, MessageKey::ApiRequestFailed) => "the Squid API request failed",
            (Self::ZhCn, MessageKey::ApiRequestFailed) => "Squid API 请求失败",
            (Self::En, MessageKey::SuggestedRetry) => {
                "check the selected profile and network, then retry"
            }
            (Self::ZhCn, MessageKey::SuggestedRetry) => "请检查所选配置和网络，然后重试",
            (Self::En, MessageKey::SuggestedApproveDevice) => {
                "start login again and approve the displayed device before it expires"
            }
            (Self::ZhCn, MessageKey::SuggestedApproveDevice) => {
                "请重新开始登录，并在显示的设备过期前批准它"
            }
            (Self::En, MessageKey::DraftListEmpty) => "no active synchronized drafts",
            (Self::ZhCn, MessageKey::DraftListEmpty) => "没有活动的同步草稿",
            (Self::En, MessageKey::ErrorListEmpty) => {
                "no stored errors within the retention window"
            }
            (Self::ZhCn, MessageKey::ErrorListEmpty) => "保留期内没有已存储的错误",
            (Self::En, MessageKey::ErrorWorkLost) => {
                "this job was abandoned; nothing will retry it"
            }
            (Self::ZhCn, MessageKey::ErrorWorkLost) => "此任务已被放弃；不会再重试",
            (Self::En, MessageKey::ErrorLogTailHeading) => "log tail:",
            (Self::ZhCn, MessageKey::ErrorLogTailHeading) => "日志尾部：",
            (Self::En, MessageKey::ErrorReferenceAmbiguous) => {
                "warning: several reports share this reference; showing the newest"
            }
            (Self::ZhCn, MessageKey::ErrorReferenceAmbiguous) => {
                "警告：多个报告共用此引用；显示最新的一个"
            }
            (Self::En, MessageKey::InvalidErrorReference) => "the error reference is not usable",
            (Self::ZhCn, MessageKey::InvalidErrorReference) => "错误引用不可用",
            (Self::En, MessageKey::DraftCreated) => "created draft {draft_id} for {category}",
            (Self::ZhCn, MessageKey::DraftCreated) => "已为 {category} 创建草稿 {draft_id}",
            (Self::En, MessageKey::DraftChanged) => {
                "updated draft {draft_id} to revision {revision}"
            }
            (Self::ZhCn, MessageKey::DraftChanged) => "已将草稿 {draft_id} 更新到修订版 {revision}",
            (Self::En, MessageKey::DraftDeleted) => "deleted draft {draft_id}",
            (Self::ZhCn, MessageKey::DraftDeleted) => "已删除草稿 {draft_id}",
            (Self::En, MessageKey::DraftDeletionCancelled) => "kept draft {draft_id}",
            (Self::ZhCn, MessageKey::DraftDeletionCancelled) => "已保留草稿 {draft_id}",
            (Self::En, MessageKey::DraftSubmitted) => "draft {draft_id} finalization is {status}",
            (Self::ZhCn, MessageKey::DraftSubmitted) => "草稿 {draft_id} 的最终处理状态为 {status}",
            (Self::En, MessageKey::DraftConfirmationRequired) => {
                "draft deletion requires interactive confirmation"
            }
            (Self::ZhCn, MessageKey::DraftConfirmationRequired) => "删除草稿需要交互式确认",
            (Self::En, MessageKey::ConfirmDraftDeletion) => {
                "delete draft {draft_id} and its private pending media? Type the draft ID to continue: "
            }
            (Self::ZhCn, MessageKey::ConfirmDraftDeletion) => {
                "要删除草稿 {draft_id} 及其待处理的私有媒体吗？请输入草稿 ID 继续："
            }
            (Self::En, MessageKey::SuggestedUseDraftYes) => {
                "review the draft ID, then pass --yes in non-interactive use"
            }
            (Self::ZhCn, MessageKey::SuggestedUseDraftYes) => {
                "请核对草稿 ID，然后在非交互环境中传入 --yes"
            }
            (Self::En, MessageKey::InvalidJsonValue) => "field value must be valid JSON",
            (Self::ZhCn, MessageKey::InvalidJsonValue) => "字段值必须是有效的 JSON",
            (Self::En, MessageKey::InvalidFormContract) => {
                "the server returned an invalid or incompatible submission form"
            }
            (Self::ZhCn, MessageKey::InvalidFormContract) => "服务器返回了无效或不兼容的投稿表单",
            (Self::En, MessageKey::FormRequiresWeb) => {
                "this draft contains a required field the selected terminal renderer cannot display"
            }
            (Self::ZhCn, MessageKey::FormRequiresWeb) => {
                "此草稿包含所选终端渲染器无法显示的必填字段"
            }
            (Self::En, MessageKey::SuggestedContinueOnWeb) => {
                "continue this synchronized draft on the Redstone Squid website"
            }
            (Self::ZhCn, MessageKey::SuggestedContinueOnWeb) => {
                "请在 Redstone Squid 网站上继续编辑此同步草稿"
            }
            (Self::En, MessageKey::FormInteractionRequired) => {
                "interactive form editing requires a terminal"
            }
            (Self::ZhCn, MessageKey::FormInteractionRequired) => "交互式表单编辑需要终端",
            (Self::En, MessageKey::FormAnswerInvalid) => {
                "the answer does not satisfy this field's constraints; try again"
            }
            (Self::ZhCn, MessageKey::FormAnswerInvalid) => "回答不符合字段约束；请重试",
            (Self::En, MessageKey::FormEditingCancelled) => {
                "form editing was cancelled; the synchronized draft was kept"
            }
            (Self::ZhCn, MessageKey::FormEditingCancelled) => "表单编辑已取消；同步草稿已保留",
            (Self::En, MessageKey::FinalizationWaitTimedOut) => {
                "finalization is still running after the local wait timeout"
            }
            (Self::ZhCn, MessageKey::FinalizationWaitTimedOut) => {
                "本地等待超时后，最终处理仍在运行"
            }
            (Self::En, MessageKey::SuggestedCheckStatus) => {
                "run squid draft status with this draft ID"
            }
            (Self::ZhCn, MessageKey::SuggestedCheckStatus) => {
                "请使用此草稿 ID 运行 squid draft status"
            }
            (Self::En, MessageKey::MediaListEmpty) => "this draft has no retained media",
            (Self::ZhCn, MessageKey::MediaListEmpty) => "此草稿没有保留的媒体",
            (Self::En, MessageKey::MediaUploaded) => {
                "media {upload_id} for draft {draft_id} is {status}"
            }
            (Self::ZhCn, MessageKey::MediaUploaded) => {
                "草稿 {draft_id} 的媒体 {upload_id} 状态为 {status}"
            }
            (Self::En, MessageKey::MediaDiscarded) => "discarded media {upload_id}",
            (Self::ZhCn, MessageKey::MediaDiscarded) => "已丢弃媒体 {upload_id}",
            (Self::En, MessageKey::MediaDiscardCancelled) => "kept media {upload_id}",
            (Self::ZhCn, MessageKey::MediaDiscardCancelled) => "已保留媒体 {upload_id}",
            (Self::En, MessageKey::ConfirmMediaDiscard) => {
                "discard media {upload_id}? Type the upload ID to continue: "
            }
            (Self::ZhCn, MessageKey::ConfirmMediaDiscard) => {
                "要丢弃媒体 {upload_id} 吗？请输入上传 ID 继续："
            }
            (Self::En, MessageKey::MediaConfirmationRequired) => {
                "media discard requires interactive confirmation"
            }
            (Self::ZhCn, MessageKey::MediaConfirmationRequired) => "丢弃媒体需要交互式确认",
            (Self::En, MessageKey::SuggestedUseMediaYes) => {
                "review the upload ID, then pass --yes in non-interactive use"
            }
            (Self::ZhCn, MessageKey::SuggestedUseMediaYes) => {
                "请核对上传 ID，然后在非交互环境中传入 --yes"
            }
            (Self::En, MessageKey::MediaInputInvalid) => {
                "the media source path, type, or size is invalid"
            }
            (Self::ZhCn, MessageKey::MediaInputInvalid) => "媒体源路径、类型或大小无效",
            (Self::En, MessageKey::MediaContractInvalid) => {
                "the server returned invalid draft media data"
            }
            (Self::ZhCn, MessageKey::MediaContractInvalid) => "服务器返回了无效的草稿媒体数据",
            (Self::En, MessageKey::MediaWaitTimedOut) => {
                "media processing is still running after the local wait timeout"
            }
            (Self::ZhCn, MessageKey::MediaWaitTimedOut) => "本地等待超时后，媒体处理仍在运行",
            (Self::En, MessageKey::SuggestedCheckMediaStatus) => {
                "run squid media status with this draft and upload ID"
            }
            (Self::ZhCn, MessageKey::SuggestedCheckMediaStatus) => {
                "请使用此草稿和上传 ID 运行 squid media status"
            }
            (Self::En, MessageKey::FormBooleanPrompt) => "[y/n] ",
            (Self::ZhCn, MessageKey::FormBooleanPrompt) => "[是/否] ",
            (Self::En, MessageKey::FormRepeatablePrompt) => {
                "enter one value per line; submit a blank line when finished"
            }
            (Self::ZhCn, MessageKey::FormRepeatablePrompt) => "每行输入一个值；完成后提交空行",
            (Self::En, MessageKey::TuiAppTitle) => "Redstone Squid",
            (Self::ZhCn, MessageKey::TuiAppTitle) => "红石鱿鱼",
            (Self::En, MessageKey::TuiHelpTitle) => "Help",
            (Self::ZhCn, MessageKey::TuiHelpTitle) => "帮助",
            (Self::En, MessageKey::TuiAnswerTitle) => "Answer",
            (Self::ZhCn, MessageKey::TuiAnswerTitle) => "回答",
            (Self::En, MessageKey::TuiChooseOneTitle) => "Choose one",
            (Self::ZhCn, MessageKey::TuiChooseOneTitle) => "请选择一项",
            (Self::En, MessageKey::TuiChooseManyTitle) => "Choose values",
            (Self::ZhCn, MessageKey::TuiChooseManyTitle) => "请选择多项",
            (Self::En, MessageKey::TuiInvalidAnswer) => {
                "Answer does not satisfy this field's constraints"
            }
            (Self::ZhCn, MessageKey::TuiInvalidAnswer) => "回答不符合此字段的限制",
            (Self::En, MessageKey::TuiBooleanUnset) => "Not selected",
            (Self::ZhCn, MessageKey::TuiBooleanUnset) => "尚未选择",
            (Self::En, MessageKey::TuiBooleanYes) => "Yes",
            (Self::ZhCn, MessageKey::TuiBooleanYes) => "是",
            (Self::En, MessageKey::TuiBooleanNo) => "No",
            (Self::ZhCn, MessageKey::TuiBooleanNo) => "否",
            (Self::En, MessageKey::TuiFooterText) => "Enter submit · Esc/Ctrl-C cancel",
            (Self::ZhCn, MessageKey::TuiFooterText) => "Enter 提交 · Esc/Ctrl-C 取消",
            (Self::En, MessageKey::TuiFooterMultiline) => {
                "Shift/Alt-Enter newline · Enter submit · Esc/Ctrl-C cancel"
            }
            (Self::ZhCn, MessageKey::TuiFooterMultiline) => {
                "Shift/Alt-Enter 换行 · Enter 提交 · Esc/Ctrl-C 取消"
            }
            (Self::En, MessageKey::TuiFooterBoolean) => {
                "Y/N choose · Enter submit · Esc/Ctrl-C cancel"
            }
            (Self::ZhCn, MessageKey::TuiFooterBoolean) => "Y/N 选择 · Enter 提交 · Esc/Ctrl-C 取消",
            (Self::En, MessageKey::TuiFooterSingleChoice) => {
                "↑/↓ or J/K choose · Enter submit · Esc/Ctrl-C cancel"
            }
            (Self::ZhCn, MessageKey::TuiFooterSingleChoice) => {
                "↑/↓ 或 J/K 选择 · Enter 提交 · Esc/Ctrl-C 取消"
            }
            (Self::En, MessageKey::TuiFooterMultipleChoice) => {
                "↑/↓ or J/K move · Space toggle · Enter submit · Esc/Ctrl-C cancel"
            }
            (Self::ZhCn, MessageKey::TuiFooterMultipleChoice) => {
                "↑/↓ 或 J/K 移动 · 空格切换 · Enter 提交 · Esc/Ctrl-C 取消"
            }
        }
    }
}

impl FromStr for Locale {
    type Err = UnsupportedLocale;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let normalized = value
            .split(['.', '@'])
            .next()
            .unwrap_or(value)
            .replace('_', "-")
            .to_ascii_lowercase();
        if normalized == "c"
            || normalized == "posix"
            || normalized == "en"
            || normalized.starts_with("en-")
        {
            return Ok(Self::En);
        }
        if normalized == "zh-cn" || normalized == "zh-hans" || normalized.starts_with("zh-hans-") {
            return Ok(Self::ZhCn);
        }
        Err(UnsupportedLocale)
    }
}

/// Stable identifiers whose translations must be exhaustive for every locale.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MessageKey {
    VersionLine,
    CompletionRequiresHumanOutput,
    UnsupportedLocale,
    LocalIoFailed,
    SuggestedCheckFilesystem,
    ProfileAdded,
    ProfileSelected,
    ProfileRemoved,
    ProfileRemovalCancelled,
    ProfileListEmpty,
    ProfileDetails,
    ProfileActiveMarker,
    ProfileInactiveMarker,
    ProfileDefaultLocale,
    ProfileNoCustomCa,
    Enabled,
    Disabled,
    EditorTui,
    EditorPrompt,
    ConfirmProfileRemoval,
    ProfileConfirmationRequired,
    SuggestedUseYes,
    InvalidProfileName,
    ProfileAlreadyExists,
    ProfileNotFound,
    NoActiveProfile,
    ProfileTrustRequired,
    ProfileLocaleInvalid,
    ProfileCaInvalid,
    ProfileConfigInvalid,
    ProfileStorageFailed,
    LocalSecurityStateFailed,
    ProfileSymlinkRejected,
    OriginInvalidUrl,
    OriginSchemeInvalid,
    OriginCredentialsRejected,
    OriginComponentsRejected,
    OriginHostMissing,
    OriginPortMissing,
    OriginHttpsRequired,
    AuthApprovalInstructions,
    AuthLoginSucceeded,
    AuthSessionRenewed,
    AuthLoggedOut,
    AuthAlreadyLoggedOut,
    AuthStatusSignedIn,
    AuthStatusSignedOut,
    AuthFallbackWarning,
    AuthWaitTimedOut,
    AuthStateFailed,
    AuthLoginRequired,
    SuggestedLogin,
    ApiRequestFailed,
    SuggestedRetry,
    SuggestedApproveDevice,
    DraftListEmpty,
    ErrorListEmpty,
    ErrorLogTailHeading,
    ErrorWorkLost,
    ErrorReferenceAmbiguous,
    InvalidErrorReference,
    DraftCreated,
    DraftChanged,
    DraftDeleted,
    DraftDeletionCancelled,
    DraftSubmitted,
    DraftConfirmationRequired,
    ConfirmDraftDeletion,
    SuggestedUseDraftYes,
    InvalidJsonValue,
    InvalidFormContract,
    FormRequiresWeb,
    SuggestedContinueOnWeb,
    FormInteractionRequired,
    FormAnswerInvalid,
    FormEditingCancelled,
    FinalizationWaitTimedOut,
    SuggestedCheckStatus,
    MediaListEmpty,
    MediaUploaded,
    MediaDiscarded,
    MediaDiscardCancelled,
    ConfirmMediaDiscard,
    MediaConfirmationRequired,
    SuggestedUseMediaYes,
    MediaInputInvalid,
    MediaContractInvalid,
    MediaWaitTimedOut,
    SuggestedCheckMediaStatus,
    FormBooleanPrompt,
    FormRepeatablePrompt,
    TuiAppTitle,
    TuiHelpTitle,
    TuiAnswerTitle,
    TuiChooseOneTitle,
    TuiChooseManyTitle,
    TuiInvalidAnswer,
    TuiBooleanUnset,
    TuiBooleanYes,
    TuiBooleanNo,
    TuiFooterText,
    TuiFooterMultiline,
    TuiFooterBoolean,
    TuiFooterSingleChoice,
    TuiFooterMultipleChoice,
}

/// A catalog key plus untrusted values substituted only after selecting a locale.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LocalizedMessage {
    key: MessageKey,
    values: BTreeMap<String, String>,
}

impl LocalizedMessage {
    /// Construct a message with no placeholder values.
    #[must_use]
    pub fn new(key: MessageKey) -> Self {
        Self {
            key,
            values: BTreeMap::new(),
        }
    }

    /// Add one named placeholder value.
    #[must_use]
    pub fn with(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.values.insert(key.into(), value.into());
        self
    }

    /// Render using one complete compile-time catalog.
    #[must_use]
    pub fn render(&self, locale: Locale) -> String {
        let values = self
            .values
            .iter()
            .map(|(key, value)| (key.as_str(), value.as_str()))
            .collect::<Vec<_>>();
        format_message(locale.message(self.key), &values)
    }
}

/// A locale name not shipped by this binary.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UnsupportedLocale;

/// Substitute named placeholders in a trusted catalog string.
#[must_use]
pub fn format_message(template: &str, values: &[(&str, &str)]) -> String {
    values
        .iter()
        .fold(String::from(template), |rendered, (key, value)| {
            rendered.replace(&format!("{{{key}}}"), value)
        })
}

#[cfg(test)]
mod tests {
    use std::str::FromStr;

    use super::{Locale, LocalizedMessage, MessageKey, format_message};

    #[test]
    fn accepts_supported_locale_spellings() {
        assert_eq!(Locale::from_str("en_US.UTF-8"), Ok(Locale::En));
        assert_eq!(Locale::from_str("zh-CN"), Ok(Locale::ZhCn));
        assert_eq!(Locale::from_str("zh_Hans_CN.UTF-8"), Ok(Locale::ZhCn));
    }

    #[test]
    fn rejects_unshipped_locales() {
        assert!(Locale::from_str("is-IS").is_err());
    }

    #[test]
    fn formats_catalog_placeholders() {
        let rendered = format_message(
            Locale::En.message(MessageKey::VersionLine),
            &[
                ("version", "1.2.3"),
                ("target", "test-target"),
                ("minimum", "1"),
                ("maximum", "2"),
            ],
        );
        assert_eq!(
            rendered,
            "squid 1.2.3 (test-target; submission protocol 1..=2)"
        );
    }

    #[test]
    fn renders_owned_placeholder_values() {
        let rendered = LocalizedMessage::new(MessageKey::VersionLine)
            .with("version", "1.2.3")
            .with("target", "test-target")
            .with("minimum", "1")
            .with("maximum", "2")
            .render(Locale::ZhCn);
        assert_eq!(rendered, "squid 1.2.3（test-target；投稿协议 1..=2）");
    }
}
