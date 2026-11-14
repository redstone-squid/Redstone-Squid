<script lang="ts">
  import { untrack } from "svelte";
  import { SvelteURLSearchParams } from "svelte/reactivity";
  import SuggestField from "./SuggestField.svelte";
  import Field from "../../ui/svelte/Field.svelte";
  import Button from "../../ui/svelte/Button.svelte";
  import { type GuidedSearch } from "../../lib/search";
  import {
    localizePath,
    translate,
    translateTaxonomy,
    type Locale,
    type StaticMessageKey,
  } from "../../lib/i18n";
  import "./search.css";
  type Mode = "guided" | "advanced";
  let {
    locale,
    action,
    initialGuided = {},
    initialQuery = "",
    initialSort = "",
    initialMode = "guided",
  }: {
    locale: Locale;
    action: string;
    initialGuided?: GuidedSearch;
    initialQuery?: string;
    initialSort?: string;
    initialMode?: Mode;
  } = $props();
  const id = $props.id();
  let mode = $state(untrack(() => initialMode));
  let guided = $state<Required<GuidedSearch>>(
    untrack(() => ({
      keywords: initialGuided.keywords ?? "",
      creator: initialGuided.creator ?? "",
      version: initialGuided.version ?? "",
      category: initialGuided.category ?? "",
      type: initialGuided.type ?? "",
      maxWidth: initialGuided.maxWidth ?? "",
      maxHeight: initialGuided.maxHeight ?? "",
      maxDepth: initialGuided.maxDepth ?? "",
      maxOpening: initialGuided.maxOpening ?? "",
      maxClosing: initialGuided.maxClosing ?? "",
      tag: initialGuided.tag ?? "",
    })),
  );
  let advanced = $state(untrack(() => initialQuery));
  let sort = $state(untrack(() => initialSort));
  const t = (key: StaticMessageKey) => translate(locale, key);
  const sources = [
    { name: "keywords", source: "build_titles" },
    { name: "creator", source: "creators" },
    { name: "version", source: "approved_source_versions" },
  ] as const;
  const maxima = ["maxWidth", "maxHeight", "maxDepth", "maxOpening", "maxClosing"] as const;
  let filtersOpen = $state(
    untrack(() =>
      Boolean(initialGuided.type || initialGuided.tag || maxima.some((key) => initialGuided[key])),
    ),
  );
  function modeHref(next: Mode): string {
    const parameters = new SvelteURLSearchParams({
      mode: next,
      sort,
    });
    if (next === "advanced") parameters.set("q", advanced);
    for (const [key, value] of Object.entries(guided)) if (value) parameters.set(key, value);
    return `${action}?${parameters}`;
  }
</script>

<section class="sq-panel search-composer" aria-label={t("search.title")}>
  <nav class="search-modes" aria-label={t("search.title")}>
    {#each ["guided", "advanced"] as next (next)}
      <a
        href={modeHref(next as Mode)}
        aria-current={mode === next ? "true" : undefined}
        onclick={(event) => {
          if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
            event.preventDefault();
            mode = next as Mode;
          }
        }}>{t(next === "guided" ? "search.modeGuided" : "search.modeAdvanced")}</a
      >
    {/each}
  </nav>
  <form method="get" {action}>
    <input type="hidden" name="mode" value={mode} />
    {#if mode === "guided"}
      <div class="search-fields">
        {#each sources as field (field.name)}<SuggestField
            {locale}
            source={field.source}
            name={field.name}
            label={t(`search.${field.name}`)}
            bind:value={guided[field.name]}
          />{/each}
        <div class="sq-field">
          <label for={`${id}-category`}>{t("search.category")}</label><select
            class="sq-input"
            id={`${id}-category`}
            name="category"
            bind:value={guided.category}
            ><option value="">{t("search.allCategories")}</option
            >{#each ["door", "entrance", "extender", "utility"] as category (category)}<option
                value={category}>{translateTaxonomy(locale, category)}</option
              >{/each}</select
          >
        </div>
      </div>
      <details class="search-filters" bind:open={filtersOpen}>
        <summary>{t("search.moreFilters")}</summary>
        <div class="search-fields">
          <SuggestField
            {locale}
            source="approved_patterns"
            name="type"
            label={t("search.type")}
            bind:value={guided.type}
          />
          {#each maxima as name (name)}<Field
              {name}
              label={t(`search.${name}`)}
              bind:value={guided[name]}
              inputmode={name === "maxOpening" || name === "maxClosing" ? "text" : "decimal"}
            />{/each}
          <SuggestField
            {locale}
            source="approved_showcase_tags"
            name="tag"
            label={t("search.tag")}
            bind:value={guided.tag}
          />
        </div>
      </details>
    {:else}
      <SuggestField
        {locale}
        source="search_query"
        name="q"
        label={t("search.query")}
        bind:value={advanced}
        multiline
      />
    {/if}
    <div class="search-actions">
      <div class="sq-field">
        <label for={`${id}-sort`}>{t("search.sort")}</label><select
          class="sq-input"
          id={`${id}-sort`}
          name="sort"
          bind:value={sort}
        >
          {#each [["", "search.sortRelevance"], ["-created_at", "search.sortNewest"], ["created_at", "search.sortOldest"], ["width", "search.sortWidth"], ["-width", "search.sortWidthDesc"]] as [value, key] (value)}<option
              {value}>{t(key as StaticMessageKey)}</option
            >{/each}
        </select>
      </div>
      <a href={localizePath("/search/help", locale)}>{t("search.help")}</a>
      <Button type="submit" variant="primary"
        >{t("search.submit")} <span aria-hidden="true">→</span></Button
      >
    </div>
  </form>
</section>
