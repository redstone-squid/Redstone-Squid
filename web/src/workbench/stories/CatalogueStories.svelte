<script lang="ts">
  import * as m from "@/locales/messages-catalogue.gen";
  import { recordFactLabel } from "@/locales/vocabulary.gen";
  import BuildCard from "@/features/catalogue/BuildCard.svelte";
  import BuildFacts from "@/features/catalogue/BuildFacts.svelte";
  import BuildListItem from "@/features/catalogue/BuildListItem.svelte";
  import BuildPreview from "@/features/catalogue/BuildPreview.svelte";
  import CreatorCredits from "@/features/catalogue/CreatorCredits.svelte";
  import FeaturedBuild from "@/features/catalogue/FeaturedBuild.svelte";
  import RecordCard from "@/features/catalogue/RecordCard.svelte";
  import RecordDetail from "@/features/catalogue/RecordDetail.svelte";
  import RecordList from "@/features/catalogue/RecordList.svelte";
  import SchematicViewer from "@/features/catalogue/SchematicViewer.svelte";
  import RecordPrediction from "@/features/submissions/RecordPrediction.svelte";
  import SubmissionReview from "@/features/submissions/SubmissionReview.svelte";
  import List from "@/ui/data/List.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import {
    buildScenarios,
    creatorFixtures,
    recordDetailScenarios,
    recordPerCriterion,
    recordScenarios,
  } from "@/workbench/fixtures";
  import { predictionScenarios, submissionScenarios } from "@/workbench/submission-fixtures";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const builds = buildScenarios();
  const records = recordScenarios();
  const details = recordDetailScenarios();
  const creator = creatorFixtures();
  const submissions = submissionScenarios();
  const predictions = predictionScenarios();
  // Whole pages sit in a frame, so their page headers read as part of the specimen.
  const pageFrame = "min-w-0 rounded-md border border-dashed border-line-strong p-4";
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
      <BuildCard build={builds.unknownMeasurements} category={m.trialDoor()} />
    </Variant>
    <Variant label={m.storyMissingFacts()}>
      <BuildCard
        build={builds.unknownMeasurements}
        category={m.trialDoor()}
        missingFacts={[recordFactLabel("opening"), recordFactLabel("closing")]}
      />
    </Variant>
    <Variant label={m.storyLongTitleManyCreators()}>
      <BuildCard build={builds.crowded} category={m.trialDoor()} />
    </Variant>
  </Layout>
</Story>

<Story
  name="BuildListItem"
  source="features/catalogue/BuildListItem.svelte"
  description={m.storyBuildlistitemDescription()}
>
  <Variant label={m.storyComplete()}>
    <List><BuildListItem build={builds.standard} /></List>
  </Variant>
  <Variant label={m.storyLongTitleManyCreators()}>
    <List><BuildListItem build={builds.crowded} /></List>
  </Variant>
</Story>

<Story
  name="FeaturedBuild"
  source="features/catalogue/FeaturedBuild.svelte"
  description={m.storyFeaturedbuildDescription()}
>
  <Variant label={m.storyWithImage()}><FeaturedBuild build={builds.standard} /></Variant>
  <Variant label={m.storyNoPreview()}><FeaturedBuild build={builds.noPreview} /></Variant>
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
  <Variant label={m.storyLastPage()}>
    <RecordList records={[records.single]} prevHref="#recordlist" nextHref={null} />
  </Variant>
  <Variant label={m.storyEmpty()}>
    <RecordList records={[]} prevHref={null} nextHref={null} />
  </Variant>
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
  name="RecordPrediction"
  source="features/submissions/RecordPrediction.svelte"
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
