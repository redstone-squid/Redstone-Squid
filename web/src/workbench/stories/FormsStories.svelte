<script lang="ts">
  import Box from "@lucide/svelte/icons/box";
  import DoorOpen from "@lucide/svelte/icons/door-open";
  import Search from "@lucide/svelte/icons/search";
  import Zap from "@lucide/svelte/icons/zap";
  import * as m from "@/locales/messages-forms.gen";
  import Button from "@/ui/actions/Button.svelte";
  import Autocomplete from "@/ui/forms/Autocomplete.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import ChoiceGroup from "@/ui/forms/ChoiceGroup.svelte";
  import CodeEntry from "@/ui/forms/CodeEntry.svelte";
  import Combobox from "@/ui/forms/Combobox.svelte";
  import DateField from "@/ui/forms/DateField.svelte";
  import DateRangeField from "@/ui/forms/DateRangeField.svelte";
  import ErrorSummary from "@/ui/forms/ErrorSummary.svelte";
  import FieldGroup from "@/ui/forms/FieldGroup.svelte";
  import FileDrop from "@/ui/forms/FileDrop.svelte";
  import FormActions from "@/ui/forms/FormActions.svelte";
  import ListField from "@/ui/forms/ListField.svelte";
  import RangeField from "@/ui/forms/RangeField.svelte";
  import SelectField from "@/ui/forms/SelectField.svelte";
  import Switch from "@/ui/forms/Switch.svelte";
  import TextareaField from "@/ui/forms/TextareaField.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import TimeField from "@/ui/forms/TimeField.svelte";
  import UnitField from "@/ui/forms/UnitField.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import { versionOptions } from "@/workbench/draft-fixture";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const versions = versionOptions.map((version) => ({ value: version, label: version }));
  const tags = [
    { value: "flush", label: m.storyFlush() },
    { value: "seamless", label: m.storySeamless() },
    { value: "tileable", label: m.storyTileable() },
    { value: "silent", label: m.storySilent() },
    { value: "compact", label: m.storyCompactTag() },
    { value: "survival friendly", label: m.storySurvivalFriendly() },
  ];
  const comboboxLabels = {
    toggle: m.storyShowOptions(),
    noResults: m.storyNoMatches(),
    loading: m.storyLoading(),
    error: m.storyOptionsAreUnavailable(),
    remove: (label: string) => m.storyRemoveOption({ label }),
    create: (text: string) => m.storyAddOption({ text }),
  };
  const dateLabels = {
    segments: {
      year: m.storyDateYear(),
      month: m.storyDateMonth(),
      day: m.storyDateDay(),
      hour: m.storyDateHour(),
      minute: m.storyDateMinute(),
      dayPeriod: m.storyDateDayPeriod(),
      empty: m.storyDateEmpty(),
    },
    calendar: m.storyChooseDate(),
    previousMonth: m.storyPreviousMonth(),
    nextMonth: m.storyNextMonth(),
  };
  const delayUnits = [
    { id: "gt", label: m.storyGameTicks(), factor: 1 },
    { id: "rt", label: m.storyRedstoneTicks(), factor: 2 },
    { id: "s", label: "s", factor: 20 },
  ];

  let note = $state(m.storyOpensIn06SWithNoVisible());
  let notifications = $state(true);
  let syncing = $state(false);
  let edition = $state<string | string[]>("java");
  let features = $state<string | string[]>(["flush"]);
  let category = $state<string | string[]>("door");
  let delay = $state<number | null>(25);
  let code = $state("");
  let version = $state<string | string[]>(versionOptions[0]);
  let chosenTags = $state<string | string[]>(["flush", "seamless"]);
  let links = $state([{ url: "https://youtube.com/@circuitsage" }]);
  let files = $state<string[]>([]);
  let rejected = $state<string[]>([]);
  let width = $state({ name: "width-min", value: "3", label: m.storyMinimumWidth() });
  let widthMax = $state({ name: "width-max", value: "", label: m.storyMaximumWidth() });
  let builtOn = $state("2025-06-14");
  let closes = $state("2026-12-01T18:00");
  let starts = $state("19:30");
  let submittedFrom = $state({ name: "submitted-from", value: "2026-01-01", label: m.storyFrom() });
  let submittedUntil = $state({ name: "submitted-until", value: "", label: m.storyUntil() });
  let badMin = $state({ name: "bad-min", value: "9", label: m.storyMinimumHeight() });
  let badMax = $state({
    name: "bad-max",
    value: "4",
    label: m.storyMaximumHeight(),
    error: m.storyUseAMaximumOfAtLeast9(),
  });
  const problems = () => [
    { fieldId: "story-summary-title", message: m.workbenchEnterATitleForYourBuild() },
    { fieldId: "story-summary-width", message: m.storyEnterTheOpeningWidth() },
  ];
  let summaryErrors = $state(problems());
  let submitted = $state(false);

  function startSync(): void {
    syncing = true;
    window.setTimeout(() => (syncing = false), 1200);
  }
</script>

<Story name="TextField" source="forms/TextField" description={m.storyTextFieldDescription()}>
  <Layout pattern="grid" min="card">
    <Variant label={m.storyHintAndPlaceholder()}>
      <TextField
        label={m.storyCreator()}
        hint={m.storyPublicNameShownOnTheBuild()}
        placeholder="CircuitSage"
      />
    </Variant>
    <Variant label={m.storyOptional()}>
      <TextField label={m.storyVideoLink()} detail={m.storyOptional()} type="url" />
    </Variant>
    <Variant label={m.storyError()}>
      <TextField
        label={m.workbenchBuildTitle()}
        error={m.workbenchEnterATitleForYourBuild()}
        required
      />
    </Variant>
    <Variant label={m.storyNumberWithUnitNarrow()}>
      <TextField
        label={m.trialWidth()}
        inputmode="numeric"
        value="5"
        suffix={m.storyBlocks()}
        width="sm"
      />
    </Variant>
    <Variant label={m.storyPrefix()}>
      <TextField label={m.storyChannel()} prefix="#" value="door-records" width="md" />
    </Variant>
    <Variant label={m.storyIconAndClearButton()}>
      <TextField
        label={m.storySearchBuilds()}
        type="search"
        icon={Search}
        value={m.storyPistonDoor()}
        clearable
        clearLabel={m.storyClearSearch()}
      />
    </Variant>
    <Variant label={m.storyCounter()}>
      <TextField
        label={m.storyShortTitle()}
        maxlength={24}
        counter
        value={m.storySeamless33FlushDoor()}
      />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <TextField label={m.storyBuildId()} value="b_01HZ8Q" disabled />
    </Variant>
    <Variant label={m.storyReadOnly()}>
      <TextField label={m.storySubmittedBy()} value="CircuitSage" readonly />
    </Variant>
    <Variant label={m.storyHiddenLabelToolbar()}>
      <TextField
        label={m.storyFilterTags()}
        labelVisibility="hidden"
        placeholder={m.storyFilterTags()}
      />
    </Variant>
  </Layout>
</Story>

<Story name="TextareaField" source="forms/TextareaField">
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyCounterAndAutosize()}>
      <TextareaField
        label={m.storyBuilderNotes()}
        hint={m.storyDescribeTheOpeningSequenceOrTiming()}
        maxlength={200}
        counter
        autosize
        bind:value={note}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <TextareaField label={m.storyChangeSummary()} error={m.storySayWhatChangedForReviewers()} />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <TextareaField label={m.storyModeratorNotes()} value={m.storyLockedWhileVoting()} disabled />
    </Variant>
  </Layout>
</Story>

<Story name="SelectField" source="forms/SelectField" description={m.storySelectFieldDescription()}>
  <Layout pattern="grid" min="card">
    <Variant label={m.storyOptionsAndPlaceholder()}>
      <SelectField
        label={m.trialCategory()}
        hint={m.storyChooseTheClosestCategory()}
        placeholder={m.storyChooseACategory()}
        options={[
          { value: "door", label: m.trialDoor() },
          { value: "utility", label: m.trialUtility() },
          { value: "farm", label: m.storyFarm(), disabled: true },
        ]}
      />
    </Variant>
    <Variant label={m.storyOptionChildrenNarrow()}>
      <SelectField label={m.storySort()} width="sm" value="new">
        <option value="new">{m.storyNewest()}</option>
        <option value="fast">{m.storyFastest()}</option>
      </SelectField>
    </Variant>
    <Variant label={m.storyError()}>
      <SelectField
        label={m.storyEdition()}
        placeholder={m.storyChooseAnEdition()}
        error={m.storyChooseTheEditionYouTestedOn()}
        options={[{ value: "java", label: m.storyJava() }]}
      />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <SelectField
        label={m.storyServer()}
        disabled
        options={[{ value: "main", label: m.storyMain() }]}
      />
    </Variant>
  </Layout>
</Story>

<Story name="CheckField" source="forms/CheckField" description={m.storyCheckFieldDescription()}>
  <Layout pattern="grid" min="card">
    <Variant label={m.storyChecked()}>
      <CheckField label={m.storyIncludeBuildDimensions()} checked />
    </Variant>
    <Variant label={m.storyHint()}>
      <CheckField label={m.storyHideMyProfile()} hint={m.storyOnlyYourBuildsStayPublic()} />
    </Variant>
    <Variant label={m.storyError()}>
      <CheckField
        label={m.storyIAgreeToTheSubmissionRules()}
        error={m.storyAgreeToTheRulesToSubmit()}
      />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <CheckField label={m.storyVerifiedByStaff()} checked disabled />
    </Variant>
  </Layout>
</Story>

<Story name="Switch" source="forms/Switch" description={m.storySwitchDescription()}>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyOff()}>
      <Switch label={m.storyShowMyIdentity()} hint={m.storyYourDiscordNameAppearsOnCredits()} />
    </Variant>
    <Variant label={m.storyOnSavingAfterAClick()}>
      <Switch
        label={m.storyRecordNotifications()}
        bind:checked={notifications}
        pending={syncing}
        onchange={startSync}
      />
    </Variant>
    <Variant label={m.storyPending()}>
      <Switch label={m.storyHideMyProfile()} checked pending />
    </Variant>
    <Variant label={m.storyError()}>
      <Switch label={m.storyEmailDigests()} error={m.storyAddAnEmailAddressFirst()} />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <Switch label={m.storyStaffMode()} checked disabled />
    </Variant>
  </Layout>
</Story>

<Story name="ChoiceGroup" source="forms/ChoiceGroup" description={m.storyChoiceGroupDescription()}>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyRadioListWithDescriptions()}>
      <ChoiceGroup
        label={m.storyMinecraftEdition()}
        name="story-edition"
        bind:value={edition}
        options={[
          {
            value: "java",
            label: m.storyJava(),
            description: m.storyTestedOnAJavaServerOrClient(),
          },
          {
            value: "bedrock",
            label: m.storyBedrock(),
            description: m.storyTestedOnBedrockEdition(),
          },
          { value: "both", label: m.storyBoth(), disabled: true },
        ]}
      />
    </Variant>
    <Variant label={m.storyCheckboxesInline()}>
      <ChoiceGroup
        type="checkbox"
        variant="inline"
        label={m.storyFeatures()}
        detail={m.storyOptional()}
        name="story-features"
        bind:value={features}
        options={[
          { value: "flush", label: m.storyFlushLabel() },
          { value: "seamless", label: m.storySeamless() },
          { value: "silent", label: m.storySilent() },
        ]}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <ChoiceGroup
        label={m.storyDirection()}
        name="story-direction"
        error={m.storyChooseWhichWayTheDoorOpens()}
        options={[
          { value: "in", label: m.storyInward() },
          { value: "out", label: m.storyOutward() },
        ]}
      />
    </Variant>
  </Layout>
  <Variant label={m.storyCardsInThreeColumns()}>
    <ChoiceGroup
      variant="cards"
      columns={3}
      label={m.storyWhatAreYouSubmitting()}
      name="story-category"
      bind:value={category}
      options={[
        {
          value: "door",
          label: m.storyDoor(),
          description: m.storyPistonDoorsOfAnySize(),
          icon: DoorOpen,
        },
        {
          value: "utility",
          label: m.trialUtility(),
          description: m.storyMachinesAndFarms(),
          icon: Zap,
        },
        { value: "other", label: m.storyOther(), description: m.storyAnythingElse(), icon: Box },
      ]}
    />
  </Variant>
</Story>

<Story name="FieldGroup" source="forms/FieldGroup" description={m.storyFieldGroupDescription()}>
  <Layout pattern="stack" gap="lg">
    <Variant label={m.storyRow()}>
      <FieldGroup
        label={m.storyDoorSize()}
        hint={m.storyInBlocksMeasuredFromTheOpening()}
        layout="row"
      >
        <TextField label={m.storyWidth()} inputmode="numeric" value="3" width="xs" />
        <TextField label={m.storyHeight()} inputmode="numeric" value="3" width="xs" />
        <TextField
          label={m.storyDepth()}
          detail={m.storyOptional()}
          inputmode="numeric"
          width="xs"
        />
      </FieldGroup>
    </Variant>
    <Variant label={m.storyError()}>
      <FieldGroup label={m.storyContact()} error={m.storyGiveAtLeastOneWayToReachYou()}>
        <TextField label="Discord" />
        <TextField label={m.storyEmail()} type="email" />
      </FieldGroup>
    </Variant>
  </Layout>
</Story>

<Story name="RangeField" source="forms/RangeField" description={m.storyRangeFieldDescription()}>
  <Layout pattern="stack" gap="lg">
    <Variant label={m.storyWithUnit()}>
      <RangeField
        label={m.storyWidth()}
        hint={m.storyLeaveEitherEndEmptyForNoLimit()}
        separator={m.storyTo()}
        unit={m.storyBlocks()}
        bind:min={width}
        bind:max={widthMax}
      />
    </Variant>
    <Variant label={m.storyErrorOnOneBound()}>
      <RangeField
        label={m.storyHeight()}
        separator={m.storyTo()}
        bind:min={badMin}
        bind:max={badMax}
      />
    </Variant>
  </Layout>
</Story>

<Story name="DateField" source="forms/DateField" description={m.storyDateFieldDescription()}>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyDateOnly()}>
      <DateField
        label={m.storyBuiltOn()}
        name="story-built-on"
        hint={m.storyTheDayTheBuildFirstWorked()}
        max="2026-11-26"
        bind:value={builtOn}
        labels={dateLabels}
      />
    </Variant>
    <Variant label={m.storyWithTimeAndZone()}>
      <DateField
        label={m.storyVotingCloses()}
        name="story-voting-closes"
        time
        timeZone="Europe/Berlin"
        bind:value={closes}
        labels={dateLabels}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <DateField
        label={m.storyBuiltOn()}
        name="story-built-on-error"
        value="2030-01-01"
        error={m.storyEnterADateThatHasPassed()}
        labels={dateLabels}
      />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <DateField
        label={m.storyBuiltOn()}
        name="story-built-on-disabled"
        value="2025-06-14"
        disabled
        labels={dateLabels}
      />
    </Variant>
  </Layout>
</Story>

<Story name="TimeField" source="forms/TimeField" description={m.storyTimeFieldDescription()}>
  <TimeField
    label={m.storyEventStarts()}
    name="story-event-starts"
    bind:value={starts}
    labels={dateLabels}
  />
</Story>

<Story
  name="DateRangeField"
  source="forms/DateRangeField"
  description={m.storyDateRangeFieldDescription()}
>
  <DateRangeField
    label={m.storySubmittedBetween()}
    hint={m.storyLeaveEitherEndEmptyForNoLimit()}
    separator={m.storyTo()}
    bind:start={submittedFrom}
    bind:end={submittedUntil}
    labels={dateLabels}
  />
</Story>

<Story name="UnitField" source="forms/UnitField" description={m.storyUnitFieldDescription()}>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyConverted()}>
      <UnitField
        label={m.storyOpeningTime()}
        hint={m.storyHowLongTheDoorTakesToOpen()}
        name="story-delay"
        unitName="story-delay-unit"
        units={delayUnits}
        labels={{ unit: m.storyUnit() }}
        bind:value={delay}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <UnitField
        label={m.storyClosingTime()}
        name="story-close"
        unitName="story-close-unit"
        units={delayUnits}
        labels={{ unit: m.storyUnit() }}
        value={null}
        error={m.storyEnterATime()}
      />
    </Variant>
  </Layout>
</Story>

<Story name="CodeEntry" source="forms/CodeEntry" description={m.storyCodeEntryDescription()}>
  <Layout pattern="grid" min="card">
    <Variant label={m.storyGroupedLettersAndDigits()}>
      <CodeEntry
        label={m.storyMergeCode()}
        hint={m.storyShownOnYourOtherAccount()}
        name="story-merge"
        length={8}
        groups={[4, 4]}
        bind:value={code}
      />
    </Variant>
    <Variant label={m.storyDigits()}>
      <CodeEntry label={m.storySignInCode()} name="story-login" length={6} charset="digits" />
    </Variant>
    <Variant label={m.storyError()}>
      <CodeEntry
        label={m.storyLinkCode()}
        name="story-link"
        length={8}
        groups={[4, 4]}
        value="K7Q2"
        error={m.storyThisCodeHasExpiredAskForANew()}
      />
    </Variant>
  </Layout>
</Story>

<Story
  name="ErrorSummary"
  source="forms/ErrorSummary"
  description={m.storyErrorSummaryDescription()}
>
  <form
    class="grid max-w-form gap-4"
    novalidate
    onsubmit={(event) => {
      event.preventDefault();
      submitted = true;
      summaryErrors = problems();
    }}
  >
    <!-- Focus moves only after a submit, so loading the workbench doesn't jump here. -->
    <ErrorSummary
      title={m.storyFix2ProblemsToSubmit()}
      errors={summaryErrors}
      focusOnMount={submitted}
    />
    <TextField
      id="story-summary-title"
      label={m.workbenchBuildTitle()}
      error={m.workbenchEnterATitleForYourBuild()}
    />
    <TextField
      id="story-summary-width"
      label={m.trialWidth()}
      inputmode="numeric"
      width="xs"
      error={m.storyEnterTheOpeningWidth()}
    />
    <div><Button type="submit" variant="primary">{m.storySubmitForReview()}</Button></div>
  </form>
</Story>

<Story name="FormActions" source="forms/FormActions" description={m.storyFormActionsDescription()}>
  <Variant label={m.storyPrimarySecondaryDestructiveAndNote()}>
    <FormActions note={m.storyPublishingIsFinalYouCanStillEditThe()}>
      {#snippet destructive()}<Button variant="quiet">{m.storyDiscardDraft()}</Button>{/snippet}
      {#snippet secondary()}<Button>{m.workbenchSaveDraft()}</Button>{/snippet}
      {#snippet primary()}<Button variant="primary">{m.storyPublish()}</Button>{/snippet}
    </FormActions>
  </Variant>
  <Variant label={m.storySticky()}>
    <div class="grid max-h-48 gap-4 overflow-y-auto rounded-sm border border-line px-4 pt-4">
      <TextField label={m.storyTitle()} />
      <TextareaField label={m.storyDescription()} />
      <FormActions sticky>
        {#snippet primary()}<Button variant="primary">{m.storySave()}</Button>{/snippet}
      </FormActions>
    </div>
  </Variant>
</Story>

<Story name="FileDrop" source="forms/FileDrop" description={m.storyFileDropDescription()}>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storyMultipleSizeLimit()}>
      <FileDrop
        label={m.storySchematics()}
        hint={m.storyLitematicOrSchemFilesUpTo1Mb()}
        accept=".litematic,.schem"
        multiple
        maxBytes={1_000_000}
        labels={{ drop: m.storyDropFilesHereOr(), browse: m.storyChooseFiles() }}
        onFiles={(accepted, skipped) => {
          files = accepted.map((file) => file.name);
          rejected = skipped.map((file) => file.name);
        }}
      />
      {#if files.length > 0}<p class="text-sm">
          {m.storyChosenFiles({ files: files.join(", ") })}
        </p>{/if}
      {#if rejected.length > 0}<p class="text-sm">
          {m.storySkippedFiles({ files: rejected.join(", ") })}
        </p>{/if}
    </Variant>
    <Variant label={m.storyError()}>
      <FileDrop
        label={m.storyCoverImage()}
        accept="image/*"
        error={m.storyTheImageCouldNotBeReadChooseAnother()}
        labels={{ drop: m.storyDropAnImageHereOr(), browse: m.storyChooseImage() }}
        onFiles={() => undefined}
      />
    </Variant>
    <Variant label={m.storyDisabled()}>
      <FileDrop
        label={m.storyVideo()}
        disabled
        hint={m.storyUploadsOpenOnceTheDraftHasATitle()}
        labels={{ drop: m.storyDropAVideoHereOr(), browse: m.storyChooseVideo() }}
        onFiles={() => undefined}
      />
    </Variant>
  </Layout>
</Story>

<Story
  name="Autocomplete"
  source="forms/Autocomplete"
  description={m.storyAutocompleteDescription()}
>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storySuggestionsWithDescriptions()}>
      <Autocomplete
        name="story-version"
        label={m.workbenchMinecraftVersion()}
        hint={m.storyTypeToSeeVersions()}
        minQueryLength={0}
        labels={{
          suggestions: m.searchSuggestions(),
          loading: m.storyLoading(),
          error: m.searchSuggestionError(),
          noResults: m.storyNoMatchingVersions(),
          count: (count) => m.storySuggestionCount({ count }),
        }}
        load={(query) =>
          Promise.resolve({
            items: versionOptions
              .filter((value) => value.toLowerCase().includes(query.trim().toLowerCase()))
              .map((value) => ({ value, label: value, description: m.storyRelease() })),
          })}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <Autocomplete
        name="story-credit"
        label={m.storyCreditedBuilder()}
        error={m.storyEnterABuilderName()}
        labels={{
          suggestions: m.storyBuilders(),
          loading: m.storyLoading(),
          error: m.storySuggestionsAreUnavailable(),
          noResults: m.storyNoBuildersFound(),
          count: (count) => m.storySuggestionCount({ count }),
        }}
        load={() => Promise.resolve({ items: [] })}
      />
    </Variant>
  </Layout>
</Story>

<Story name="Combobox" source="forms/Combobox" description={m.storyComboboxDescription()}>
  <Layout pattern="grid" min="card-wide">
    <Variant label={m.storySingle()}>
      <Combobox
        label={m.storyGameVersion()}
        name="story-combobox-version"
        options={versions}
        bind:value={version}
        labels={comboboxLabels}
      />
    </Variant>
    <Variant label={m.storyMultipleCanAddNew()}>
      <Combobox
        label={m.storyTags()}
        name="story-combobox-tags"
        hint={m.storyPickTagsOrAddYourOwn()}
        options={tags}
        multiple
        allowCreate
        bind:value={chosenTags}
        labels={comboboxLabels}
      />
    </Variant>
    <Variant label={m.storyLoadedFromTheServer()}>
      <Combobox
        label={m.storyBuildToMatch()}
        name="story-combobox-build"
        placeholder={m.storySearchBuilds()}
        labels={comboboxLabels}
        load={(query) =>
          Promise.resolve(
            [m.storyCopperBolt(), "Seamless Vault", "Tiny Flush"]
              .filter((title) => title.toLowerCase().includes(query.toLowerCase()))
              .map((title) => ({ value: title, label: title, description: m.story33Door() })),
          )}
      />
    </Variant>
    <Variant label={m.storyError()}>
      <Combobox
        label={m.storyChannel()}
        name="story-combobox-channel"
        options={[{ value: "records", label: "#records" }]}
        error={m.storyChooseAChannel()}
        labels={comboboxLabels}
      />
    </Variant>
  </Layout>
</Story>

<Story name="ListField" source="forms/ListField" description={m.storyListFieldDescription()}>
  <div class="max-w-form">
    <ListField
      label={m.storyProfileLinks()}
      hint={m.storyUpToFourLinks()}
      bind:items={links}
      newItem={() => ({ url: "" })}
      max={4}
      reorderable
      labels={{
        add: m.storyAddLink(),
        remove: (row) => m.storyRemoveRow({ row }),
        moveUp: (row) => m.storyMoveRowUp({ row }),
        moveDown: (row) => m.storyMoveRowDown({ row }),
        rowLabel: (n) => m.storyLinkNumber({ n }),
        removed: (n) => m.storyLinkRemoved({ n }),
      }}
    >
      {#snippet row(link)}
        <TextField
          label={m.storyAddress()}
          type="url"
          value={link.url}
          oninput={(event) => (link.url = event.currentTarget.value)}
        />
      {/snippet}
    </ListField>
  </div>
</Story>
