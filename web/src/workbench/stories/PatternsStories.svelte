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
  import ConfirmDialog from "@/ui/patterns/ConfirmDialog.svelte";
  import Switch from "@/ui/forms/Switch.svelte";
  import type { SaveState } from "@/ui/feedback/save-state";
  import type { MediaItem, MediaStatus } from "@/ui/patterns/media";
  import MediaUploader from "@/ui/patterns/MediaUploader.svelte";
  import FlashNotice from "@/ui/patterns/FlashNotice.svelte";
  import NavBar from "@/ui/patterns/NavBar.svelte";
  import OneTimeSecret from "@/ui/patterns/OneTimeSecret.svelte";
  import StepFlow from "@/ui/patterns/StepFlow.svelte";
  import SettingsList from "@/ui/patterns/SettingsList.svelte";
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
  let revokeError = $state<string>();
  const wait = () => new Promise<void>((resolve) => setTimeout(resolve, 800));
  const saveLabels = {
    idle: "",
    dirty: "Unsaved",
    saving: "Saving",
    saved: "Saved",
    offline: "Offline",
    error: "Could not save",
    conflict: "Changed elsewhere",
    retry: "Retry",
  };
  const settingStates = $state<Record<string, SaveState>>({
    dm: "idle",
    digest: "saved",
    mentions: "error",
  });
  async function saveSetting(id: string): Promise<void> {
    settingStates[id] = "saving";
    await wait();
    settingStates[id] = id === "mentions" ? "error" : "saved";
  }
  let media = $state<MediaItem[]>([
    {
      id: "front",
      name: "front-view.png",
      status: "ready",
      primary: true,
      thumb: { src: "/favicon.svg", width: 32, height: 32 },
    },
    { id: "open", name: "door-opening.mp4", status: "processing" },
    { id: "wiring", name: "wiring-closeup.png", status: "uploading", progress: 40 },
    { id: "huge", name: "full-build-render.png", status: "failed", error: "Larger than 8 MB." },
    { id: "old", name: "old-screenshot.png", status: "removed" },
  ]);
  function setMedia(id: string, change: Partial<MediaItem>): void {
    media = media.map((item) => (item.id === id ? { ...item, ...change } : item));
  }
  const mediaStatus: Record<MediaStatus, string> = {
    queued: "Waiting to upload",
    uploading: "Uploading",
    processing: "Processing",
    ready: "Ready",
    failed: "Upload failed",
    removed: "Removed",
  };
  const mergeSteps = ["code", "preview", "confirm"];
  let mergeStep = $state("code");
  const stepLabels = {
    pending: "Not started",
    current: "Current step",
    done: "Done",
    error: "Needs attention",
  };
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
    <NavBar
      label="Story navigation"
      menuLabel="Menu"
      home={{ label: m.siteName(), href: "#navbar", logo: "/favicon.svg" }}
      {links}
      actions={navActions}
    />
  </Variant>
  <Variant label="narrow frame, links collapsed">
    <div class="max-w-dialog-sm">
      <NavBar
        label="Narrow story navigation"
        menuLabel="Menu"
        home={{ label: m.siteName(), href: "#navbar", logo: "/favicon.svg" }}
        {links}
      />
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

<Story
  name="ConfirmDialog"
  source="patterns/ConfirmDialog"
  description="Confirms an irreversible action; danger starts focus on Cancel."
>
  <Layout pattern="cluster">
    <ConfirmDialog
      title="Revoke Laptop CLI?"
      description="The device signs out at once and must enroll again."
      confirmLabel="Revoke"
      cancelLabel="Cancel"
      closeLabel="Close"
      pendingLabel="Revoking"
      trigger={{ label: "Revoke device", variant: "danger" }}
      fallbackHref="#confirmdialog"
      onConfirm={wait}
    />
    <ConfirmDialog
      title="Merge accounts?"
      consequences={[
        "Builds and records move to CircuitSage.",
        "The other account's sign-in links are removed.",
        "This cannot be undone.",
      ]}
      requireText={{
        label: "Type CircuitSage to confirm",
        expected: "CircuitSage",
        error: "Type the account name exactly.",
      }}
      confirmLabel="Merge accounts"
      cancelLabel="Cancel"
      closeLabel="Close"
      trigger={{ label: "Merge accounts" }}
      onConfirm={wait}
    />
    <ConfirmDialog
      title="Reject this proposal?"
      confirmLabel="Reject"
      cancelLabel="Cancel"
      closeLabel="Close"
      error={revokeError}
      trigger={{ label: "Reject, failing" }}
      onConfirm={async () => {
        await wait();
        revokeError = "The proposal changed since you opened it. Reload and try again.";
        throw new Error("conflict");
      }}
    />
    <ConfirmDialog
      tone="default"
      title="Publish the draft?"
      description="Staff will review it before it appears in the catalogue."
      confirmLabel="Publish"
      cancelLabel="Not yet"
      closeLabel="Close"
      trigger={{ label: "Publish", variant: "primary" }}
      onConfirm={wait}
    />
  </Layout>
</Story>

{#snippet dmSwitch()}
  <Switch
    label="Direct messages"
    hint="Messages from the bot about your builds."
    checked
    onchange={() => saveSetting("dm")}
  />
{/snippet}
{#snippet digestSwitch()}
  <Switch label="Weekly digest" onchange={() => saveSetting("digest")} />
{/snippet}
{#snippet mentionsSwitch()}
  <Switch
    label="Mentions"
    error="Your Discord DMs are closed."
    onchange={() => saveSetting("mentions")}
  />
{/snippet}

<Story
  name="SettingsList"
  source="patterns/SettingsList"
  description="Settings that save on change, each with its own status. Toggle them to see saving."
>
  <SettingsList
    title="Notifications"
    description="Changes save as you make them."
    labels={saveLabels}
    onRetry={saveSetting}
    items={[
      { id: "dm", control: dmSwitch, state: settingStates.dm ?? "idle" },
      { id: "digest", control: digestSwitch, state: settingStates.digest ?? "idle" },
      { id: "mentions", control: mentionsSwitch, state: settingStates.mentions ?? "idle" },
    ]}
  />
</Story>

<Story
  name="MediaUploader"
  source="patterns/MediaUploader"
  description="Files with status, progress, retry, primary and an inline undo for removal."
>
  <MediaUploader
    items={media}
    label="Images and videos"
    hint="Show the door closed and open."
    limitsLabel="Up to 10 files, 8 MB each."
    accept="image/*,video/*"
    labels={{
      drop: "Drop files here, or",
      browse: "Choose files",
      status: mediaStatus,
      primary: "Cover",
      remove: (name) => `Remove ${name}`,
      undo: "Undo",
      retry: "Retry",
      setPrimary: (name) => `Use ${name} as the cover`,
      summary: ({ ready, failed }) => `${ready} files ready, ${failed} failed`,
    }}
    onFiles={(files) => {
      media = [
        ...media,
        ...files.map((file) => ({
          id: crypto.randomUUID(),
          name: file.name,
          status: "queued" as const,
        })),
      ];
    }}
    onRetry={(id) => setMedia(id, { status: "uploading", progress: 10, error: undefined })}
    onRemove={(id) => setMedia(id, { status: "removed" })}
    onUndoRemove={(id) => setMedia(id, { status: "ready" })}
    onSetPrimary={(id) => {
      media = media.map((item) => ({ ...item, primary: item.id === id }));
    }}
  />
</Story>

<Story
  name="OneTimeSecret"
  source="patterns/OneTimeSecret"
  description="A secret shown once, with copy and an acknowledgement; leaving early asks first."
>
  <OneTimeSecret
    value="sqd_inst_7f3a9c2e41b84d0f9a6e5c1b2d3f4a5b"
    label="Installation secret"
    warning="Copy this secret now. It will not be shown again."
    detail="Paste it into the plugin's config.yml."
    copyLabels={{ label: "Copy", copied: "Copied", failed: "Copy failed" }}
    acknowledgeLabel="I saved the secret"
    guardUnload={false}
  />
</Story>

<Story
  name="StepFlow"
  source="patterns/StepFlow"
  description="Steps with the current one marked; moving between steps focuses the step heading."
>
  <StepFlow
    label="Merge progress"
    current={mergeStep}
    labels={stepLabels}
    steps={[
      { id: "code", label: "Enter the code", status: mergeStep === "code" ? "pending" : "done" },
      {
        id: "preview",
        label: "Review the merge",
        status: mergeStep === "confirm" ? "done" : "pending",
      },
      { id: "confirm", label: "Confirm", status: "pending" },
    ]}
  >
    <Layout pattern="cluster">
      <Button
        disabled={mergeStep === "code"}
        onclick={() => (mergeStep = mergeSteps[mergeSteps.indexOf(mergeStep) - 1] ?? "code")}
        >Back</Button
      >
      <Button
        variant="primary"
        disabled={mergeStep === "confirm"}
        onclick={() => (mergeStep = mergeSteps[mergeSteps.indexOf(mergeStep) + 1] ?? "confirm")}
        >Continue</Button
      >
    </Layout>
  </StepFlow>
  <Variant label="a failed phase">
    <StepFlow
      label="Finalization"
      current="render"
      labels={stepLabels}
      headingLevel={3}
      steps={[
        { id: "check", label: "Check the draft", status: "done" },
        { id: "render", label: "Render previews", status: "error" },
        { id: "publish", label: "Publish", status: "pending" },
      ]}
    >
      <Notice tone="danger">The schematic could not be rendered.</Notice>
    </StepFlow>
  </Variant>
</Story>

<Story
  name="FlashNotice"
  source="patterns/FlashNotice"
  description="The outcome of a server-handled form after its redirect, announced once hydrated."
>
  <FlashNotice message="Profile saved." />
  <FlashNotice tone="info" message="The draft was discarded." />
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
