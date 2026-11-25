<script lang="ts">
  import * as m from "@/locales/messages-patterns.gen";
  import Copy from "@lucide/svelte/icons/copy";
  import Download from "@lucide/svelte/icons/download";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import History from "@lucide/svelte/icons/history";
  import Layers from "@lucide/svelte/icons/layers";
  import Pencil from "@lucide/svelte/icons/pencil";
  import Trophy from "@lucide/svelte/icons/trophy";
  import Vote from "@lucide/svelte/icons/vote";
  import BuildCard from "@/features/catalogue/BuildCard.svelte";
  import RecordCard from "@/features/catalogue/RecordCard.svelte";
  import Button from "@/ui/actions/Button.svelte";
  import Avatar from "@/ui/data/Avatar.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Menu from "@/ui/overlays/Menu.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import RelatedLinks from "@/ui/patterns/RelatedLinks.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import SelectField from "@/ui/forms/SelectField.svelte";
  import ActiveFilters from "@/ui/patterns/ActiveFilters.svelte";
  import FilterPanel from "@/ui/patterns/FilterPanel.svelte";
  import LayoutGrid from "@lucide/svelte/icons/layout-grid";
  import Table from "@lucide/svelte/icons/table";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import ErrorState from "@/ui/feedback/ErrorState.svelte";
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
    dirty: m.storyUnsaved(),
    saving: m.storySaving(),
    saved: m.storySaved(),
    offline: m.storyOffline(),
    error: m.storyCouldNotSave(),
    conflict: m.storyChangedElsewhere(),
    retry: m.storyRetry(),
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
    { id: "huge", name: "full-build-render.png", status: "failed", error: m.storyLargerThan8Mb() },
    { id: "old", name: "old-screenshot.png", status: "removed" },
  ]);
  function setMedia(id: string, change: Partial<MediaItem>): void {
    media = media.map((item) => (item.id === id ? { ...item, ...change } : item));
  }
  const mediaStatus: Record<MediaStatus, string> = {
    queued: m.storyWaitingToUpload(),
    uploading: m.storyUploading(),
    processing: m.storyProcessing(),
    ready: m.storyReady(),
    failed: m.storyUploadFailed(),
    removed: m.storyRemoved(),
  };
  const mergeSteps = ["code", "preview", "confirm"];
  let mergeStep = $state("code");
  const stepLabels = {
    pending: m.storyNotStarted(),
    current: m.storyCurrentStep(),
    done: m.storyDone(),
    error: m.storyNeedsAttention(),
  };
  let resultsStatus = $state<(typeof statuses)[number]>("ready");
  const statusLabels = {
    ready: m.storyReady(),
    refreshing: m.storyRefreshing(),
    loading: m.storyLoading(),
    empty: m.storyEmpty(),
    error: m.storyErrorState(),
  };
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

<Story
  name="RelatedLinks"
  source="patterns/RelatedLinks"
  description={m.storyRelatedlinksDescription()}
>
  {@const count = (value: number) => m.storyRelatedCount({ count: value })}
  <Variant label={m.storyRelatedWithActions()}>
    <div class="grid min-w-0 gap-4">
      <PageHeader title={m.storyCopperBolt55()} eyebrow={m.trialDoor()}>
        {#snippet meta()}<Badge tone="warning" dot>{m.storyPendingReview()}</Badge>{/snippet}
        {#snippet actions()}
          <!-- The vote applies to a pending build, so it stays visible with its reason. -->
          <p id="relatedlinks-vote-reason" class="text-sm text-fg-muted">
            {m.storyVotingClosed()}
          </p>
          <div class="flex items-center gap-3">
            <Button
              variant="primary"
              icon={Vote}
              disabled
              aria-describedby="relatedlinks-vote-reason">{m.storyVote()}</Button
            >
            <Menu
              label={m.storyBuildActions()}
              trigger={{ label: m.storyMoreActions(), iconOnly: true, icon: Ellipsis }}
              items={[
                { label: m.storyEdit(), icon: Pencil, href: "#relatedlinks" },
                { label: m.storyDownloadSchematic(), icon: Download, href: "#relatedlinks" },
                { label: m.storyCopyLink(), icon: Copy, href: "#relatedlinks" },
                { label: m.storySuggestACorrection(), href: "#relatedlinks" },
              ]}
            />
          </div>
        {/snippet}
      </PageHeader>
      <RelatedLinks
        label={m.storyRelatedToBuild()}
        items={[
          {
            label: m.storyRelatedRecordsHeld({ count: 2 }),
            count: count(2),
            href: "#relatedlinks-records",
            icon: Trophy,
          },
          {
            label: m.storyRelatedVersions({ count: 3 }),
            count: count(3),
            href: "#relatedlinks-versions",
            icon: Layers,
          },
          {
            label: m.storyRelatedEdits({ count: 14 }),
            count: count(14),
            href: "#relatedlinks-history",
            icon: History,
          },
        ]}
      />
    </div>
  </Variant>
  <Variant label={m.storyRelatedZeroCounts()}>
    <RelatedLinks
      label={m.storyRelatedToRecord()}
      items={[
        {
          label: m.storyRelatedHolders({ count: 1 }),
          count: count(1),
          href: "#relatedlinks-holders",
          icon: Trophy,
        },
        {
          label: m.storyRelatedPastHolders({ count: 0 }),
          count: count(0),
          href: "#relatedlinks-past",
          icon: History,
        },
        {
          label: m.storyRelatedSiblingRecords({ count: 4 }),
          count: count(4),
          href: "#relatedlinks-siblings",
          icon: Layers,
        },
      ]}
    />
  </Variant>
  <Variant label={m.storyRelatedCreator()}>
    <RelatedLinks
      label={m.storyRelatedToCreator()}
      items={[
        {
          label: m.storyRelatedBuilds({ count: 1204 }),
          count: count(1204),
          href: "#relatedlinks-builds",
        },
        {
          label: m.storyRelatedRecordsHeld({ count: 0 }),
          count: count(0),
          href: "#relatedlinks-held",
        },
        {
          label: m.storyRelatedPastRecords({ count: 37 }),
          count: count(37),
          href: "#relatedlinks-ever",
        },
      ]}
    />
  </Variant>
</Story>

<Story name="Section" source="patterns/Section">
  <Variant label={m.storyWithActions()}>
    <Section title={m.recordHolders()} description={m.storyBuildsThatHoldThisRecord()}>
      {#snippet actions()}<Button size="sm" variant="quiet">{m.storyViewAll()}</Button>{/snippet}
      <p class="text-sm">{m.storyCopperBolt55()}</p>
    </Section>
  </Variant>
  <Variant label={m.storySurfaceLevel3()}>
    <Section title={m.storyDoorEvidence()} surface level={3}>
      <p class="text-sm">{m.storyMeasuredWithATickCounterOnJava1()}</p>
    </Section>
  </Variant>
</Story>

{#snippet navActions()}<Button size="sm" variant="primary">{m.navSubmit()}</Button>{/snippet}

<Story name="NavBar" source="patterns/NavBar" description={m.storyNavbarDescription()}>
  {@const links = [
    { label: m.navBuilds(), href: "#navbar-builds", current: true },
    { label: m.navRecords(), href: "#navbar-records" },
    { label: m.navSearch(), href: "#navbar-search" },
    { label: m.navAbout(), href: "#navbar-about" },
  ]}
  <Variant label={m.storyFullWidth()}>
    <NavBar
      label={m.storyStoryNavigation()}
      menuLabel={m.navMenu()}
      home={{ label: m.siteName(), href: "#navbar", logo: "/favicon.svg" }}
      {links}
      actions={navActions}
    />
  </Variant>
  <Variant label={m.storyNarrow()}>
    <div class="max-w-dialog-sm">
      <NavBar
        label={m.storyNarrowStoryNavigation()}
        menuLabel={m.navMenu()}
        home={{ label: m.siteName(), href: "#navbar", logo: "/favicon.svg" }}
        {links}
      />
    </div>
  </Variant>
</Story>

<Story name="SearchBox" source="patterns/SearchBox" description={m.storySearchboxDescription()}>
  <Layout pattern="stack" gap="lg">
    <Variant label={m.storyPageSearchKeepingScope()}>
      <SearchBox
        action="/design-system/patterns"
        label={m.storySearchBuildsAndRecords()}
        placeholder={m.storySearchPlaceholder()}
        submitLabel={m.navSearch()}
        hidden={{ scope: "builds" }}
        value="flush"
      />
    </Variant>
    <Variant label={m.storyCompact()}>
      <SearchBox
        action="/design-system/patterns"
        label={m.navSearch()}
        submitLabel={m.navSearch()}
        compact
      />
    </Variant>
    <Variant label={m.storyWithSuggestions()}>
      <SearchBox
        action="/design-system/patterns"
        label={m.storySearchRecords()}
        submitLabel={m.navSearch()}
        suggestions={{
          labels: {
            suggestions: m.storySuggestedSearches(),
            loading: m.storyLoading(),
            error: m.storySuggestionsAreUnavailable(),
            noResults: m.storyNoSuggestions(),
            count: (count) => m.storySuggestionCount({ count }),
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
    label={m.trialCategory()}
    name="category"
    value="door"
    options={[
      { value: "", label: m.storyAnyCategory() },
      { value: "door", label: m.workbenchPistonDoor() },
      { value: "extender", label: m.storyExtender() },
    ]}
  />
  <CheckField label={m.storyRecordHoldersOnly()} name="holders" value="1" checked />
{/snippet}

<Story
  name="FilterPanel"
  source="patterns/FilterPanel"
  description={m.storyFilterpanelDescription()}
>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyApplyButtonTwoActive()}>
      <FilterPanel
        action="/design-system/patterns"
        title={m.storyFilters()}
        activeLabel={m.storyTwoActive()}
        applyLabel={m.storyApplyFilters()}
        resetHref="/design-system/patterns"
        resetLabel={m.storyReset()}
        hidden={{ q: "door" }}
        children={filterFields}
      />
    </Variant>
    <Variant label={m.storyInASplitlayoutSheet()}>
      <SplitLayout
        sidebarLabel={m.storyFilters()}
        trigger={{ label: m.storyFiltersTwo() }}
        closeLabel={m.storyCloseFilters()}
        collapse="sheet"
      >
        {#snippet sidebar()}
          <FilterPanel
            action="/design-system/patterns"
            title={m.storyFilters()}
            applyLabel={m.storyApplyFilters()}
            resetHref="/design-system/patterns"
            resetLabel={m.storyReset()}
            children={filterFields}
          />
        {/snippet}
        <p class="text-sm">{m.story312Builds()}</p>
      </SplitLayout>
    </Variant>
  </Layout>
</Story>

<Story
  name="ActiveFilters"
  source="patterns/ActiveFilters"
  description={m.storyActivefiltersDescription()}
>
  <ActiveFilters
    items={[
      {
        label: m.workbenchPistonDoor(),
        removeLabel: m.storyRemovePistonDoor(),
        removeHref: "#activefilters-1",
      },
      {
        label: m.storyWidthAtLeast5(),
        removeLabel: m.storyRemoveWidthAtLeast5(),
        removeHref: "#activefilters-2",
      },
      {
        label: m.storyRecordHolders(),
        removeLabel: m.storyRemoveRecordHolders(),
        removeHref: "#activefilters-3",
      },
    ]}
    clearHref="#activefilters"
    labels={{ region: m.storyActiveFilters(), clear: m.storyClearAllFilters() }}
  />
</Story>

<Story
  name="ResultsToolbar"
  source="patterns/ResultsToolbar"
  description={m.storyResultstoolbarDescription()}
>
  <ResultsToolbar
    count={m.story312Builds()}
    sort={{
      label: m.storySortOrder(),
      current: m.storySortNewest(),
      options: [
        { label: m.storyNewest(), href: "#resultstoolbar-newest", current: true },
        { label: m.storyFastestOpening(), href: "#resultstoolbar-fastest" },
        { label: m.storySmallestVolume(), href: "#resultstoolbar-smallest" },
      ],
    }}
    view={{
      label: m.storyResultsView(),
      items: [
        { label: m.storyCards(), href: "#resultstoolbar-cards", current: true, icon: LayoutGrid },
        { label: m.storyTable(), href: "#resultstoolbar-table", icon: Table },
      ],
    }}
  >
    {#snippet extra()}<Button size="sm" variant="quiet">{m.storyFollowThisSearch()}</Button
      >{/snippet}
  </ResultsToolbar>
  <Variant label={m.storyCountOnlyLive()}>
    <ResultsToolbar count={m.storyOneRecord()} live />
  </Variant>
</Story>

<Story
  name="ResultsView"
  source="patterns/ResultsView"
  description={m.storyResultsviewDescription()}
>
  <SelectField
    label={m.storyResultsState()}
    width="md"
    bind:value={resultsStatus}
    options={statuses.map((status) => ({ value: status, label: statusLabels[status] }))}
  />
  <ResultsView
    view="cards"
    status={resultsStatus}
    busyLabel={m.storyLoadingBuilds()}
    id="story-results"
  >
    {#snippet cards()}
      <Layout as="ul" pattern="grid">
        {#each buildFixtures() as build, index (build.id)}
          <BuildCard {build} category={categories[index] ?? ""} as="li" />
        {/each}
      </Layout>
    {/snippet}
    {#snippet empty()}
      <EmptyState
        title={m.storyNoBuildsMatchFlush()}
        description={m.storyRemoveAFilterOrSearchAll()}
      />
    {/snippet}
    {#snippet error()}
      <ErrorState
        title={m.storyBuildsCouldNotLoad()}
        retryLabel={m.storyTryAgain()}
        onRetry={() => (resultsStatus = "ready")}
      />
    {/snippet}
  </ResultsView>
  <Variant label={m.storyTableViewLoading()}>
    <ResultsView
      view="table"
      status="loading"
      busyLabel={m.storyLoadingRecords()}
      id="story-table-results"
    />
  </Variant>
</Story>

<Story
  name="ConfirmDialog"
  source="patterns/ConfirmDialog"
  description={m.storyConfirmdialogDescription()}
>
  <Layout pattern="cluster" gap="lg">
    <Variant label={m.storyDanger()}>
      <ConfirmDialog
        title={m.storyRevokeLaptopCli()}
        description={m.storyDeviceSignsOut()}
        confirmLabel={m.storyRevoke()}
        cancelLabel={m.storyCancel()}
        closeLabel={m.storyClose()}
        pendingLabel={m.storyRevoking()}
        trigger={{ label: m.storyRevokeDevice(), variant: "danger" }}
        fallbackHref="#confirmdialog"
        onConfirm={wait}
      />
    </Variant>
    <Variant label={m.storyTypedConfirmation()}>
      <ConfirmDialog
        title={m.storyMergeAccountsQuestion()}
        consequences={[
          m.storyBuildsMoveToCircuitsage(),
          m.storySignInLinksRemoved(),
          m.storyCannotBeUndone(),
        ]}
        requireText={{
          label: m.storyTypeCircuitsage(),
          expected: "CircuitSage",
          error: m.storyTypeAccountNameExactly(),
        }}
        confirmLabel={m.storyMergeAccounts()}
        cancelLabel={m.storyCancel()}
        closeLabel={m.storyClose()}
        trigger={{ label: m.storyMergeAccounts() }}
        onConfirm={wait}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <ConfirmDialog
        title={m.storyRejectThisProposal()}
        confirmLabel={m.storyReject()}
        cancelLabel={m.storyCancel()}
        closeLabel={m.storyClose()}
        error={revokeError}
        trigger={{ label: m.storyReject() }}
        onConfirm={async () => {
          await wait();
          revokeError = m.storyProposalChanged();
          throw new Error("conflict");
        }}
      />
    </Variant>
    <Variant label={m.storyDefaultTone()}>
      <ConfirmDialog
        tone="default"
        title={m.storyPublishTheDraft()}
        description={m.storyStaffWillReview()}
        confirmLabel={m.storyPublish()}
        cancelLabel={m.storyNotYet()}
        closeLabel={m.storyClose()}
        trigger={{ label: m.storyPublish(), variant: "primary" }}
        onConfirm={wait}
      />
    </Variant>
  </Layout>
</Story>

{#snippet dmSwitch()}
  <Switch
    label={m.storyDirectMessages()}
    hint={m.storyMessagesFromTheBot()}
    checked
    onchange={() => saveSetting("dm")}
  />
{/snippet}
{#snippet digestSwitch()}
  <Switch label={m.storyWeeklyDigest()} onchange={() => saveSetting("digest")} />
{/snippet}
{#snippet mentionsSwitch()}
  <Switch
    label={m.storyMentions()}
    error={m.storyDiscordDmsClosed()}
    onchange={() => saveSetting("mentions")}
  />
{/snippet}

<Story
  name="SettingsList"
  source="patterns/SettingsList"
  description={m.storySettingslistDescription()}
>
  <SettingsList
    title={m.storyNotifications()}
    description={m.storyChangesSaveAsYouMakeThem()}
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
  description={m.storyMediauploaderDescription()}
>
  <MediaUploader
    items={media}
    label={m.storyImagesAndVideos()}
    hint={m.storyShowTheDoorClosedAndOpen()}
    limitsLabel={m.storyUpTo10Files()}
    accept="image/*,video/*"
    labels={{
      drop: m.storyDropFilesHereOr(),
      browse: m.storyChooseFiles(),
      status: mediaStatus,
      primary: m.storyCover(),
      remove: (name) => m.storyRemoveFile({ name }),
      undo: m.storyUndo(),
      retry: m.storyRetry(),
      setPrimary: (name) => m.storyUseAsCover({ name }),
      summary: ({ ready, failed }) => m.storyFilesSummary({ ready, failed }),
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
  description={m.storyOnetimesecretDescription()}
>
  <OneTimeSecret
    value="sqd_inst_7f3a9c2e41b84d0f9a6e5c1b2d3f4a5b"
    label={m.storyInstallationSecret()}
    warning={m.storyCopyThisSecretNow()}
    detail={m.storyPasteIntoConfig()}
    copyLabels={{ label: m.storyCopy(), copied: m.storyCopied(), failed: m.storyCopyFailed() }}
    acknowledgeLabel={m.storyISavedTheSecret()}
    guardUnload={false}
  />
</Story>

<Story name="StepFlow" source="patterns/StepFlow" description={m.storyStepflowDescription()}>
  <StepFlow
    label={m.storyMergeProgress()}
    current={mergeStep}
    labels={stepLabels}
    steps={[
      {
        id: "code",
        label: m.storyEnterTheCode(),
        status: mergeStep === "code" ? "pending" : "done",
      },
      {
        id: "preview",
        label: m.storyReviewTheMerge(),
        status: mergeStep === "confirm" ? "done" : "pending",
      },
      { id: "confirm", label: m.storyConfirm(), status: "pending" },
    ]}
  >
    <Layout pattern="cluster">
      <Button
        disabled={mergeStep === "code"}
        onclick={() => (mergeStep = mergeSteps[mergeSteps.indexOf(mergeStep) - 1] ?? "code")}
        >{m.storyBack()}</Button
      >
      <Button
        variant="primary"
        disabled={mergeStep === "confirm"}
        onclick={() => (mergeStep = mergeSteps[mergeSteps.indexOf(mergeStep) + 1] ?? "confirm")}
        >{m.storyContinue()}</Button
      >
    </Layout>
  </StepFlow>
  <Variant label={m.storyFailedStep()}>
    <StepFlow
      label={m.storyFinalization()}
      current="render"
      labels={stepLabels}
      headingLevel={3}
      steps={[
        { id: "check", label: m.storyCheckTheDraft(), status: "done" },
        { id: "render", label: m.storyRenderPreviews(), status: "error" },
        { id: "publish", label: m.storyPublish(), status: "pending" },
      ]}
    >
      <Notice tone="danger">{m.storySchematicNotRendered()}</Notice>
    </StepFlow>
  </Variant>
</Story>

<Story
  name="FlashNotice"
  source="patterns/FlashNotice"
  description={m.storyFlashnoticeDescription()}
>
  <FlashNotice message={m.storyProfileSaved()} />
  <FlashNotice tone="info" message={m.storyDraftDiscarded()} />
</Story>

{#snippet sideNav()}
  <ul class="grid gap-2 text-sm">
    <li><a href="#splitlayout">{m.storyGeneral()}</a></li>
    <li><a href="#splitlayout">{m.storyMedia()}</a></li>
    <li><a href="#splitlayout">{m.storyCredits()}</a></li>
  </ul>
{/snippet}

<Story
  name="SplitLayout"
  source="patterns/SplitLayout"
  description={m.storySplitlayoutDescription()}
>
  <Variant label={m.storyStackSidebarAtStart()}>
    <SplitLayout sidebarLabel={m.storySections()} sidebar={sideNav}>
      <p class="text-sm">{m.storyAFastFlush55Door()}</p>
    </SplitLayout>
  </Variant>
  <Variant label={m.storySheetEndSticky()}>
    <SplitLayout
      sidebarLabel={m.storyBuildFilters()}
      trigger={{ label: m.storyFiltersTwo() }}
      closeLabel={m.storyCloseFilters()}
      collapse="sheet"
      side="end"
      sticky
      sidebar={sideNav}
    >
      <p class="text-sm">{m.story312Builds()}</p>
    </SplitLayout>
  </Variant>
  <Variant label={m.storySheetInANarrowFrame()}>
    <div class="max-w-dialog-sm">
      <SplitLayout
        sidebarLabel={m.storyAccount()}
        closeLabel={m.storyCloseAccountNavigation()}
        collapse="sheet"
        sidebar={sideNav}
      >
        <p class="text-sm">{m.storyShowTheDoorClosedAndOpen()}</p>
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
