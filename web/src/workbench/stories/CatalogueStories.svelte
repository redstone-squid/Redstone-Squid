<script lang="ts">
  import * as m from "@/locales/messages-catalogue.gen";
  import { buildKindLabel, recordFactLabel } from "@/locales/vocabulary.gen";
  import BuildCard from "@/features/catalogue/BuildCard.svelte";
  import BuildDetail from "@/features/catalogue/BuildDetail.svelte";
  import BuildFacts from "@/features/catalogue/BuildFacts.svelte";
  import BuildMention from "@/features/catalogue/BuildMention.svelte";
  import BuildPreview from "@/features/catalogue/BuildPreview.svelte";
  import BuildRow from "@/features/catalogue/BuildRow.svelte";
  import BuildTable from "@/features/catalogue/BuildTable.svelte";
  import CreatorCredits from "@/features/catalogue/CreatorCredits.svelte";
  import FeaturedBuild from "@/features/catalogue/FeaturedBuild.svelte";
  import HolderTimeline from "@/features/catalogue/HolderTimeline.svelte";
  import { recordMeasurements } from "@/features/catalogue/presentation";
  import RecordCard from "@/features/catalogue/RecordCard.svelte";
  import RecordDetail from "@/features/catalogue/RecordDetail.svelte";
  import RecordList from "@/features/catalogue/RecordList.svelte";
  import RecordPrediction from "@/features/catalogue/RecordPrediction.svelte";
  import RecordRow from "@/features/catalogue/RecordRow.svelte";
  import RecordTable from "@/features/catalogue/RecordTable.svelte";
  import SchematicViewer from "@/features/catalogue/SchematicViewer.svelte";
  import SubmissionReview from "@/features/submissions/SubmissionReview.svelte";
  import List from "@/ui/data/List.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import {
    buildDetailScenarios,
    buildFixtures,
    buildScenarios,
    creatorFixtures,
    recordDetailScenarios,
    recordPerCriterion,
    recordScenarios,
    recordTableFixtures,
    unnamedHistory,
  } from "@/workbench/fixtures";
  import { predictionScenarios, submissionScenarios } from "@/workbench/submission-fixtures";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const builds = buildScenarios();
  const records = recordScenarios();
  const details = recordDetailScenarios();
  const buildPages = buildDetailScenarios();
  const creator = creatorFixtures();
  const submissions = submissionScenarios();
  const predictions = predictionScenarios();
  const holderHistory = details.single.history ?? [];
  const [fastest] = recordPerCriterion().slice(3);
  const smallestExtender = recordTableFixtures().find((record) => record.build_kind === "extender");
  // Whole pages sit in a frame, so their page headers read as part of the specimen.
  const pageFrame = "min-w-0 rounded-md border border-dashed border-line-strong p-4";
  const sortHref = (key: string, direction: string) =>
    `?sort=${direction === "desc" ? "-" : ""}${key}#buildtable`;
  const visibilityHref = (hidden: string[]) => `?hide=${hidden.join(",")}#buildtable`;
</script>

<Story
  name="BuildPreview"
  source="features/catalogue/BuildPreview.svelte"
  description={m.storyBuildpreviewDescription()}
>
  <Layout pattern="grid">
    <Variant label={m.storyWithImage()}><BuildPreview build={builds.standard} /></Variant>
    <Variant label={m.storyNoPreview()}><BuildPreview build={builds.noPreview} /></Variant>
    <Variant label={m.storyFailedToLoad()}>
      <BuildPreview build={builds.unavailableImage} />
    </Variant>
  </Layout>
  <Variant label={m.storyThumbnails()}>
    <Layout pattern="cluster">
      <BuildPreview build={builds.standard} thumbnail="square" class="size-16 rounded-xs" />
      <BuildPreview build={builds.noPreview} thumbnail="square" class="size-16 rounded-xs" />
      <BuildPreview build={builds.unavailableImage} thumbnail="square" class="size-10 rounded-xs" />
    </Layout>
  </Variant>
</Story>

<Story
  name="BuildMention"
  source="features/catalogue/BuildMention.svelte"
  description={m.storyBuildmentionDescription()}
>
  <Layout pattern="grid">
    <Variant label={m.storyInlineMention()}><BuildMention build={builds.standard} /></Variant>
    <Variant label={m.storyWithCreators()}>
      <BuildMention build={builds.standard} layout="block" />
    </Variant>
    <Variant label={m.storyNoPreview()}>
      <BuildMention build={builds.noPreview} layout="block" />
    </Variant>
    <Variant label={m.storyLongTitleManyCreators()}>
      <BuildMention build={builds.crowded} layout="block" />
    </Variant>
  </Layout>
</Story>

<Story
  name="BuildFacts"
  source="features/catalogue/BuildFacts.svelte"
  description={m.storyBuildfactsDescription()}
>
  <Layout pattern="grid">
    <Variant label={m.storyComplete()}><BuildFacts build={builds.standard} /></Variant>
    <Variant label={m.storyUnknownSizeAndTiming()}>
      <BuildFacts build={builds.unknownMeasurements} />
    </Variant>
    <Variant label={m.storyLongTitleManyCreators()}><BuildFacts build={builds.crowded} /></Variant>
  </Layout>
</Story>

<Story
  name="BuildCard"
  source="features/catalogue/BuildCard.svelte"
  description={m.storyBuildcardDescription()}
>
  <Layout pattern="grid">
    <Variant label={m.storyComplete()}>
      <BuildCard build={builds.standard} category={m.trialDoor()} />
    </Variant>
    <Variant label={m.storyNoPreview()}>
      <BuildCard build={builds.noPreview} category={m.trialDoor()} />
    </Variant>
    <Variant label={m.storyFailedToLoad()}>
      <BuildCard build={builds.unavailableImage} category={m.trialDoor()} />
    </Variant>
    <Variant label={m.storyUnknownSizeAndTiming()}>
      <BuildCard build={builds.unknownMeasurements} category={buildKindLabel("extender")} />
    </Variant>
    <Variant label={m.storyMissingFacts()}>
      <BuildCard
        build={builds.unknownMeasurements}
        category={buildKindLabel("extender")}
        missingFacts={[recordFactLabel("extension"), recordFactLabel("retraction")]}
      />
    </Variant>
    <Variant label={m.storyLongTitleManyCreators()}>
      <BuildCard build={builds.crowded} category={m.trialDoor()} />
    </Variant>
  </Layout>
  <Variant label={m.storyCompactGallery()}>
    <Layout as="ul" pattern="grid" min="tile">
      {#each [builds.standard, builds.crowded, builds.unknownMeasurements, builds.noPreview] as build (build.id)}
        <BuildCard {build} category={m.trialDoor()} as="li" compact />
      {/each}
    </Layout>
  </Variant>
</Story>

<Story
  name="BuildRow"
  source="features/catalogue/BuildRow.svelte"
  description={m.storyBuildrowDescription()}
>
  <Variant label={m.storyComfortable()}>
    <List>
      <BuildRow build={builds.standard} />
      <BuildRow build={builds.crowded} />
      <BuildRow build={builds.noPreview} />
    </List>
  </Variant>
  <Variant label={m.storyCompactRows()}>
    <List>
      <BuildRow build={builds.standard} density="compact" />
      <BuildRow build={builds.crowded} density="compact" />
    </List>
  </Variant>
  <Variant label={m.storyMissingFacts()}>
    <List>
      <BuildRow
        build={builds.unknownMeasurements}
        missingFacts={[recordFactLabel("extension"), recordFactLabel("retraction")]}
      />
    </List>
  </Variant>
</Story>

<Story
  name="BuildTable"
  source="features/catalogue/BuildTable.svelte"
  description={m.storyBuildtableDescription()}
>
  <Variant label={m.storySortedWithRecord()}>
    <BuildTable
      builds={[...buildFixtures(), builds.unknownMeasurements]}
      caption={m.storyDoorsCompeting()}
      sort={{ key: "opening", direction: "asc" }}
      {sortHref}
      {visibilityHref}
      hidden={["submitted"]}
      highlight={{ ids: [1], label: m.storyRecordBadge() }}
    />
  </Variant>
  <Variant label={m.storyEmpty()}>
    <BuildTable builds={[]} caption={m.storyDoorsCompeting()} />
  </Variant>
</Story>

<Story
  name="FeaturedBuild"
  source="features/catalogue/FeaturedBuild.svelte"
  description={m.storyFeaturedbuildDescription()}
>
  <Variant label={m.storyRecordHolder()}>
    <FeaturedBuild
      build={builds.standard}
      measurements={fastest === undefined ? [] : recordMeasurements(fastest)}
      since="2026-01-12T00:00:00Z"
    />
  </Variant>
  <Variant label={m.storyNoPreview()}>
    <FeaturedBuild
      build={builds.noPreview}
      measurements={recordMeasurements(smallestExtender ?? records.single)}
    />
  </Variant>
  <Variant label={m.storyWithEyebrow()}>
    <FeaturedBuild build={builds.crowded} eyebrow={m.storyBuildOfTheWeek()} />
  </Variant>
</Story>

<Story
  name="BuildDetail"
  source="features/catalogue/BuildDetail.svelte"
  description={m.storyBuilddetailDescription()}
>
  <Variant label={m.storyDoorWithEverything()}>
    <div class={pageFrame}><BuildDetail build={buildPages.door} /></div>
  </Variant>
  <Variant label={m.storyPendingExtender()}>
    <div class={pageFrame}><BuildDetail build={buildPages.extender} /></div>
  </Variant>
</Story>

<Story
  name="RecordCard"
  source="features/catalogue/RecordCard.svelte"
  description={m.storyRecordcardDescription()}
>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storySingleHolder()}><RecordCard record={records.single} /></Variant>
    <Variant label={m.storyNoSubtitle()}><RecordCard record={records.noSubtitle} /></Variant>
    <Variant label={m.storyShared()}><RecordCard record={records.shared} /></Variant>
    <Variant label={m.storyTied()}><RecordCard record={records.tied} /></Variant>
    <Variant label={m.storyCurrentVersionOnly()}>
      <RecordCard record={records.currentVersion} />
    </Variant>
    <Variant label={m.storyRefreshPending()}><RecordCard record={records.refreshPending} /></Variant
    >
    <Variant label={m.storyNoHolders()}><RecordCard record={records.noHolder} /></Variant>
    <Variant label={m.storyEverythingAtOnce()}><RecordCard record={records.everything} /></Variant>
  </Layout>
  <Variant label={m.storyOnePerCriterion()}>
    <Layout as="ul" pattern="grid" min="card-wide">
      {#each recordPerCriterion() as record (record.record_id)}
        <RecordCard {record} as="li" />
      {/each}
    </Layout>
  </Variant>
</Story>

<Story
  name="RecordRow"
  source="features/catalogue/RecordRow.svelte"
  description={m.storyRecordrowDescription()}
>
  <Variant label={m.storyFromARecordSummary()}>
    <List>
      <RecordRow record={records.single} />
      <RecordRow record={records.shared} />
    </List>
  </Variant>
  <Variant label={m.storyOnABuildPage()}>
    <List>
      {#each buildPages.door.records_held ?? [] as record (record.record_id)}
        <RecordRow {record} />
      {/each}
      {#each buildPages.door.records_former ?? [] as record (record.record_id)}
        <RecordRow {record} former />
      {/each}
      <RecordRow
        record={{
          record_id: "retired",
          title: m.storyFirstIris(),
          since: "2025-03-01T00:00:00Z",
          retired: true,
        }}
      />
    </List>
  </Variant>
  <Variant label={m.storyTitleOnly()}>
    <List>
      {#each creator.stats.records_held as record (record.id)}
        <RecordRow record={{ record_id: record.id, title: record.title }} />
      {/each}
    </List>
  </Variant>
</Story>

<Story
  name="RecordTable"
  source="features/catalogue/RecordTable.svelte"
  description={m.storyRecordtableDescription()}
>
  <RecordTable records={recordTableFixtures()} caption={m.recordsTitle()} />
</Story>

<Story
  name="RecordList"
  source="features/catalogue/RecordList.svelte"
  description={m.storyRecordlistDescription()}
>
  <Variant label={m.storyWithAdjacentPages()}>
    <RecordList
      records={Object.values(records).slice(0, 3)}
      prevHref="#recordlist"
      nextHref="#recordlist"
    />
  </Variant>
  <Variant label={m.storyAsATable()}>
    <RecordList
      records={recordTableFixtures()}
      view="table"
      prevHref={null}
      nextHref="#recordlist"
    />
  </Variant>
  <Variant label={m.storyEmpty()}>
    <RecordList records={[]} prevHref={null} nextHref={null} />
  </Variant>
</Story>

<Story
  name="HolderTimeline"
  source="features/catalogue/HolderTimeline.svelte"
  description={m.storyHoldertimelineDescription()}
>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyNamedHolders()}>
      <HolderTimeline history={holderHistory} criterion="fastest" label={m.recordHistory()} />
    </Variant>
    <Variant label={m.storyBuildNumbersOnly()}>
      <HolderTimeline history={unnamedHistory()} criterion="fastest" label={m.recordHistory()} />
    </Variant>
  </Layout>
</Story>

<Story
  name="RecordDetail"
  source="features/catalogue/RecordDetail.svelte"
  description={m.storyRecorddetailDescription()}
>
  <Variant label={m.storyOneHolderWithHistory()}>
    <div class={pageFrame}><RecordDetail record={details.single} /></div>
  </Variant>
  <Variant label={m.storySharedRecord()}>
    <div class={pageFrame}><RecordDetail record={details.shared} /></div>
  </Variant>
  <Variant label={m.storySharedDatesMissing()}>
    <div class={pageFrame}><RecordDetail record={details.sharedUndated} /></div>
  </Variant>
  <Variant label={m.storyCouldChange()}>
    <div class={pageFrame}><RecordDetail record={details.changeable} /></div>
  </Variant>
  <Variant label={m.storyRetired()}>
    <div class={pageFrame}><RecordDetail record={details.retired} /></div>
  </Variant>
  <Variant label={m.storyNoHolders()}>
    <div class={pageFrame}><RecordDetail record={details.noHolders} /></div>
  </Variant>
</Story>

<Story
  name="RecordPrediction"
  source="features/catalogue/RecordPrediction.svelte"
  description={m.storyRecordpredictionDescription()}
>
  <Variant label={m.storyEveryOutcome()}>
    <div class={pageFrame}><RecordPrediction preview={predictions.outcomes} /></div>
  </Variant>
  <Variant label={m.storyFactsStillMissing()}>
    <div class={pageFrame}><RecordPrediction preview={predictions.missingFacts} /></div>
  </Variant>
  <Variant label={m.storyNoCategories()}>
    <div class={pageFrame}><RecordPrediction preview={predictions.empty} /></div>
  </Variant>
</Story>

<Story
  name="CreatorCredits"
  source="features/catalogue/CreatorCredits.svelte"
  description={m.storyCreatorcreditsDescription()}
>
  <Variant label={m.storyBuildsAndRecords()}>
    <div class={pageFrame}>
      <CreatorCredits
        stats={creator.stats}
        builds={creator.builds}
        prevHref={null}
        nextHref="#creatorcredits"
      />
    </div>
  </Variant>
  <Variant label={m.storyClaimingCredits()}>
    <div class={pageFrame}>
      <CreatorCredits
        stats={creator.stats}
        builds={creator.builds}
        claimNameId={creator.claimNameId}
        prevHref={null}
        nextHref={null}
      />
    </div>
  </Variant>
  <Variant label={m.storyNewCreator()}>
    <div class={pageFrame}>
      <CreatorCredits stats={creator.newcomer} builds={[]} prevHref={null} nextHref={null} />
    </div>
  </Variant>
</Story>

<Story
  name="SubmissionReview"
  source="features/submissions/SubmissionReview.svelte"
  description={m.storySubmissionreviewDescription()}
>
  {#each [[m.storyReviewerVoteOpen(), submissions.reviewer], [m.storyVoterWhoHasVoted(), submissions.voted], [m.storySubmittersView(), submissions.submitter], [m.storyApprovedAndPublished(), submissions.published], [m.storyRejectedWithFeedback(), submissions.rejected]] as const as [label, scenario] (label)}
    <Variant {label}>
      <div class={pageFrame}>
        <SubmissionReview
          title={scenario.detail.submission.title ?? ""}
          detail={scenario.detail}
          vote={scenario.vote}
          previewHref="#recordprediction"
        />
      </div>
    </Variant>
  {/each}
</Story>

<Story
  name="Schematic viewer"
  heading={m.storySchematicViewer()}
  source="features/catalogue/SchematicViewer.svelte"
  description={m.storySchematicViewerDescription()}
>
  <SchematicViewer
    title={m.storyCopperBolt55()}
    schematicUrl="/fixtures/piston-door.schem"
    previewUrl="/fixtures/copper-door.svg"
    resourcePackUrl="/v1/schematic-resource-pack"
  />
</Story>
