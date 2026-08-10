import { useEffect, useId, useMemo, useRef, useState } from "react";

import { composeGuidedQuery, type GuidedSearch } from "../lib/search";
import { localizePath, translate, translateTaxonomy, type Locale } from "../lib/i18n";

type Mode = "guided" | "advanced";

type Props = {
  locale: Locale;
  action: string;
  initialGuided?: GuidedSearch;
  initialQuery?: string;
  initialSort?: string;
  initialMode?: Mode;
};

const EMPTY_GUIDED: GuidedSearch = {};

export default function SearchComposer({
  locale,
  action,
  initialGuided = EMPTY_GUIDED,
  initialQuery = "",
  initialSort = "",
  initialMode = "guided",
}: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [guided, setGuided] = useState<GuidedSearch>(initialGuided);
  const [advanced, setAdvanced] = useState(initialQuery);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionError, setSuggestionError] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const suggestionId = useId();
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);
  const suggestValue = mode === "guided" ? (guided.keywords ?? "") : advanced;
  const visibleSuggestions = suggestValue.trim().length >= 2 ? suggestions : [];
  const query = useMemo(() => composeGuidedQuery(guided), [guided]);
  const setField = (key: keyof GuidedSearch, value: string) =>
    setGuided((current) => ({ ...current, [key]: value }));

  useEffect(() => {
    if (suggestValue.trim().length < 2) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const url = new URL(localizePath("/api/suggest", locale), window.location.origin);
      url.searchParams.set("q", suggestValue.trim());
      fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } })
        .then(async (response) => {
          if (!response.ok) throw new Error(`Suggestion endpoint returned ${response.status}.`);
          return (await response.json()) as { suggestions: string[] };
        })
        .then((result) => {
          setSuggestions(result.suggestions);
          setSuggestionError(false);
          setActiveSuggestion(-1);
        })
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setSuggestions([]);
          setSuggestionError(true);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [locale, suggestValue]);

  const chooseSuggestion = (suggestion: string) => {
    if (mode === "guided") setField("keywords", suggestion);
    else setAdvanced(suggestion);
    setSuggestions([]);
    setActiveSuggestion(-1);
    inputRef.current?.focus();
  };

  const onSuggestionKeyDown = (event: React.KeyboardEvent) => {
    if (visibleSuggestions.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveSuggestion((current) => (current + 1) % visibleSuggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveSuggestion((current) =>
        current <= 0 ? visibleSuggestions.length - 1 : current - 1,
      );
    } else if (event.key === "Enter" && activeSuggestion >= 0) {
      event.preventDefault();
      const selected = visibleSuggestions[activeSuggestion];
      if (selected) chooseSuggestion(selected);
    } else if (event.key === "Escape") {
      setSuggestions([]);
      setActiveSuggestion(-1);
    }
  };

  const suggestionList = visibleSuggestions.length > 0 && (
    <ul
      className="suggestions"
      id={suggestionId}
      role="listbox"
      aria-label={translate(locale, "search.suggestions")}
    >
      {visibleSuggestions.map((suggestion, index) => (
        <li key={suggestion} role="presentation">
          <button
            id={`${suggestionId}-${index}`}
            type="button"
            role="option"
            aria-selected={activeSuggestion === index}
            onClick={() => chooseSuggestion(suggestion)}
          >
            {suggestion}
          </button>
        </li>
      ))}
    </ul>
  );

  return (
    <section className="surface search-shell" aria-label={translate(locale, "search.title")}>
      <div className="search-mode" role="group" aria-label={translate(locale, "search.title")}>
        <button
          className="mode-button"
          type="button"
          aria-pressed={mode === "guided"}
          onClick={() => setMode("guided")}
        >
          {translate(locale, "search.modeGuided")}
        </button>
        <button
          className="mode-button"
          type="button"
          aria-pressed={mode === "advanced"}
          onClick={() => setMode("advanced")}
        >
          {translate(locale, "search.modeAdvanced")}
        </button>
      </div>

      {mode === "guided" ? (
        <form method="get" action={action}>
          <input type="hidden" name="q" value={query} />
          <div className="form-grid">
            <div className="field field--wide">
              <label htmlFor={`${suggestionId}-keywords`}>
                {translate(locale, "search.keywords")}
              </label>
              <input
                ref={inputRef as React.RefObject<HTMLInputElement>}
                id={`${suggestionId}-keywords`}
                name="keywords"
                value={guided.keywords ?? ""}
                onChange={(event) => setField("keywords", event.currentTarget.value)}
                onKeyDown={onSuggestionKeyDown}
                role="combobox"
                aria-autocomplete="list"
                aria-controls={visibleSuggestions.length > 0 ? suggestionId : undefined}
                aria-expanded={visibleSuggestions.length > 0}
                aria-activedescendant={
                  activeSuggestion >= 0 ? `${suggestionId}-${activeSuggestion}` : undefined
                }
                autoComplete="off"
              />
              {suggestionList}
              {suggestValue.trim().length >= 2 && suggestionError && (
                <span role="status">{translate(locale, "search.suggestionError")}</span>
              )}
            </div>
            <ComposerField
              locale={locale}
              id={`${suggestionId}-creator`}
              name="creator"
              value={guided.creator}
              onChange={(value) => setField("creator", value)}
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-version`}
              name="version"
              value={guided.version}
              onChange={(value) => setField("version", value)}
            />
            <div className="field">
              <label htmlFor={`${suggestionId}-category`}>
                {translate(locale, "search.category")}
              </label>
              <select
                id={`${suggestionId}-category`}
                name="category"
                value={guided.category ?? ""}
                onChange={(event) => setField("category", event.currentTarget.value)}
              >
                <option value="">{translate(locale, "search.allCategories")}</option>
                {(["door", "entrance", "extender", "utility"] as const).map((category) => (
                  <option key={category} value={category}>
                    {translateTaxonomy(locale, category)}
                  </option>
                ))}
              </select>
            </div>
            <ComposerField
              locale={locale}
              id={`${suggestionId}-type`}
              name="type"
              value={guided.type}
              onChange={(value) => setField("type", value)}
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-maxWidth`}
              name="maxWidth"
              value={guided.maxWidth}
              onChange={(value) => setField("maxWidth", value)}
              inputMode="decimal"
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-maxHeight`}
              name="maxHeight"
              value={guided.maxHeight}
              onChange={(value) => setField("maxHeight", value)}
              inputMode="decimal"
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-maxDepth`}
              name="maxDepth"
              value={guided.maxDepth}
              onChange={(value) => setField("maxDepth", value)}
              inputMode="decimal"
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-maxOpening`}
              name="maxOpening"
              value={guided.maxOpening}
              onChange={(value) => setField("maxOpening", value)}
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-maxClosing`}
              name="maxClosing"
              value={guided.maxClosing}
              onChange={(value) => setField("maxClosing", value)}
            />
            <ComposerField
              locale={locale}
              id={`${suggestionId}-tag`}
              name="tag"
              value={guided.tag}
              onChange={(value) => setField("tag", value)}
            />
            <SortField locale={locale} id={`${suggestionId}-sort`} initialSort={initialSort} />
          </div>
          <div className="form-actions">
            <a href={localizePath("/search/help", locale)}>{translate(locale, "search.help")}</a>
            <button className="button button--primary" type="submit">
              {translate(locale, "search.submit")}
            </button>
          </div>
        </form>
      ) : (
        <form method="get" action={action}>
          <div className="form-grid">
            <div className="field field--wide">
              <label htmlFor={`${suggestionId}-advanced`}>
                {translate(locale, "search.query")}
              </label>
              <textarea
                ref={inputRef as React.RefObject<HTMLTextAreaElement>}
                id={`${suggestionId}-advanced`}
                name="q"
                value={advanced}
                maxLength={1000}
                onChange={(event) => setAdvanced(event.currentTarget.value)}
                onKeyDown={onSuggestionKeyDown}
                role="combobox"
                aria-autocomplete="list"
                aria-controls={visibleSuggestions.length > 0 ? suggestionId : undefined}
                aria-expanded={visibleSuggestions.length > 0}
              />
              {suggestionList}
              {suggestValue.trim().length >= 2 && suggestionError && (
                <span role="status">{translate(locale, "search.suggestionError")}</span>
              )}
            </div>
            <SortField
              locale={locale}
              id={`${suggestionId}-advanced-sort`}
              initialSort={initialSort}
            />
          </div>
          <div className="form-actions">
            <a href={localizePath("/search/help", locale)}>{translate(locale, "search.help")}</a>
            <button className="button button--primary" type="submit">
              {translate(locale, "search.submit")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

type ComposerFieldProps = {
  locale: Locale;
  id: string;
  name: keyof GuidedSearch;
  value?: string;
  inputMode?: React.HTMLAttributes<HTMLInputElement>["inputMode"];
  onChange: (value: string) => void;
};

function ComposerField({ locale, id, name, value, inputMode, onChange }: ComposerFieldProps) {
  const translationKey = `search.${name}` as const;
  return (
    <div className="field">
      <label htmlFor={id}>{translate(locale, translationKey)}</label>
      <input
        id={id}
        name={name}
        value={value ?? ""}
        inputMode={inputMode}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </div>
  );
}

function SortField({
  locale,
  id,
  initialSort,
}: {
  locale: Locale;
  id: string;
  initialSort: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{translate(locale, "search.sort")}</label>
      <select id={id} name="sort" defaultValue={initialSort}>
        <option value="">{translate(locale, "search.sortRelevance")}</option>
        <option value="-created_at">{translate(locale, "search.sortNewest")}</option>
        <option value="created_at">{translate(locale, "search.sortOldest")}</option>
        <option value="width">{translate(locale, "search.sortWidth")}</option>
        <option value="-width">{translate(locale, "search.sortWidthDesc")}</option>
      </select>
    </div>
  );
}
