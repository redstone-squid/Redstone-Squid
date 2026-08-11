//! Full-screen terminal renderer for one visible, validated form field.

use std::collections::BTreeSet;
use std::io;

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode,
};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Text};
use ratatui::widgets::{Block, Borders, List, ListItem, Paragraph, Wrap};

use crate::form::{
    ChoiceOption, FormAnswer, FormControl, FormError, FormField, parse_boolean_answer,
    parse_integer_answer, validate_text_answer,
};
use crate::locale::{Locale, MessageKey};
use crate::terminal::sanitize_terminal_text;

const MAXIMUM_TUI_INPUT_CHARACTERS: usize = 1024 * 1024;

/// Render one form field in an alternate screen and restore the terminal on every return path.
pub fn read_answer_tui(field: &FormField, locale: Locale) -> Result<Option<FormAnswer>, FormError> {
    field.validate()?;
    enable_raw_mode().map_err(FormError::Io)?;
    let mut stderr = io::stderr();
    if let Err(error) = execute!(stderr, EnterAlternateScreen) {
        let _restored = disable_raw_mode();
        return Err(FormError::Io(error));
    }
    let _guard = TerminalRestoreGuard;
    let backend = CrosstermBackend::new(stderr);
    let mut terminal = Terminal::new(backend).map_err(FormError::Io)?;
    terminal.clear().map_err(FormError::Io)?;
    let mut state = TuiFieldState::new(field);

    loop {
        terminal
            .draw(|frame| draw_field(frame, field, &state, locale))
            .map_err(FormError::Io)?;
        match event::read().map_err(FormError::Io)? {
            Event::Key(key) if matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) => {
                match state.handle_key(field, key)? {
                    TuiAction::Continue => {}
                    TuiAction::Submit(answer) => return Ok(answer),
                    TuiAction::Cancel => return Err(FormError::Cancelled),
                }
            }
            Event::Paste(value) => state.insert_text(field, &value),
            Event::Resize(_, _) | Event::FocusGained | Event::FocusLost | Event::Mouse(_) => {}
            Event::Key(_) => {}
        }
    }
}

struct TerminalRestoreGuard;

impl Drop for TerminalRestoreGuard {
    fn drop(&mut self) {
        let _left_screen = execute!(io::stderr(), LeaveAlternateScreen);
        let _raw_mode = disable_raw_mode();
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum TuiAction {
    Continue,
    Submit(Option<FormAnswer>),
    Cancel,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct TuiFieldState {
    input: String,
    option_cursor: usize,
    selected: BTreeSet<usize>,
    choice_touched: bool,
    boolean: Option<bool>,
    validation_error: bool,
}

impl TuiFieldState {
    fn new(_field: &FormField) -> Self {
        Self::default()
    }

    fn handle_key(&mut self, field: &FormField, key: KeyEvent) -> Result<TuiAction, FormError> {
        if key.code == KeyCode::Esc
            || (key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL))
        {
            return Ok(TuiAction::Cancel);
        }
        self.validation_error = false;
        match &field.control {
            FormControl::Text { .. }
            | FormControl::MultilineText { .. }
            | FormControl::Integer { .. } => self.handle_text_key(field, key),
            FormControl::Boolean => self.handle_boolean_key(field, key),
            FormControl::SingleChoice { options } => {
                self.handle_single_choice_key(field, options, key)
            }
            FormControl::MultipleChoice {
                options,
                minimum_selections,
                maximum_selections,
            } => self.handle_multiple_choice_key(
                field,
                options,
                *minimum_selections,
                *maximum_selections,
                key,
            ),
        }
    }

    fn handle_text_key(
        &mut self,
        field: &FormField,
        key: KeyEvent,
    ) -> Result<TuiAction, FormError> {
        match key.code {
            KeyCode::Enter
                if matches!(field.control, FormControl::MultilineText { .. })
                    && key
                        .modifiers
                        .intersects(KeyModifiers::SHIFT | KeyModifiers::ALT) =>
            {
                self.insert_text(field, "\n");
                Ok(TuiAction::Continue)
            }
            KeyCode::Enter => self.submit_text(field),
            KeyCode::Backspace => {
                self.input.pop();
                Ok(TuiAction::Continue)
            }
            KeyCode::Char(character)
                if !key
                    .modifiers
                    .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                self.insert_text(field, &character.to_string());
                Ok(TuiAction::Continue)
            }
            _ => Ok(TuiAction::Continue),
        }
    }

    fn submit_text(&mut self, field: &FormField) -> Result<TuiAction, FormError> {
        let answer = match &field.control {
            FormControl::Text {
                minimum_characters,
                maximum_characters,
            }
            | FormControl::MultilineText {
                minimum_characters,
                maximum_characters,
            } => validate_text_answer(
                &self.input,
                field.required,
                *minimum_characters,
                *maximum_characters,
            ),
            FormControl::Integer { minimum, maximum } => {
                parse_integer_answer(&self.input, field.required, *minimum, *maximum)
            }
            _ => return Err(FormError::UnsupportedControl),
        };
        self.finish_or_mark_invalid(answer)
    }

    fn insert_text(&mut self, field: &FormField, value: &str) {
        let maximum = match field.control {
            FormControl::Text {
                maximum_characters, ..
            }
            | FormControl::MultilineText {
                maximum_characters, ..
            } => maximum_characters.unwrap_or(MAXIMUM_TUI_INPUT_CHARACTERS),
            FormControl::Integer { .. } => 32,
            _ => return,
        }
        .min(MAXIMUM_TUI_INPUT_CHARACTERS);
        let remaining = maximum.saturating_sub(self.input.chars().count());
        let sanitized = sanitize_terminal_text(value)
            .chars()
            .filter(|character| {
                !character.is_control()
                    || (matches!(field.control, FormControl::MultilineText { .. })
                        && *character == '\n')
            })
            .take(remaining)
            .collect::<String>();
        self.input.push_str(&sanitized);
    }

    fn handle_boolean_key(
        &mut self,
        field: &FormField,
        key: KeyEvent,
    ) -> Result<TuiAction, FormError> {
        match key.code {
            KeyCode::Char('y' | 'Y') => self.boolean = Some(true),
            KeyCode::Char('n' | 'N') => self.boolean = Some(false),
            KeyCode::Left | KeyCode::Right | KeyCode::Up | KeyCode::Down | KeyCode::Char(' ') => {
                self.boolean = Some(!self.boolean.unwrap_or(false));
            }
            KeyCode::Enter => {
                let value = self
                    .boolean
                    .map_or("", |value| if value { "yes" } else { "no" });
                return self.finish_or_mark_invalid(parse_boolean_answer(value, field.required));
            }
            _ => {}
        }
        Ok(TuiAction::Continue)
    }

    fn handle_single_choice_key(
        &mut self,
        field: &FormField,
        options: &[ChoiceOption],
        key: KeyEvent,
    ) -> Result<TuiAction, FormError> {
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => {
                self.option_cursor = self.option_cursor.saturating_sub(1);
                self.choice_touched = true;
            }
            KeyCode::Down | KeyCode::Char('j') => {
                self.option_cursor = self
                    .option_cursor
                    .saturating_add(1)
                    .min(options.len().saturating_sub(1));
                self.choice_touched = true;
            }
            KeyCode::Enter => {
                if !field.required && !self.choice_touched {
                    return Ok(TuiAction::Submit(None));
                }
                let answer = options
                    .get(self.option_cursor)
                    .map(|option| FormAnswer::Choice(option.code.clone()))
                    .ok_or(FormError::InvalidOptions)?;
                return Ok(TuiAction::Submit(Some(answer)));
            }
            _ => {}
        }
        Ok(TuiAction::Continue)
    }

    fn handle_multiple_choice_key(
        &mut self,
        field: &FormField,
        options: &[ChoiceOption],
        minimum: Option<usize>,
        maximum: Option<usize>,
        key: KeyEvent,
    ) -> Result<TuiAction, FormError> {
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => {
                self.option_cursor = self.option_cursor.saturating_sub(1);
            }
            KeyCode::Down | KeyCode::Char('j') => {
                self.option_cursor = self
                    .option_cursor
                    .saturating_add(1)
                    .min(options.len().saturating_sub(1));
            }
            KeyCode::Char(' ') => {
                if !self.selected.remove(&self.option_cursor) {
                    if maximum.is_none_or(|maximum| self.selected.len() < maximum) {
                        self.selected.insert(self.option_cursor);
                    } else {
                        self.validation_error = true;
                    }
                }
            }
            KeyCode::Enter => {
                let count = self.selected.len();
                if (field.required && count == 0)
                    || minimum.is_some_and(|minimum| count < minimum)
                    || maximum.is_some_and(|maximum| count > maximum)
                {
                    self.validation_error = true;
                    return Ok(TuiAction::Continue);
                }
                if count == 0 {
                    return Ok(TuiAction::Submit(None));
                }
                let answers = self
                    .selected
                    .iter()
                    .filter_map(|index| options.get(*index))
                    .map(|option| option.code.clone())
                    .collect();
                return Ok(TuiAction::Submit(Some(FormAnswer::Choices(answers))));
            }
            _ => {}
        }
        Ok(TuiAction::Continue)
    }

    fn finish_or_mark_invalid(
        &mut self,
        result: Result<Option<FormAnswer>, FormError>,
    ) -> Result<TuiAction, FormError> {
        match result {
            Ok(answer) => Ok(TuiAction::Submit(answer)),
            Err(FormError::InvalidAnswer) => {
                self.validation_error = true;
                Ok(TuiAction::Continue)
            }
            Err(error) => Err(error),
        }
    }
}

fn draw_field(
    frame: &mut ratatui::Frame<'_>,
    field: &FormField,
    state: &TuiFieldState,
    locale: Locale,
) {
    let area = frame.area();
    let sections = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(3),
        ])
        .split(area);
    let required = if field.required { " *" } else { "" };
    let title = format!("{}{}", single_line(&field.label), required);
    frame.render_widget(
        Paragraph::new(title)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(locale.message(MessageKey::TuiAppTitle)),
            )
            .style(Style::default().add_modifier(Modifier::BOLD)),
        sections[0],
    );
    let description = field
        .description
        .as_deref()
        .map(sanitize_terminal_text)
        .unwrap_or_default();
    frame.render_widget(
        Paragraph::new(description)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(locale.message(MessageKey::TuiHelpTitle)),
            )
            .wrap(Wrap { trim: false }),
        sections[1],
    );
    draw_control(frame, sections[2], field, state, locale);
    let status = if state.validation_error {
        Line::styled(
            locale.message(MessageKey::TuiInvalidAnswer),
            Style::default().fg(Color::Red),
        )
    } else {
        Line::raw(footer(field, locale))
    };
    frame.render_widget(
        Paragraph::new(status).block(Block::default().borders(Borders::ALL)),
        sections[3],
    );
}

fn draw_control(
    frame: &mut ratatui::Frame<'_>,
    area: ratatui::layout::Rect,
    field: &FormField,
    state: &TuiFieldState,
    locale: Locale,
) {
    match &field.control {
        FormControl::Text { .. }
        | FormControl::MultilineText { .. }
        | FormControl::Integer { .. } => {
            frame.render_widget(
                Paragraph::new(Text::from(sanitize_terminal_text(&state.input)))
                    .block(
                        Block::default()
                            .borders(Borders::ALL)
                            .title(locale.message(MessageKey::TuiAnswerTitle)),
                    )
                    .wrap(Wrap { trim: false }),
                area,
            );
        }
        FormControl::Boolean => {
            let value = state.boolean.map_or_else(
                || locale.message(MessageKey::TuiBooleanUnset),
                |value| {
                    if value {
                        locale.message(MessageKey::TuiBooleanYes)
                    } else {
                        locale.message(MessageKey::TuiBooleanNo)
                    }
                },
            );
            frame.render_widget(
                Paragraph::new(value).block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title(locale.message(MessageKey::TuiAnswerTitle)),
                ),
                area,
            );
        }
        FormControl::SingleChoice { options } => {
            let items = options.iter().enumerate().map(|(index, option)| {
                let marker =
                    if index == state.option_cursor && (field.required || state.choice_touched) {
                        ">"
                    } else {
                        " "
                    };
                ListItem::new(format!("{marker} {}", single_line(&option.label)))
            });
            frame.render_widget(
                List::new(items).block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title(locale.message(MessageKey::TuiChooseOneTitle)),
                ),
                area,
            );
        }
        FormControl::MultipleChoice { options, .. } => {
            let items = options.iter().enumerate().map(|(index, option)| {
                let cursor = if index == state.option_cursor {
                    ">"
                } else {
                    " "
                };
                let selected = if state.selected.contains(&index) {
                    "x"
                } else {
                    " "
                };
                ListItem::new(format!(
                    "{cursor} [{selected}] {}",
                    single_line(&option.label)
                ))
            });
            frame.render_widget(
                List::new(items).block(
                    Block::default()
                        .borders(Borders::ALL)
                        .title(locale.message(MessageKey::TuiChooseManyTitle)),
                ),
                area,
            );
        }
    }
}

fn footer(field: &FormField, locale: Locale) -> &'static str {
    match field.control {
        FormControl::Text { .. } | FormControl::Integer { .. } => {
            locale.message(MessageKey::TuiFooterText)
        }
        FormControl::MultilineText { .. } => locale.message(MessageKey::TuiFooterMultiline),
        FormControl::Boolean => locale.message(MessageKey::TuiFooterBoolean),
        FormControl::SingleChoice { .. } => locale.message(MessageKey::TuiFooterSingleChoice),
        FormControl::MultipleChoice { .. } => locale.message(MessageKey::TuiFooterMultipleChoice),
    }
}

fn single_line(value: &str) -> String {
    sanitize_terminal_text(value)
        .chars()
        .map(|character| match character {
            '\n' | '\r' | '\t' => ' ',
            other => other,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    use ratatui::Terminal;
    use ratatui::backend::TestBackend;

    use super::{TuiAction, TuiFieldState, draw_field};
    use crate::form::{ChoiceOption, FormAnswer, FormCode, FormControl, FormError, FormField};
    use crate::locale::Locale;

    fn field(control: FormControl, required: bool) -> Result<FormField, FormError> {
        Ok(FormField {
            code: FormCode::parse("door.width")?,
            label: String::from("Width\u{1b}[31m"),
            description: Some(String::from("Choose carefully")),
            required,
            control,
        })
    }

    fn option(code: &str, label: &str) -> Result<ChoiceOption, FormError> {
        Ok(ChoiceOption {
            code: FormCode::parse(code)?,
            label: String::from(label),
        })
    }

    #[test]
    fn text_state_validates_before_submit() -> Result<(), FormError> {
        let field = field(
            FormControl::Text {
                minimum_characters: Some(2),
                maximum_characters: Some(3),
            },
            true,
        )?;
        let mut state = TuiFieldState::new(&field);
        assert_eq!(
            state.handle_key(
                &field,
                KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE)
            )?,
            TuiAction::Continue
        );
        assert_eq!(
            state.handle_key(&field, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))?,
            TuiAction::Continue
        );
        assert!(state.validation_error);
        state.handle_key(
            &field,
            KeyEvent::new(KeyCode::Char('b'), KeyModifiers::NONE),
        )?;
        assert_eq!(
            state.handle_key(&field, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))?,
            TuiAction::Submit(Some(FormAnswer::Text(String::from("ab"))))
        );
        Ok(())
    }

    #[test]
    fn multiple_choice_state_enforces_maximum() -> Result<(), FormError> {
        let field = field(
            FormControl::MultipleChoice {
                options: vec![option("a", "A")?, option("b", "B")?],
                minimum_selections: Some(1),
                maximum_selections: Some(1),
            },
            true,
        )?;
        let mut state = TuiFieldState::new(&field);
        state.handle_key(
            &field,
            KeyEvent::new(KeyCode::Char(' '), KeyModifiers::NONE),
        )?;
        state.handle_key(&field, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE))?;
        state.handle_key(
            &field,
            KeyEvent::new(KeyCode::Char(' '), KeyModifiers::NONE),
        )?;
        assert!(state.validation_error);
        assert_eq!(state.selected, BTreeSet::from([0]));
        assert_eq!(
            state.handle_key(&field, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))?,
            TuiAction::Submit(Some(FormAnswer::Choices(vec![FormCode::parse("a")?])))
        );
        Ok(())
    }

    #[test]
    fn test_backend_contains_only_sanitized_server_text() -> Result<(), Box<dyn std::error::Error>>
    {
        let field = field(FormControl::Boolean, true)?;
        let state = TuiFieldState::new(&field);
        let backend = TestBackend::new(80, 16);
        let mut terminal = Terminal::new(backend)?;
        terminal.draw(|frame| draw_field(frame, &field, &state, Locale::ZhCn))?;
        let rendered = terminal.backend().buffer().content().to_vec();
        assert!(!rendered.iter().any(|cell| cell.symbol() == "\u{1b}"));
        assert!(rendered.iter().any(|cell| cell.symbol() == "帮"));
        assert!(rendered.iter().any(|cell| cell.symbol() == "助"));
        Ok(())
    }

    #[test]
    fn control_c_cancels_without_submitting() -> Result<(), FormError> {
        let field = field(FormControl::Boolean, true)?;
        let mut state = TuiFieldState::new(&field);
        assert_eq!(
            state.handle_key(
                &field,
                KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
            )?,
            TuiAction::Cancel
        );
        Ok(())
    }
}
