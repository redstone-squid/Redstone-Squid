<script lang="ts">
  import * as m from "@/locales/messages-patterns.gen";
  import Pencil from "@lucide/svelte/icons/pencil";
  import BuildCard from "@/features/catalogue/BuildCard.svelte";
  import RecordCard from "@/features/catalogue/RecordCard.svelte";
  import Button from "@/ui/actions/Button.svelte";
  import Avatar from "@/ui/data/Avatar.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import SelectField from "@/ui/forms/SelectField.svelte";
  import ActiveFilters from "@/ui/patterns/ActiveFilters.svelte";
  import FilterPanel from "@/ui/patterns/FilterPanel.svelte";
  import LayoutGrid from "@lucide/svelte/icons/layout-grid";
  import Table from "@lucide/svelte/icons/table";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import ErrorState from "@/ui/feedback/ErrorState.svelte";
  import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
  import NavBar from "@/ui/patterns/NavBar.svelte";
  import ResultsToolbar from "@/ui/patterns/ResultsToolbar.svelte";
  import ResultsView from "@/ui/patterns/ResultsView.svelte";
  import SearchBox from "@/ui/patterns/SearchBox.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import SplitLayout from "@/ui/patterns/SplitLayout.svelte";
  import Variant from "./Variant.svelte";
  import { buildFixtures, recordFixtures } from "@/workbench/fixtures";
  import Story from "./Story.svelte";

  const categories = [m.trialDoor(), m.storySeamlessDoor(), m.storyExtender()];
  const statuses = ["ready", "refreshing", "loading", "empty", "error"] as const;
  let resultsStatus = $state<(typeof statuses)[number]>("ready");
</script>

<Story name="PageHeader" source="patterns/PageHeader" description={m.storyPageHeaderDescription()}>
  <PageHeader
    title={m.storyCopperBolt55()}
    eyebrow={m.trialDoor()}
    description={m.storyAFastFlush55Door()}
    breadcrumbs={{
      label: m.storyBreadcrumb(),
      items: [{ label: m.navBuilds(), href: "#pageheader" }, { label: m.storyCopperBolt55() }],
    }}
  >
    {#snippet media()}<Avatar name="CircuitSage" size="lg" />{/snippet}
    {#snippet meta()}<Badge tone="success" dot>{m.storyApproved()}</Badge>{/snippet}
    {#snippet actions()}<Button icon={Pencil}>{m.storyEdit()}</Button>{/snippet}
    {#snippet notice()}<Notice tone="warning">{m.storyPendingReviewByStaff()}</Notice>{/snippet}
  </PageHeader>
</Story>

<Story name="Section" source="patterns/Section">
  <Section title={m.recordHolders()} description={m.storyBuildsThatHoldThisRecord()}>
    {#snippet actions()}<Button size="sm" variant="quiet">{m.storyViewAll()}</Button>{/snippet}
    <p class="text-sm">{m.storySectionContent()}</p>
  </Section>
  <Section title={m.storySurfaceSection()} surface level={3}>
    <p class="text-sm">{m.storyDrawnAsAPanel()}</p>
  </Section>
</Story>

{#snippet brand()}
  <a href="#navbar" class="inline-flex items-center gap-3 rounded-sm font-bold no-underline">
    <img class="size-8" src="/favicon.svg" width="32" height="32" alt="" />Redstone Squid
  </a>
{/snippet}
{#snippet navActions()}<Button size="sm" variant="primary">Submit</Button>{/snippet}

<Story
  name="NavBar"
  source="patterns/NavBar"
  description="The site header body. The links collapse into a popover when the bar is narrow."
>
  {@const links = [
    { label: "Builds", href: "#navbar-builds", current: true },
    { label: "Records", href: "#navbar-records" },
    { label: "Search", href: "#navbar-search" },
    { label: "About", href: "#navbar-about" },
  ]}
  <Variant label="as wide as the page">
    <NavBar label="Story navigation" menuLabel="Menu" {brand} {links} actions={navActions} />
  </Variant>
  <Variant label="narrow frame, links collapsed">
    <div class="max-w-dialog-sm">
      <NavBar label="Narrow story navigation" menuLabel="Menu" {brand} {links} />
    </div>
  </Variant>
</Story>

<Story
  name="SearchBox"
  source="patterns/SearchBox"
  description="A GET search form that keeps other query parameters."
>
  <Layout pattern="grid" min="card-wide">
    <Variant label="page search, keeping scope">
      <SearchBox
        action="/design-system/patterns"
        label="Search builds and records"
        placeholder="Piston door, 3×3, CircuitSage"
        submitLabel="Search"
        hidden={{ scope: "builds" }}
        value="flush"
      />
    </Variant>
    <Variant label="compact, for the header">
      <SearchBox action="/design-system/patterns" label="Search" submitLabel="Search" compact />
    </Variant>
    <Variant label="with suggestions">
      <SearchBox
        action="/design-system/patterns"
        label="Search records"
        submitLabel="Search"
        suggestions={{
          labels: {
            suggestions: "Suggested searches",
            loading: "Loading",
            error: "Suggestions are unavailable.",
            noResults: "No suggestions",
            count: (count) => `${count} suggestions`,
          },
          load: (query) =>
            Promise.resolve({
              items: ["piston door", "piston extender", "pulse circuit"]
                .filter((term) => term.includes(query.trim().toLowerCase()))
                .map((term) => ({ value: term, label: term })),
            }),
        }}
      />
    </Variant>
  </Layout>
</Story>

{#snippet filterFields()}
  <SelectField
    label="Category"
    name="category"
    value="door"
    options={[
      { value: "", label: "Any category" },
      { value: "door", label: "Piston door" },
      { value: "extender", label: "Extender" },
    ]}
  />
  <CheckField label="Record holders only" name="holders" value="1" checked />
{/snippet}

<Story
  name="FilterPanel"
  source="patterns/FilterPanel"
  description="Filters in a GET form; auto-apply navigates after a change once hydrated."
>
  <Layout pattern="grid" min="card-wide">
    <Variant label="apply button, two active">
      <FilterPanel
        action="/design-system/patterns"
        title="Filters"
        activeLabel="2 active"
        applyLabel="Apply filters"
        resetHref="/design-system/patterns"
        resetLabel="Reset"
        hidden={{ q: "door" }}
        children={filterFields}
      />
    </Variant>
    <Variant label="in a SplitLayout sheet">
      <SplitLayout
        sidebarLabel="Filters"
        triggerLabel="Filters · 2"
        closeLabel="Close filters"
        collapse="sheet"
      >
        {#snippet sidebar()}
          <FilterPanel
            action="/design-system/patterns"
            title="Filters"
            applyLabel="Apply filters"
            resetHref="/design-system/patterns"
            resetLabel="Reset"
            children={filterFields}
          />
        {/snippet}
        <p class="text-sm">Results.</p>
      </SplitLayout>
    </Variant>
  </Layout>
</Story>

<Story
  name="ActiveFilters"
  source="patterns/ActiveFilters"
  description="Removable chips for applied filters, each a link to the URL without it."
>
  <ActiveFilters
    items={[
      { label: "Piston door", removeLabel: "Remove Piston door", removeHref: "#activefilters-1" },
      { label: "Width ≥ 5", removeLabel: "Remove width ≥ 5", removeHref: "#activefilters-2" },
      {
        label: "Record holders",
        removeLabel: "Remove Record holders",
        removeHref: "#activefilters-3",
      },
    ]}
    clearHref="#activefilters"
    labels={{ region: "Active filters", clear: "Clear all filters" }}
  />
</Story>

<Story
  name="ResultsToolbar"
  source="patterns/ResultsToolbar"
  description="Count, sort menu, view switch and extra actions, all links."
>
  <ResultsToolbar
    count="312 builds"
    sort={{
      label: "Sort order",
      current: "Sort: Newest",
      options: [
        { label: "Newest", href: "#resultstoolbar-newest", current: true },
        { label: "Fastest opening", href: "#resultstoolbar-fastest" },
        { label: "Smallest volume", href: "#resultstoolbar-smallest" },
      ],
    }}
    view={{
      label: "Results view",
      items: [
        { label: "Cards", href: "#resultstoolbar-cards", current: true, icon: LayoutGrid },
        { label: "Table", href: "#resultstoolbar-table", icon: Table },
      ],
    }}
  >
    {#snippet extra()}<Button size="sm" variant="quiet">Follow this search</Button>{/snippet}
  </ResultsToolbar>
  <Variant label="count only, live">
    <ResultsToolbar count="1 record" live />
  </Variant>
</Story>

<Story
  name="ResultsView"
  source="patterns/ResultsView"
  description="Cards or table by view, with loading, refreshing, empty and error states."
>
  <SegmentedControl
    mode="radio"
    name="results-status"
    label="Results state"
    bind:value={resultsStatus}
    items={statuses.map((status) => ({ label: status, value: status }))}
  />
  <ResultsView view="cards" status={resultsStatus} busyLabel="Loading builds" id="story-results">
    {#snippet cards()}
      <Layout as="ul" pattern="grid">
        {#each buildFixtures() as build, index (build.id)}
          <BuildCard {build} category={categories[index] ?? ""} as="li" />
        {/each}
      </Layout>
    {/snippet}
    {#snippet empty()}
      <EmptyState
        title="No builds match “flush 9×9”"
        description="Remove a filter or search all builds."
      />
    {/snippet}
    {#snippet error()}
      <ErrorState
        title="Builds could not load"
        retryLabel="Try again"
        onRetry={() => (resultsStatus = "ready")}
      />
    {/snippet}
  </ResultsView>
  <Variant label="table view, loading">
    <ResultsView
      view="table"
      status="loading"
      busyLabel="Loading records"
      id="story-table-results"
    />
  </Variant>
</Story>

{#snippet sideNav()}
  <ul class="grid gap-2 text-sm">
    <li><a href="#splitlayout">General</a></li>
    <li><a href="#splitlayout">Media</a></li>
    <li><a href="#splitlayout">Credits</a></li>
  </ul>
{/snippet}

<Story
  name="SplitLayout"
  source="patterns/SplitLayout"
  description="A sidebar beside the content, sized by its own width. Narrow it to see the collapse."
>
  <Variant label="stack, sidebar at the start">
    <SplitLayout sidebarLabel="Sections" sidebar={sideNav}>
      <p class="text-sm">Main content flows beside the sidebar when there is room.</p>
    </SplitLayout>
  </Variant>
  <Variant label="sheet, sidebar at the end, sticky">
    <SplitLayout
      sidebarLabel="Filters"
      triggerLabel="Filters · 2"
      closeLabel="Close filters"
      collapse="sheet"
      side="end"
      sticky
      sidebar={sideNav}
    >
      <p class="text-sm">On a narrow layout the sidebar opens as a sheet from the end edge.</p>
    </SplitLayout>
  </Variant>
  <Variant label="sheet in a narrow frame">
    <div class="max-w-dialog-sm">
      <SplitLayout
        sidebarLabel="Account"
        closeLabel="Close account navigation"
        collapse="sheet"
        sidebar={sideNav}
      >
        <p class="text-sm">This frame is always narrow, so the sidebar stays behind a button.</p>
      </SplitLayout>
    </div>
  </Variant>
</Story>

<Story
  name="Catalogue cards"
  heading={m.storyCatalogueCards()}
  source="features/catalogue"
  description={m.storyCatalogueCardsDescription()}
>
  <Layout as="ul" pattern="grid">
    {#each buildFixtures() as build, index (build.id)}
      <BuildCard {build} category={categories[index] ?? ""} as="li" />
    {/each}
  </Layout>
  <Layout as="ul" pattern="grid" min="card-wide">
    {#each recordFixtures() as record (record.record_id)}
      <RecordCard {record} as="li" />
    {/each}
  </Layout>
</Story>
