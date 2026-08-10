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
    ProfileSymlinkRejected,
    OriginInvalidUrl,
    OriginSchemeInvalid,
    OriginCredentialsRejected,
    OriginComponentsRejected,
    OriginHostMissing,
    OriginPortMissing,
    OriginHttpsRequired,
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
