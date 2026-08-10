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
