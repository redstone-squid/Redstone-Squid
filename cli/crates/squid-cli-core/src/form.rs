//! Backend-schema-neutral form validation, capability gating, and terminal prompts.

use std::collections::BTreeSet;
use std::io::{self, BufRead, Write};
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use thiserror::Error;

use crate::process::{EditorError, ExternalTextEditor};
use crate::terminal::sanitize_terminal_text;
use crate::{locale::Locale, locale::MessageKey};

const MAXIMUM_FIELD_CODE_BYTES: usize = 96;
const MAXIMUM_CAPABILITY_BYTES: usize = 128;
const MAXIMUM_LABEL_CHARACTERS: usize = 4_096;
const MAXIMUM_OPTIONS: usize = 500;
const MAXIMUM_PROMPT_BYTES: u64 = 1024 * 1024;

const CAPABILITY_PROMPT: &str = "cli.prompt.v1";
const CAPABILITY_TUI: &str = "cli.tui.v1";
const CAPABILITY_TEXT: &str = "cli.control.text.v1";
const CAPABILITY_MULTILINE: &str = "cli.control.multiline_text.v1";
const CAPABILITY_INTEGER: &str = "cli.control.integer.v1";
const CAPABILITY_BOOLEAN: &str = "cli.control.boolean.v1";
const CAPABILITY_SINGLE_CHOICE: &str = "cli.control.single_choice.v1";
const CAPABILITY_MULTIPLE_CHOICE: &str = "cli.control.multiple_choice.v1";
const CAPABILITY_REPEATABLE_TEXT: &str = "repeatable_text";

/// A validated stable field or option code.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(transparent)]
pub struct FormCode(String);

impl FormCode {
    /// Accept compact ASCII identifiers that remain safe in operations and terminal selectors.
    pub fn parse(value: &str) -> Result<Self, FormError> {
        let valid = (1..=MAXIMUM_FIELD_CODE_BYTES).contains(&value.len())
            && value.bytes().enumerate().all(|(index, byte)| {
                byte.is_ascii_lowercase()
                    || (index > 0
                        && (byte.is_ascii_digit() || byte == b'_' || byte == b'-' || byte == b'.'))
            });
        if !valid {
            return Err(FormError::InvalidCode);
        }
        Ok(Self(String::from(value)))
    }

    /// Validated identifier value.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// One selectable value with a stable machine code and localized server-authored label.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ChoiceOption {
    pub code: FormCode,
    pub label: String,
}

/// Renderer-independent control constraints.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FormControl {
    Text {
        minimum_characters: Option<usize>,
        maximum_characters: Option<usize>,
    },
    MultilineText {
        minimum_characters: Option<usize>,
        maximum_characters: Option<usize>,
    },
    RepeatableText {
        minimum_items: Option<usize>,
        maximum_items: Option<usize>,
        minimum_characters: Option<usize>,
        maximum_characters: Option<usize>,
    },
    Integer {
        minimum: Option<i64>,
        maximum: Option<i64>,
    },
    Boolean,
    SingleChoice {
        options: Vec<ChoiceOption>,
    },
    MultipleChoice {
        options: Vec<ChoiceOption>,
        minimum_selections: Option<usize>,
        maximum_selections: Option<usize>,
    },
}

impl FormControl {
    const fn capability(&self) -> &'static str {
        match self {
            Self::Text { .. } => CAPABILITY_TEXT,
            Self::MultilineText { .. } => CAPABILITY_MULTILINE,
            Self::RepeatableText { .. } => CAPABILITY_REPEATABLE_TEXT,
            Self::Integer { .. } => CAPABILITY_INTEGER,
            Self::Boolean => CAPABILITY_BOOLEAN,
            Self::SingleChoice { .. } => CAPABILITY_SINGLE_CHOICE,
            Self::MultipleChoice { .. } => CAPABILITY_MULTIPLE_CHOICE,
        }
    }
}

/// One localized field after an API adapter has applied manifest visibility rules.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FormField {
    pub code: FormCode,
    pub label: String,
    pub description: Option<String>,
    pub required: bool,
    pub control: FormControl,
}

impl FormField {
    /// Validate limits and option codes before rendering untrusted schema content.
    pub fn validate(&self) -> Result<(), FormError> {
        if self.label.chars().count() > MAXIMUM_LABEL_CHARACTERS
            || self
                .description
                .as_deref()
                .is_some_and(|value| value.chars().count() > MAXIMUM_LABEL_CHARACTERS)
        {
            return Err(FormError::LabelTooLarge);
        }
        match &self.control {
            FormControl::Text {
                minimum_characters,
                maximum_characters,
            }
            | FormControl::MultilineText {
                minimum_characters,
                maximum_characters,
            } => validate_range(*minimum_characters, *maximum_characters),
            FormControl::RepeatableText {
                minimum_items,
                maximum_items,
                minimum_characters,
                maximum_characters,
            } => {
                validate_range(*minimum_items, *maximum_items)?;
                validate_range(*minimum_characters, *maximum_characters)
            }
            FormControl::Integer { minimum, maximum } => {
                if minimum.zip(*maximum).is_some_and(|(min, max)| min > max) {
                    return Err(FormError::InvalidConstraint);
                }
                Ok(())
            }
            FormControl::Boolean => Ok(()),
            FormControl::SingleChoice { options } => validate_options(options),
            FormControl::MultipleChoice {
                options,
                minimum_selections,
                maximum_selections,
            } => {
                validate_options(options)?;
                validate_range(*minimum_selections, *maximum_selections)?;
                if maximum_selections.is_some_and(|maximum| maximum > options.len()) {
                    return Err(FormError::InvalidConstraint);
                }
                Ok(())
            }
        }
    }
}

fn validate_options(options: &[ChoiceOption]) -> Result<(), FormError> {
    if options.is_empty() || options.len() > MAXIMUM_OPTIONS {
        return Err(FormError::InvalidOptions);
    }
    let mut codes = BTreeSet::new();
    for option in options {
        if option.label.chars().count() > MAXIMUM_LABEL_CHARACTERS
            || !codes.insert(option.code.as_str())
        {
            return Err(FormError::InvalidOptions);
        }
    }
    Ok(())
}

fn validate_range<T: Ord>(minimum: Option<T>, maximum: Option<T>) -> Result<(), FormError> {
    if minimum.zip(maximum).is_some_and(|(min, max)| min > max) {
        return Err(FormError::InvalidConstraint);
    }
    Ok(())
}

/// A renderer answer ready to become one stable-field set operation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FormAnswer {
    Text(String),
    Texts(Vec<String>),
    Integer(i64),
    Boolean(bool),
    Choice(FormCode),
    Choices(Vec<FormCode>),
}

impl FormAnswer {
    /// Convert the validated answer to the primitive JSON value used by an API adapter.
    #[must_use]
    pub fn to_json(&self) -> Value {
        match self {
            Self::Text(value) => Value::String(value.clone()),
            Self::Texts(values) => {
                Value::Array(values.iter().cloned().map(Value::String).collect())
            }
            Self::Integer(value) => Value::Number((*value).into()),
            Self::Boolean(value) => Value::Bool(*value),
            Self::Choice(value) => Value::String(String::from(value.as_str())),
            Self::Choices(values) => Value::Array(
                values
                    .iter()
                    .map(|value| Value::String(String::from(value.as_str())))
                    .collect(),
            ),
        }
    }
}

/// One server-declared renderer capability and whether submission depends on it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapabilityRequirement {
    pub code: String,
    pub required: bool,
}

/// Capabilities implemented by one concrete invocation's renderer.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct RendererCapabilities(BTreeSet<&'static str>);

impl RendererCapabilities {
    /// Capabilities of the line-prompt renderer, optionally including external multiline editing.
    #[must_use]
    pub fn prompt(external_editor: bool) -> Self {
        let mut values = BTreeSet::from([
            CAPABILITY_PROMPT,
            CAPABILITY_TEXT,
            CAPABILITY_INTEGER,
            CAPABILITY_BOOLEAN,
            CAPABILITY_SINGLE_CHOICE,
            CAPABILITY_MULTIPLE_CHOICE,
            CAPABILITY_REPEATABLE_TEXT,
        ]);
        if external_editor {
            values.insert(CAPABILITY_MULTILINE);
        }
        Self(values)
    }

    /// Capabilities of the full-screen renderer, including inline multiline text editing.
    #[must_use]
    pub fn tui() -> Self {
        Self(BTreeSet::from([
            CAPABILITY_TUI,
            CAPABILITY_TEXT,
            CAPABILITY_MULTILINE,
            CAPABILITY_INTEGER,
            CAPABILITY_BOOLEAN,
            CAPABILITY_SINGLE_CHOICE,
            CAPABILITY_MULTIPLE_CHOICE,
            CAPABILITY_REPEATABLE_TEXT,
        ]))
    }

    /// Sorted, comma-separated HTTP header value.
    #[must_use]
    pub fn header_value(&self) -> String {
        self.0.iter().copied().collect::<Vec<_>>().join(",")
    }

    /// Stable server form capabilities accepted in draft-creation request bodies.
    #[must_use]
    pub fn submission_capabilities(&self) -> Vec<String> {
        [CAPABILITY_REPEATABLE_TEXT]
            .into_iter()
            .filter(|capability| self.0.contains(capability))
            .map(String::from)
            .collect()
    }

    /// Evaluate required controls separately from ignorable optional presentation hints.
    pub fn assess(
        &self,
        requirements: &[CapabilityRequirement],
    ) -> Result<CapabilityAssessment, FormError> {
        let mut missing_required = BTreeSet::new();
        let mut ignored_optional = BTreeSet::new();
        for requirement in requirements {
            validate_capability_code(&requirement.code)?;
            if !self.0.contains(requirement.code.as_str()) {
                if requirement.required {
                    missing_required.insert(requirement.code.clone());
                } else {
                    ignored_optional.insert(requirement.code.clone());
                }
            }
        }
        Ok(CapabilityAssessment {
            web_continuation_required: !missing_required.is_empty(),
            missing_required: missing_required.into_iter().collect(),
            ignored_optional: ignored_optional.into_iter().collect(),
        })
    }

    /// Whether this invocation can render one validated internal control.
    #[must_use]
    pub fn supports(&self, control: &FormControl) -> bool {
        self.0.contains(control.capability())
    }
}

fn validate_capability_code(value: &str) -> Result<(), FormError> {
    let valid = (1..=MAXIMUM_CAPABILITY_BYTES).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'));
    if !valid {
        return Err(FormError::InvalidCapability);
    }
    Ok(())
}

/// Result of matching a pinned manifest's requirements to one renderer.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapabilityAssessment {
    pub web_continuation_required: bool,
    pub missing_required: Vec<String>,
    pub ignored_optional: Vec<String>,
}

/// Whether reading from a terminal is permitted for this invocation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InteractionMode {
    Interactive,
    NonInteractive,
}

/// A one-attempt prompt renderer; callers own retry, cancellation, and draft-operation policy.
#[derive(Debug)]
pub struct PromptRenderer<'a> {
    mode: InteractionMode,
    locale: Locale,
    external_editor: Option<&'a ExternalTextEditor>,
    editor_timeout: Option<Duration>,
}

impl<'a> PromptRenderer<'a> {
    /// Construct a renderer that never silently prompts in a non-interactive invocation.
    #[must_use]
    pub const fn new(mode: InteractionMode) -> Self {
        Self {
            mode,
            locale: Locale::En,
            external_editor: None,
            editor_timeout: None,
        }
    }

    /// Localize renderer-authored prompt chrome independently of server field text.
    #[must_use]
    pub const fn with_locale(mut self, locale: Locale) -> Self {
        self.locale = locale;
        self
    }

    /// Enable external editing for multiline text fields.
    #[must_use]
    pub const fn with_external_editor(
        mut self,
        editor: &'a ExternalTextEditor,
        timeout: Option<Duration>,
    ) -> Self {
        self.external_editor = Some(editor);
        self.editor_timeout = timeout;
        self
    }

    /// Capabilities that are truthful for this exact renderer configuration.
    #[must_use]
    pub fn capabilities(&self) -> RendererCapabilities {
        RendererCapabilities::prompt(self.external_editor.is_some())
    }

    /// Render and parse one currently visible field.
    pub fn read_answer(
        &self,
        field: &FormField,
        input: &mut impl BufRead,
        output: &mut impl Write,
    ) -> Result<Option<FormAnswer>, FormError> {
        if self.mode == InteractionMode::NonInteractive {
            return Err(FormError::InteractionRequired);
        }
        field.validate()?;
        if !self.capabilities().supports(&field.control) {
            return Err(FormError::UnsupportedControl);
        }
        render_heading(field, output)?;
        match &field.control {
            FormControl::MultilineText {
                minimum_characters,
                maximum_characters,
            } => {
                let editor = self.external_editor.ok_or(FormError::UnsupportedControl)?;
                let value = editor.edit("", self.editor_timeout)?;
                validate_text_answer(
                    &value,
                    field.required,
                    *minimum_characters,
                    *maximum_characters,
                )
            }
            FormControl::RepeatableText {
                minimum_items,
                maximum_items,
                minimum_characters,
                maximum_characters,
            } => {
                writeln!(
                    output,
                    "{}",
                    self.locale.message(MessageKey::FormRepeatablePrompt)
                )
                .map_err(FormError::Io)?;
                read_repeatable_answers(
                    input,
                    output,
                    field.required,
                    *minimum_items,
                    *maximum_items,
                    *minimum_characters,
                    *maximum_characters,
                )
            }
            FormControl::Text {
                minimum_characters,
                maximum_characters,
            } => {
                let value = read_line(input, output)?;
                validate_text_answer(
                    &value,
                    field.required,
                    *minimum_characters,
                    *maximum_characters,
                )
            }
            FormControl::Integer { minimum, maximum } => {
                let value = read_line(input, output)?;
                parse_integer_answer(&value, field.required, *minimum, *maximum)
            }
            FormControl::Boolean => {
                write!(
                    output,
                    "{}",
                    self.locale.message(MessageKey::FormBooleanPrompt)
                )
                .map_err(FormError::Io)?;
                let value = read_line(input, output)?;
                parse_boolean_answer(&value, field.required)
            }
            FormControl::SingleChoice { options } => {
                render_options(options, output)?;
                let value = read_line(input, output)?;
                parse_single_choice(&value, field.required, options)
            }
            FormControl::MultipleChoice {
                options,
                minimum_selections,
                maximum_selections,
            } => {
                render_options(options, output)?;
                let value = read_line(input, output)?;
                parse_multiple_choices(
                    &value,
                    field.required,
                    options,
                    *minimum_selections,
                    *maximum_selections,
                )
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn read_repeatable_answers(
    input: &mut impl BufRead,
    output: &mut impl Write,
    required: bool,
    minimum_items: Option<usize>,
    maximum_items: Option<usize>,
    minimum_characters: Option<usize>,
    maximum_characters: Option<usize>,
) -> Result<Option<FormAnswer>, FormError> {
    let mut values = Vec::new();
    loop {
        let value = read_line(input, output)?;
        if value.is_empty() {
            break;
        }
        validate_text_answer(&value, true, minimum_characters, maximum_characters)?;
        values.push(value);
        if maximum_items.is_some_and(|maximum| values.len() >= maximum) {
            break;
        }
    }
    if values.is_empty() && !required && minimum_items.unwrap_or(0) == 0 {
        return Ok(None);
    }
    if values.is_empty()
        || minimum_items.is_some_and(|minimum| values.len() < minimum)
        || maximum_items.is_some_and(|maximum| values.len() > maximum)
    {
        return Err(FormError::InvalidAnswer);
    }
    Ok(Some(FormAnswer::Texts(values)))
}

fn render_heading(field: &FormField, output: &mut impl Write) -> Result<(), FormError> {
    let label = single_line_terminal_text(&field.label);
    let required = if field.required { " *" } else { "" };
    writeln!(output, "{label}{required}").map_err(FormError::Io)?;
    if let Some(description) = &field.description {
        for line in sanitize_terminal_text(description).lines() {
            writeln!(output, "  {line}").map_err(FormError::Io)?;
        }
    }
    Ok(())
}

fn render_options(options: &[ChoiceOption], output: &mut impl Write) -> Result<(), FormError> {
    for (index, option) in options.iter().enumerate() {
        writeln!(
            output,
            "  {}. {}",
            index.saturating_add(1),
            single_line_terminal_text(&option.label),
        )
        .map_err(FormError::Io)?;
    }
    Ok(())
}

fn single_line_terminal_text(value: &str) -> String {
    sanitize_terminal_text(value)
        .chars()
        .map(|character| match character {
            '\n' | '\r' | '\t' => ' ',
            other => other,
        })
        .collect()
}

fn read_line(input: &mut impl BufRead, output: &mut impl Write) -> Result<String, FormError> {
    write!(output, "> ").map_err(FormError::Io)?;
    output.flush().map_err(FormError::Io)?;
    let mut value = String::new();
    let mut bounded = io::Read::take(&mut *input, MAXIMUM_PROMPT_BYTES.saturating_add(1));
    let length = bounded.read_line(&mut value).map_err(FormError::Io)?;
    if length == 0 {
        return Err(FormError::EndOfInput);
    }
    if length as u64 > MAXIMUM_PROMPT_BYTES {
        return Err(FormError::InputTooLarge);
    }
    while matches!(value.chars().last(), Some('\n' | '\r')) {
        value.pop();
    }
    Ok(value)
}

pub(crate) fn validate_text_answer(
    value: &str,
    required: bool,
    minimum: Option<usize>,
    maximum: Option<usize>,
) -> Result<Option<FormAnswer>, FormError> {
    if value.is_empty() && !required {
        return Ok(None);
    }
    let characters = value.chars().count();
    if value.is_empty()
        || minimum.is_some_and(|minimum| characters < minimum)
        || maximum.is_some_and(|maximum| characters > maximum)
    {
        return Err(FormError::InvalidAnswer);
    }
    Ok(Some(FormAnswer::Text(String::from(value))))
}

pub(crate) fn parse_repeatable_answer(
    value: &str,
    required: bool,
    minimum_items: Option<usize>,
    maximum_items: Option<usize>,
    minimum_characters: Option<usize>,
    maximum_characters: Option<usize>,
) -> Result<Option<FormAnswer>, FormError> {
    if value.is_empty() && !required && minimum_items.unwrap_or(0) == 0 {
        return Ok(None);
    }
    let values = value.split('\n').map(String::from).collect::<Vec<_>>();
    if values.is_empty()
        || minimum_items.is_some_and(|minimum| values.len() < minimum)
        || maximum_items.is_some_and(|maximum| values.len() > maximum)
    {
        return Err(FormError::InvalidAnswer);
    }
    for item in &values {
        validate_text_answer(item, true, minimum_characters, maximum_characters)?;
    }
    Ok(Some(FormAnswer::Texts(values)))
}

pub(crate) fn parse_integer_answer(
    value: &str,
    required: bool,
    minimum: Option<i64>,
    maximum: Option<i64>,
) -> Result<Option<FormAnswer>, FormError> {
    if value.trim().is_empty() && !required {
        return Ok(None);
    }
    let parsed = value
        .trim()
        .parse::<i64>()
        .map_err(|_error| FormError::InvalidAnswer)?;
    if minimum.is_some_and(|minimum| parsed < minimum)
        || maximum.is_some_and(|maximum| parsed > maximum)
    {
        return Err(FormError::InvalidAnswer);
    }
    Ok(Some(FormAnswer::Integer(parsed)))
}

pub(crate) fn parse_boolean_answer(
    value: &str,
    required: bool,
) -> Result<Option<FormAnswer>, FormError> {
    match value.trim().to_lowercase().as_str() {
        "" if !required => Ok(None),
        "y" | "yes" | "true" | "1" | "是" => Ok(Some(FormAnswer::Boolean(true))),
        "n" | "no" | "false" | "0" | "否" => Ok(Some(FormAnswer::Boolean(false))),
        _ => Err(FormError::InvalidAnswer),
    }
}

fn parse_single_choice(
    value: &str,
    required: bool,
    options: &[ChoiceOption],
) -> Result<Option<FormAnswer>, FormError> {
    if value.trim().is_empty() && !required {
        return Ok(None);
    }
    let option = resolve_choice(value.trim(), options).ok_or(FormError::InvalidAnswer)?;
    Ok(Some(FormAnswer::Choice(option.code.clone())))
}

fn parse_multiple_choices(
    value: &str,
    required: bool,
    options: &[ChoiceOption],
    minimum: Option<usize>,
    maximum: Option<usize>,
) -> Result<Option<FormAnswer>, FormError> {
    if value.trim().is_empty() && !required {
        return Ok(None);
    }
    let mut selected = BTreeSet::new();
    for token in value.split(',').map(str::trim) {
        let option = resolve_choice(token, options).ok_or(FormError::InvalidAnswer)?;
        selected.insert(option.code.clone());
    }
    let count = selected.len();
    if count == 0
        || minimum.is_some_and(|minimum| count < minimum)
        || maximum.is_some_and(|maximum| count > maximum)
    {
        return Err(FormError::InvalidAnswer);
    }
    Ok(Some(FormAnswer::Choices(selected.into_iter().collect())))
}

fn resolve_choice<'a>(value: &str, options: &'a [ChoiceOption]) -> Option<&'a ChoiceOption> {
    if let Ok(index) = value.parse::<usize>() {
        return index.checked_sub(1).and_then(|index| options.get(index));
    }
    options.iter().find(|option| option.code.as_str() == value)
}

/// Fail-closed errors from internal form adaptation and terminal input.
#[derive(Debug, Error)]
pub enum FormError {
    #[error("the form code is invalid")]
    InvalidCode,
    #[error("the renderer capability code is invalid")]
    InvalidCapability,
    #[error("a localized form label exceeds the safety limit")]
    LabelTooLarge,
    #[error("form constraints are inconsistent")]
    InvalidConstraint,
    #[error("form options are empty, duplicated, or exceed their safety limit")]
    InvalidOptions,
    #[error("the selected renderer cannot represent this required control")]
    UnsupportedControl,
    #[error("interactive input is required")]
    InteractionRequired,
    #[error("terminal input ended before an answer was read")]
    EndOfInput,
    #[error("interactive form editing was cancelled")]
    Cancelled,
    #[error("terminal input exceeds the safety limit")]
    InputTooLarge,
    #[error("the answer does not satisfy the field constraints")]
    InvalidAnswer,
    #[error("terminal input or output failed: {0}")]
    Io(io::Error),
    #[error(transparent)]
    Editor(#[from] EditorError),
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::{
        CapabilityRequirement, ChoiceOption, FormAnswer, FormCode, FormControl, FormError,
        FormField, InteractionMode, PromptRenderer, RendererCapabilities,
    };

    fn option(code: &str, label: &str) -> Result<ChoiceOption, FormError> {
        Ok(ChoiceOption {
            code: FormCode::parse(code)?,
            label: String::from(label),
        })
    }

    fn field(control: FormControl) -> Result<FormField, FormError> {
        Ok(FormField {
            code: FormCode::parse("door.width")?,
            label: String::from("Width\u{1b}[31m"),
            description: Some(String::from("Game ticks")),
            required: true,
            control,
        })
    }

    #[test]
    fn capability_gate_requires_web_only_for_unknown_required_controls() {
        let assessment = RendererCapabilities::prompt(false).assess(&[
            CapabilityRequirement {
                code: String::from("cli.presentation.sparkles.v1"),
                required: false,
            },
            CapabilityRequirement {
                code: String::from("cli.control.map_region.v1"),
                required: true,
            },
        ]);
        assert!(
            assessment.is_ok(),
            "capabilities should be valid: {assessment:?}"
        );
        let assessment = match assessment {
            Ok(value) => value,
            Err(_) => return,
        };
        assert!(assessment.web_continuation_required);
        assert_eq!(
            assessment.missing_required,
            [String::from("cli.control.map_region.v1")]
        );
        assert_eq!(
            assessment.ignored_optional,
            [String::from("cli.presentation.sparkles.v1")]
        );
        assert_eq!(
            RendererCapabilities::tui().submission_capabilities(),
            [String::from("repeatable_text")],
        );
        assert_eq!(
            RendererCapabilities::prompt(false).submission_capabilities(),
            [String::from("repeatable_text")],
        );
    }

    #[test]
    fn prompt_renderer_parses_and_bounds_integer() -> Result<(), FormError> {
        let field = field(FormControl::Integer {
            minimum: Some(1),
            maximum: Some(64),
        })?;
        let mut input = Cursor::new(b"12\n");
        let mut output = Vec::new();
        let answer = PromptRenderer::new(InteractionMode::Interactive).read_answer(
            &field,
            &mut input,
            &mut output,
        )?;
        assert_eq!(answer, Some(FormAnswer::Integer(12)));
        let rendered = String::from_utf8(output).map_err(|_error| FormError::InvalidAnswer)?;
        assert!(!rendered.contains('\u{1b}'));
        assert!(rendered.contains("Width[31m *"));
        Ok(())
    }

    #[test]
    fn prompt_renderer_accepts_codes_and_numbers_for_multiple_choice() -> Result<(), FormError> {
        let field = field(FormControl::MultipleChoice {
            options: vec![option("slime", "Slime")?, option("honey", "Honey")?],
            minimum_selections: Some(2),
            maximum_selections: Some(2),
        })?;
        let mut input = Cursor::new(b"2,slime\n");
        let mut output = Vec::new();
        let answer = PromptRenderer::new(InteractionMode::Interactive).read_answer(
            &field,
            &mut input,
            &mut output,
        )?;
        assert_eq!(
            answer,
            Some(FormAnswer::Choices(vec![
                FormCode::parse("honey")?,
                FormCode::parse("slime")?,
            ]))
        );
        Ok(())
    }

    #[test]
    fn prompt_renderer_collects_repeatable_text_one_line_at_a_time() -> Result<(), FormError> {
        let field = field(FormControl::RepeatableText {
            minimum_items: Some(1),
            maximum_items: Some(3),
            minimum_characters: Some(1),
            maximum_characters: Some(80),
        })?;
        let mut input = Cursor::new(b"Alice\nBob\n\n");
        let mut output = Vec::new();
        let answer = PromptRenderer::new(InteractionMode::Interactive).read_answer(
            &field,
            &mut input,
            &mut output,
        )?;
        assert_eq!(
            answer,
            Some(FormAnswer::Texts(vec![
                String::from("Alice"),
                String::from("Bob"),
            ]))
        );
        Ok(())
    }

    #[test]
    fn noninteractive_mode_never_reads_or_writes() -> Result<(), FormError> {
        let field = field(FormControl::Boolean)?;
        let mut input = Cursor::new(b"yes\n");
        let mut output = Vec::new();
        let result = PromptRenderer::new(InteractionMode::NonInteractive).read_answer(
            &field,
            &mut input,
            &mut output,
        );
        assert!(matches!(result, Err(FormError::InteractionRequired)));
        assert_eq!(input.position(), 0);
        assert!(output.is_empty());
        Ok(())
    }

    #[test]
    fn rejects_duplicate_options_and_invalid_constraints() -> Result<(), FormError> {
        let duplicate = field(FormControl::SingleChoice {
            options: vec![option("same", "One")?, option("same", "Two")?],
        })?;
        assert!(matches!(
            duplicate.validate(),
            Err(FormError::InvalidOptions)
        ));
        let reversed = field(FormControl::Text {
            minimum_characters: Some(10),
            maximum_characters: Some(2),
        })?;
        assert!(matches!(
            reversed.validate(),
            Err(FormError::InvalidConstraint)
        ));
        Ok(())
    }
}
