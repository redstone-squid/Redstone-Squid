import { useId, useMemo, useState } from "react";

import SuggestField from "./SuggestField";
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
  const suggestionId = useId();
  const query = useMemo(() => composeGuidedQuery(guided), [guided]);
  const setField = (key: keyof GuidedSearch, value: string) =>
    setGuided((current) => ({ ...current, [key]: value }));

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
            <SuggestField
              locale={locale}
              source="build_titles"
              id={`${suggestionId}-keywords`}
              name="keywords"
              label={translate(locale, "search.keywords")}
              value={guided.keywords ?? ""}
              onChange={(value) => setField("keywords", value)}
              multiline={false}
            />
            <SuggestField
              locale={locale}
              source="creators"
              id={`${suggestionId}-creator`}
              name="creator"
              label={translate(locale, "search.creator")}
              value={guided.creator ?? ""}
              onChange={(value) => setField("creator", value)}
            />
            <SuggestField
              locale={locale}
              source="approved_source_versions"
              id={`${suggestionId}-version`}
              name="version"
              label={translate(locale, "search.version")}
              value={guided.version ?? ""}
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
            <SuggestField
              locale={locale}
              source="approved_patterns"
              id={`${suggestionId}-type`}
              name="type"
              label={translate(locale, "search.type")}
              value={guided.type ?? ""}
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
            <SuggestField
              locale={locale}
              source="approved_showcase_tags"
              id={`${suggestionId}-tag`}
              name="tag"
              label={translate(locale, "search.tag")}
              value={guided.tag ?? ""}
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
            <SuggestField
              locale={locale}
              source="search_query"
              id={`${suggestionId}-advanced`}
              name="q"
              label={translate(locale, "search.query")}
              value={advanced}
              onChange={setAdvanced}
              multiline
              maxLength={1000}
              cursorAware
            />
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
