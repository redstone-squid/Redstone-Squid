import { useEffect, useId, useRef, useState } from "react";

import { localizePath, translate, type Locale } from "../lib/i18n";

const DEBOUNCE_MS = 250;
const MIN_QUERY_LENGTH = 2;

export type SuggestionItem = {
  value: string;
  label: string;
  description: string | null;
  kind: string;
};

type Replacement = { start: number; end: number };

type Props = {
  locale: Locale;
  /** Registered suggestion source id, e.g. `approved_restrictions`. */
  source: string;
  id: string;
  name: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Render a textarea instead of an input, for the multi-line advanced query. */
  multiline?: boolean;
  maxLength?: number;
  inputMode?: React.HTMLAttributes<HTMLInputElement>["inputMode"];
  /**
   * Send the caret offset so the server can complete the token under it rather than the whole
   * field, and splice the chosen value back into the same span.
   */
  cursorAware?: boolean;
};

/**
 * A combobox backed by the localized, same-origin suggestion proxy.
 *
 * Requests are debounced and aborted when their inputs change. Whole-value sources need
 * two trimmed characters; cursor-aware sources can complete an empty input. Failures show
 * a status message while leaving the field editable.
 */
export default function SuggestField({
  locale,
  source,
  id,
  name,
  label,
  value,
  onChange,
  multiline = false,
  maxLength,
  inputMode,
  cursorAware = false,
}: Props) {
  const [items, setItems] = useState<SuggestionItem[]>([]);
  const [replacement, setReplacement] = useState<Replacement | null>(null);
  const [active, setActive] = useState(-1);
  const [failed, setFailed] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  const query = value.trim();
  // A cursor-aware source completes a token, so it has something to say from the first keystroke;
  // a whole-value source does not until there is enough typed to narrow anything.
  const enabled = cursorAware || query.length >= MIN_QUERY_LENGTH;
  const visible = enabled ? items : [];

  useEffect(() => {
    // Below the threshold, retained items are hidden. Re-enabling starts a new request;
    // the previous items can appear until that request replaces them.
    if (!enabled) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const url = new URL(
        localizePath(`/api/suggest/${encodeURIComponent(source)}`, locale),
        window.location.origin,
      );
      url.searchParams.set("q", value);
      if (cursorAware && cursor !== null) url.searchParams.set("cursor", String(cursor));
      fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
        .then(async (response) => {
          if (!response.ok) throw new Error(`Suggestion endpoint returned ${response.status}.`);
          return (await response.json()) as {
            items: SuggestionItem[];
            replacement: Replacement | null;
          };
        })
        .then((result) => {
          // A response without an items array behaves like an empty suggestion list.
          setItems(Array.isArray(result.items) ? result.items : []);
          setReplacement(result.replacement ?? null);
          setFailed(false);
          setActive(-1);
        })
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setItems([]);
          setFailed(true);
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [locale, source, value, cursor, cursorAware, enabled]);

  const choose = (item: SuggestionItem) => {
    // Splice when the server told us which span it completed; otherwise the value is the answer.
    onChange(
      replacement === null
        ? item.value
        : value.slice(0, replacement.start) + item.value + value.slice(replacement.end),
    );
    setItems([]);
    setActive(-1);
    inputRef.current?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (visible.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((current) => (current + 1) % visible.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => (current <= 0 ? visible.length - 1 : current - 1));
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      const selected = visible[active];
      if (selected) choose(selected);
    } else if (event.key === "Escape") {
      setItems([]);
      setActive(-1);
    }
  };

  const trackCursor = (element: HTMLInputElement | HTMLTextAreaElement) => {
    if (cursorAware) setCursor(element.selectionStart);
  };

  const shared = {
    id,
    name,
    value,
    onKeyDown,
    role: "combobox" as const,
    "aria-autocomplete": "list" as const,
    "aria-controls": visible.length > 0 ? listId : undefined,
    "aria-expanded": visible.length > 0,
    "aria-activedescendant": active >= 0 ? `${listId}-${active}` : undefined,
    autoComplete: "off",
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      trackCursor(event.currentTarget);
      onChange(event.currentTarget.value);
    },
    onSelect: (event: React.SyntheticEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      trackCursor(event.currentTarget),
  };

  return (
    <div className={multiline ? "field field--wide" : "field"}>
      <label htmlFor={id}>{label}</label>
      {multiline ? (
        <textarea
          {...shared}
          ref={inputRef as React.RefObject<HTMLTextAreaElement>}
          maxLength={maxLength}
        />
      ) : (
        <input
          {...shared}
          ref={inputRef as React.RefObject<HTMLInputElement>}
          inputMode={inputMode}
          maxLength={maxLength}
        />
      )}
      {visible.length > 0 && (
        <ul
          className="suggestions"
          id={listId}
          role="listbox"
          aria-label={translate(locale, "search.suggestions")}
        >
          {visible.map((item, index) => (
            <li key={`${item.kind}:${item.value}`} role="presentation">
              <button
                id={`${listId}-${index}`}
                type="button"
                role="option"
                aria-selected={active === index}
                onClick={() => choose(item)}
              >
                {item.label}
                {item.description && <span className="suggestion-hint"> {item.description}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {enabled && failed && (
        <span role="status">{translate(locale, "search.suggestionError")}</span>
      )}
    </div>
  );
}
