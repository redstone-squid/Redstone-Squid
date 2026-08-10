//! Defensive rendering for untrusted server-authored terminal text.

/// Remove terminal controls and bidirectional overrides while retaining line layout.
#[must_use]
pub fn sanitize_terminal_text(value: &str) -> String {
    value
        .chars()
        .filter(|character| {
            matches!(character, '\n' | '\t')
                || (!character.is_control() && !is_bidirectional_control(*character))
        })
        .collect()
}

fn is_bidirectional_control(character: char) -> bool {
    matches!(
        character,
        '\u{061c}'
            | '\u{200e}'
            | '\u{200f}'
            | '\u{202a}'..='\u{202e}'
            | '\u{2066}'..='\u{2069}'
    )
}

#[cfg(test)]
mod tests {
    use super::sanitize_terminal_text;

    #[test]
    fn removes_escape_and_bidi_controls() {
        assert_eq!(
            sanitize_terminal_text("safe\u{1b}[31m red\u{202e}txt\u{202c}"),
            "safe[31m redtxt",
        );
    }

    #[test]
    fn preserves_unicode_lines_and_tabs() {
        assert_eq!(sanitize_terminal_text("红石\n\tSquid"), "红石\n\tSquid");
    }
}
