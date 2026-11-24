<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Tag from "@lucide/svelte/icons/tag";
  import Button from "@/ui/actions/Button.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import Announcer from "@/ui/feedback/Announcer.svelte";
  import { announce } from "@/ui/feedback/announcer.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Chip from "@/ui/feedback/Chip.svelte";
  import EmptyState from "@/ui/feedback/EmptyState.svelte";
  import ErrorState from "@/ui/feedback/ErrorState.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Progress from "@/ui/feedback/Progress.svelte";
  import SaveStatus, { type SaveState } from "@/ui/feedback/SaveStatus.svelte";
  import Skeleton from "@/ui/feedback/Skeleton.svelte";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const tones = ["neutral", "info", "success", "warning", "danger", "accent"] as const;
  let flush = $state(true);
  let chips = $state([m.storyWidth5(), m.storyFlush()]);
  let results = $state(312);
  const saveStates: SaveState[] = [
    "idle",
    "dirty",
    "saving",
    "saved",
    "offline",
    "error",
    "conflict",
  ];
  const saveLabels = {
    idle: m.storyAllChangesSaved(),
    dirty: m.storyUnsavedChanges(),
    saving: m.storySaving(),
    saved: m.storySaved(),
    offline: m.storyOfflineChangesAreKeptOnThisDevice(),
    error: m.storyCouldnTSave(),
    conflict: m.storyChangedElsewhereReviewBeforeSaving(),
    retry: m.storyRetry(),
  };
</script>

<Story name="Badge" source="feedback/Badge" description={m.storyBadgeDescription()}>
  <Variant label={m.storyTones()}>
    <div class="flex flex-wrap gap-2">
      {#each tones as tone (tone)}<Badge {tone}>{tone}</Badge>{/each}
    </div>
  </Variant>
  <Variant label={m.storyDotIconSizeMd()}>
    <div class="flex flex-wrap gap-2">
      <Badge tone="success" dot>{m.storyApproved()}</Badge>
      <Badge tone="warning" dot>{m.storyPendingLabel()}</Badge>
      <Badge size="md" tone="accent">{m.storyRecordHolder()}</Badge>
    </div>
  </Variant>
</Story>

<Story name="Chip" source="feedback/Chip" description={m.storyChipDescription()}>
  <Variant label={m.storyLinkToggleStatic()}>
    <div class="flex flex-wrap gap-2">
      <Chip href="#chip" icon={Tag}>{m.storySeamless()}</Chip>
      <Chip pressed={flush} onclick={() => (flush = !flush)}>{m.storyFlushLabel()}</Chip>
      <Chip>3×3</Chip>
      <Chip tone="accent">{m.storySelected()}</Chip>
    </div>
  </Variant>
  <Variant label={m.storyRemovableLinkAndButton()}>
    <div class="flex flex-wrap gap-2">
      <Chip removeLabel={m.storyRemoveVersionFilter()} removeHref="#chip">{m.storyJava121()}</Chip>
      {#each chips as chip (chip)}
        <Chip
          removeLabel={m.storyRemoveOption({ label: chip })}
          onRemove={() => (chips = chips.filter((item) => item !== chip))}>{chip}</Chip
        >
      {/each}
    </div>
  </Variant>
</Story>

<Story name="Notice" source="feedback/Notice" description={m.storyNoticeDescription()}>
  <Notice tone="info" title={m.storyNoMatchingBuilds()}
    >{m.storyTryABroaderSizeOrAnotherVersion()}</Notice
  >
  <Notice tone="success" title={m.storyDraftSaved()} role="status"
    >{m.storyYourChangesAreStored()}</Notice
  >
  <Notice tone="warning" title={m.storyCouldnTLoadBuilds()}>
    {m.storyFiltersPreserved()}
    {#snippet actions()}<Button size="sm">{m.storyRetry()}</Button>{/snippet}
  </Notice>
  <Notice tone="danger" title={m.storyUploadFailed()} role="alert"
    >{m.storyRetryWithTheSameFileOrChooseAnother()}</Notice
  >
  <Notice tone="warning" variant="banner">{m.storyThisBuildIsPendingReview()}</Notice>
</Story>

<Story name="Progress" source="feedback/Progress">
  <Progress label={m.storyUploadingDoorSchem()} value={40} valueLabel="40%" />
  <Progress label={m.storyRenderingPreview()} />
  <Progress
    label={m.storyVotes()}
    kind="meter"
    value={3}
    max={5}
    valueLabel={m.story3Of5Votes()}
    tone="success"
    thresholds={[{ value: 4, label: m.storyApproveAt4() }]}
  />
</Story>

<Story name="Spinner" source="feedback/Spinner" description={m.storySpinnerDescription()}>
  <p class="flex items-center gap-2 text-sm"><Spinner /> {m.storyCheckingTheRecord()}</p>
  <Spinner size="md" label={m.storyLoading()} />
</Story>

<Story name="Skeleton" source="feedback/Skeleton" description={m.storySkeletonDescription()}>
  <div
    class="grid max-w-96 gap-4"
    role="group"
    aria-busy="true"
    aria-label={m.storyLoadingBuilds()}
  >
    <Skeleton shape="heading" width="1/2" />
    <Skeleton lines={3} />
    <Skeleton shape="block" />
    <div class="flex items-center gap-3"><Skeleton shape="avatar" /><Skeleton width="1/3" /></div>
    <Skeleton shape="row" />
  </div>
</Story>

<Story name="EmptyState" source="feedback/EmptyState">
  <EmptyState title={m.storyNoDraftsYet()} description={m.storyStartASubmissionToSeeItHere()}>
    {#snippet actions()}<Button variant="primary">{m.submissionPageTitle()}</Button>{/snippet}
  </EmptyState>
  <EmptyState title={m.storyNoRecordsMatch()} size="page" />
</Story>

<Story name="ErrorState" source="feedback/ErrorState">
  <ErrorState
    title={m.storyCouldnTLoadSchematics()}
    description={m.storyTheCatalogueApiDidNotAnswerInTime()}
    retryLabel={m.storyTryAgain()}
    retryHref="#errorstate"
    reference={{ label: m.storyReference(), value: "ERR-7F3A" }}
  />
</Story>

<Story name="Announcer" source="feedback/Announcer" description={m.storyAnnouncerDescription()}>
  <div class="flex flex-wrap items-center gap-3">
    <Button
      onclick={() => {
        results = results === 312 ? 48 : 312;
        announce(m.storyResultCount({ results }));
      }}>{m.storyRefetchResults()}</Button
    >
    <p class="text-sm text-fg-muted" aria-hidden="true">
      {m.storyAnnouncementPreview({ message: m.storyResultCount({ results }) })}
    </p>
  </div>
  <Announcer />
</Story>

<Story name="SaveStatus" source="feedback/SaveStatus" description={m.storySaveStatusDescription()}>
  <Layout pattern="grid" min="card">
    {#each saveStates as saveState (saveState)}
      <Variant label={saveState}>
        <SaveStatus
          state={saveState}
          labels={saveLabels}
          savedAt={{ datetime: "2026-11-24T10:32:00Z", label: "10:32" }}
          onRetry={() => undefined}
        />
      </Variant>
    {/each}
  </Layout>
</Story>
