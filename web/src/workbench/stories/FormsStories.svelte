<script lang="ts">
  import Box from "@lucide/svelte/icons/box";
  import DoorOpen from "@lucide/svelte/icons/door-open";
  import Search from "@lucide/svelte/icons/search";
  import Zap from "@lucide/svelte/icons/zap";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import Autocomplete from "@/ui/forms/Autocomplete.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import ChoiceGroup from "@/ui/forms/ChoiceGroup.svelte";
  import CodeEntry from "@/ui/forms/CodeEntry.svelte";
  import Combobox from "@/ui/forms/Combobox.svelte";
  import ErrorSummary from "@/ui/forms/ErrorSummary.svelte";
  import FieldGroup from "@/ui/forms/FieldGroup.svelte";
  import FileDrop from "@/ui/forms/FileDrop.svelte";
  import FormActions from "@/ui/forms/FormActions.svelte";
  import ListField from "@/ui/forms/ListField.svelte";
  import RangeField from "@/ui/forms/RangeField.svelte";
  import SaveStatus, { type SaveState } from "@/ui/forms/SaveStatus.svelte";
  import SelectField from "@/ui/forms/SelectField.svelte";
  import Switch from "@/ui/forms/Switch.svelte";
  import TextareaField from "@/ui/forms/TextareaField.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import UnitField from "@/ui/forms/UnitField.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import { versionOptions } from "@/workbench/draft-fixture";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  const versions = versionOptions.map((version) => ({ value: version, label: version }));
  const tags = ["flush", "seamless", "tileable", "silent", "compact", "survival friendly"].map(
    (tag) => ({ value: tag, label: tag }),
  );
  const comboboxLabels = {
    toggle: "Show options",
    noResults: "No matches",
    loading: "Loading",
    error: "Options are unavailable.",
    remove: (label: string) => `Remove ${label}`,
    create: (text: string) => `Add “${text}”`,
  };
  const delayUnits = [
    { id: "gt", label: "game ticks", factor: 1 },
    { id: "rt", label: "redstone ticks", factor: 2 },
    { id: "s", label: "s", factor: 20 },
  ];
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
    idle: "All changes saved",
    dirty: "Unsaved changes",
    saving: "Saving",
    saved: "Saved",
    offline: "Offline. Changes are kept on this device.",
    error: "Couldn’t save",
    conflict: "Changed elsewhere. Review before saving.",
    retry: "Retry",
  };

  let note = $state("Opens in 0.6 s with no visible pistons.");
  let notifications = $state(true);
  let syncing = $state(false);
  let edition = $state<string | string[]>("java");
  let features = $state<string | string[]>(["flush"]);
  let category = $state<string | string[]>("door");
  let delay = $state<number | null>(25);
  let code = $state("");
  let version = $state<string | string[]>("Java 1.21.4");
  let chosenTags = $state<string | string[]>(["flush", "seamless"]);
  let links = $state([{ url: "https://youtube.com/@circuitsage" }]);
  let files = $state<string[]>([]);
  let rejected = $state<string[]>([]);
  let width = $state({ name: "width-min", value: "3", label: "Minimum width" });
  let widthMax = $state({ name: "width-max", value: "", label: "Maximum width" });
  let badMin = $state({ name: "bad-min", value: "9", label: "Minimum height" });
  let badMax = $state({
    name: "bad-max",
    value: "4",
    label: "Maximum height",
    error: "Use a maximum of at least 9.",
  });
  const problems = () => [
    { fieldId: "story-summary-title", message: "Enter a title for your build." },
    { fieldId: "story-summary-width", message: "Enter the opening width." },
  ];
  let summaryErrors = $state(problems());
  let submitted = $state(false);

  function startSync(): void {
    syncing = true;
    window.setTimeout(() => (syncing = false), 1200);
  }
</script>

<Story name="TextField" source="forms/TextField" description="Single-line text and numbers.">
  <Layout pattern="grid" min="card">
    <Variant label="hint and placeholder">
      <TextField label="Creator" hint="Public name shown on the build." placeholder="CircuitSage" />
    </Variant>
    <Variant label="optional">
      <TextField label="Video link" detail="optional" type="url" />
    </Variant>
    <Variant label="error">
      <TextField label="Build title" error="Enter a title for your build." required />
    </Variant>
    <Variant label="number with unit, narrow">
      <TextField label="Opening width" inputmode="numeric" value="5" suffix="blocks" width="sm" />
    </Variant>
    <Variant label="prefix">
      <TextField label="Channel" prefix="#" value="door-records" width="md" />
    </Variant>
    <Variant label="icon and clear button">
      <TextField
        label="Search builds"
        type="search"
        icon={Search}
        value="piston door"
        clearable
        clearLabel="Clear search"
      />
    </Variant>
    <Variant label="counter">
      <TextField label="Short title" maxlength={24} counter value="Seamless 3×3 flush door" />
    </Variant>
    <Variant label="disabled">
      <TextField label="Build id" value="b_01HZ8Q" disabled />
    </Variant>
    <Variant label="read-only">
      <TextField label="Submitted by" value="CircuitSage" readonly />
    </Variant>
    <Variant label="hidden label (toolbar)">
      <TextField label="Filter tags" labelVisibility="hidden" placeholder="Filter tags" />
    </Variant>
  </Layout>
</Story>

<Story name="TextareaField" source="forms/TextareaField">
  <Layout pattern="grid" min="card-wide">
    <Variant label="counter and autosize">
      <TextareaField
        label="Builder notes"
        hint="Describe the opening sequence or timing."
        maxlength={200}
        counter
        autosize
        bind:value={note}
      />
    </Variant>
    <Variant label="error">
      <TextareaField label="Change summary" error="Say what changed for reviewers." />
    </Variant>
    <Variant label="disabled">
      <TextareaField label="Moderator notes" value="Locked while voting." disabled />
    </Variant>
  </Layout>
</Story>

<Story name="SelectField" source="forms/SelectField" description="A native select.">
  <Layout pattern="grid" min="card">
    <Variant label="options and placeholder">
      <SelectField
        label="Category"
        hint="Choose the closest category."
        placeholder="Choose a category"
        options={[
          { value: "door", label: "Piston door" },
          { value: "utility", label: "Utility" },
          { value: "farm", label: "Farm", disabled: true },
        ]}
      />
    </Variant>
    <Variant label="option children, narrow">
      <SelectField label="Sort" width="sm" value="new">
        <option value="new">Newest</option>
        <option value="fast">Fastest</option>
      </SelectField>
    </Variant>
    <Variant label="error">
      <SelectField
        label="Edition"
        placeholder="Choose an edition"
        error="Choose the edition you tested on."
        options={[{ value: "java", label: "Java" }]}
      />
    </Variant>
    <Variant label="disabled">
      <SelectField label="Server" disabled options={[{ value: "main", label: "Main" }]} />
    </Variant>
  </Layout>
</Story>

<Story name="CheckField" source="forms/CheckField" description="One checkbox saved with a form.">
  <Layout pattern="grid" min="card">
    <Variant label="checked">
      <CheckField label="Include build dimensions" checked />
    </Variant>
    <Variant label="hint">
      <CheckField label="Hide my profile" hint="Only your builds stay public." />
    </Variant>
    <Variant label="error">
      <CheckField label="I agree to the submission rules" error="Agree to the rules to submit." />
    </Variant>
    <Variant label="disabled">
      <CheckField label="Verified by staff" checked disabled />
    </Variant>
  </Layout>
</Story>

<Story name="Switch" source="forms/Switch" description="A setting that applies immediately.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="off">
      <Switch label="Show my identity" hint="Your Discord name appears on credits." />
    </Variant>
    <Variant label="on, saving after a click">
      <Switch
        label="Record notifications"
        bind:checked={notifications}
        pending={syncing}
        onchange={startSync}
      />
    </Variant>
    <Variant label="pending">
      <Switch label="Hide my profile" checked pending />
    </Variant>
    <Variant label="error">
      <Switch label="Email digests" error="Add an email address first." />
    </Variant>
    <Variant label="disabled">
      <Switch label="Staff mode" checked disabled />
    </Variant>
  </Layout>
</Story>

<Story name="ChoiceGroup" source="forms/ChoiceGroup" description="Radios or checkboxes.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="radio list with descriptions">
      <ChoiceGroup
        label="Minecraft edition"
        name="story-edition"
        bind:value={edition}
        options={[
          { value: "java", label: "Java", description: "Tested on a Java server or client." },
          { value: "bedrock", label: "Bedrock", description: "Tested on Bedrock Edition." },
          { value: "both", label: "Both", disabled: true },
        ]}
      />
    </Variant>
    <Variant label="checkboxes inline">
      <ChoiceGroup
        type="checkbox"
        variant="inline"
        label="Features"
        detail="optional"
        name="story-features"
        bind:value={features}
        options={[
          { value: "flush", label: "Flush" },
          { value: "seamless", label: "Seamless" },
          { value: "silent", label: "Silent" },
        ]}
      />
    </Variant>
    <Variant label="error">
      <ChoiceGroup
        label="Direction"
        name="story-direction"
        error="Choose which way the door opens."
        options={[
          { value: "in", label: "Inward" },
          { value: "out", label: "Outward" },
        ]}
      />
    </Variant>
  </Layout>
  <Variant label="cards in three columns">
    <ChoiceGroup
      variant="cards"
      columns={3}
      label="What are you submitting?"
      name="story-category"
      bind:value={category}
      options={[
        { value: "door", label: "Door", description: "Piston doors of any size.", icon: DoorOpen },
        { value: "utility", label: "Utility", description: "Machines and farms.", icon: Zap },
        { value: "other", label: "Other", description: "Anything else.", icon: Box },
      ]}
    />
  </Variant>
</Story>

<Story name="FieldGroup" source="forms/FieldGroup" description="Related fields in a fieldset.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="row">
      <FieldGroup label="Door size" hint="In blocks, measured from the opening." layout="row">
        <TextField label="Width" inputmode="numeric" value="3" width="xs" />
        <TextField label="Height" inputmode="numeric" value="3" width="xs" />
        <TextField label="Depth" detail="optional" inputmode="numeric" width="xs" />
      </FieldGroup>
    </Variant>
    <Variant label="error">
      <FieldGroup label="Contact" error="Give at least one way to reach you.">
        <TextField label="Discord" />
        <TextField label="Email" type="email" />
      </FieldGroup>
    </Variant>
  </Layout>
</Story>

<Story name="RangeField" source="forms/RangeField" description="A minimum and maximum.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="with unit">
      <RangeField
        label="Width"
        hint="Leave either end empty for no limit."
        separator="to"
        unit="blocks"
        bind:min={width}
        bind:max={widthMax}
      />
    </Variant>
    <Variant label="error on one bound">
      <RangeField label="Height" separator="to" bind:min={badMin} bind:max={badMax} />
    </Variant>
  </Layout>
</Story>

<Story name="UnitField" source="forms/UnitField" description="A quantity in a chosen unit.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="converted">
      <UnitField
        label="Opening time"
        hint="How long the door takes to open."
        name="story-delay"
        unitName="story-delay-unit"
        units={delayUnits}
        labels={{ unit: "Unit" }}
        bind:value={delay}
      />
    </Variant>
    <Variant label="error">
      <UnitField
        label="Closing time"
        name="story-close"
        unitName="story-close-unit"
        units={delayUnits}
        labels={{ unit: "Unit" }}
        value={null}
        error="Enter a time."
      />
    </Variant>
  </Layout>
</Story>

<Story name="CodeEntry" source="forms/CodeEntry" description="A one-time code in one input.">
  <Layout pattern="grid" min="card">
    <Variant label="grouped letters and digits">
      <CodeEntry
        label="Merge code"
        hint="Shown on your other account."
        name="story-merge"
        length={8}
        groups={[4, 4]}
        bind:value={code}
      />
    </Variant>
    <Variant label="digits">
      <CodeEntry label="Sign-in code" name="story-login" length={6} charset="digits" />
    </Variant>
    <Variant label="error">
      <CodeEntry
        label="Link code"
        name="story-link"
        length={8}
        groups={[4, 4]}
        value="K7Q2"
        error="This code has expired. Ask for a new one."
      />
    </Variant>
  </Layout>
</Story>

<Story
  name="ErrorSummary"
  source="forms/ErrorSummary"
  description="What stopped a submit; it takes focus after each failed submit."
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
      title="Fix 2 problems to submit"
      errors={summaryErrors}
      focusOnMount={submitted}
    />
    <TextField id="story-summary-title" label="Build title" error="Enter a title for your build." />
    <TextField
      id="story-summary-width"
      label="Opening width"
      inputmode="numeric"
      width="xs"
      error="Enter the opening width."
    />
    <div><Button type="submit" variant="primary">Submit for review</Button></div>
  </form>
</Story>

<Story name="FormActions" source="forms/FormActions" description="The buttons that end a form.">
  <Variant label="primary, secondary, destructive and note">
    <FormActions note="Publishing is final; you can still edit the description later.">
      {#snippet destructive()}<Button variant="quiet">Discard draft</Button>{/snippet}
      {#snippet secondary()}<Button>Save draft</Button>{/snippet}
      {#snippet primary()}<Button variant="primary">Publish</Button>{/snippet}
    </FormActions>
  </Variant>
  <Variant label="sticky">
    <div class="grid max-h-48 gap-4 overflow-y-auto rounded-sm border border-line p-4">
      <TextField label="Title" />
      <TextareaField label="Description" />
      <FormActions sticky>
        {#snippet primary()}<Button variant="primary">Save</Button>{/snippet}
      </FormActions>
    </div>
  </Variant>
</Story>

<Story name="SaveStatus" source="forms/SaveStatus" description="Where an autosaving form stands.">
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

<Story name="FileDrop" source="forms/FileDrop" description="Pick or drop files.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="multiple, size limit">
      <FileDrop
        label="Schematics"
        hint="Litematic or schem files, up to 1 MB each."
        accept=".litematic,.schem"
        multiple
        maxBytes={1_000_000}
        labels={{ drop: "Drop files here, or", browse: "Choose files" }}
        onFiles={(accepted, skipped) => {
          files = accepted.map((file) => file.name);
          rejected = skipped.map((file) => file.name);
        }}
      />
      {#if files.length > 0}<p class="text-sm">Chosen: {files.join(", ")}</p>{/if}
      {#if rejected.length > 0}<p class="text-sm">Skipped: {rejected.join(", ")}</p>{/if}
    </Variant>
    <Variant label="error">
      <FileDrop
        label="Cover image"
        accept="image/*"
        error="The image could not be read. Choose another."
        labels={{ drop: "Drop an image here, or", browse: "Choose image" }}
        onFiles={() => undefined}
      />
    </Variant>
    <Variant label="disabled">
      <FileDrop
        label="Video"
        disabled
        hint="Uploads open once the draft has a title."
        labels={{ drop: "Drop a video here, or", browse: "Choose video" }}
        onFiles={() => undefined}
      />
    </Variant>
  </Layout>
</Story>

<Story name="Autocomplete" source="forms/Autocomplete" description="Free text with suggestions.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="suggestions with descriptions">
      <Autocomplete
        name="story-version"
        label={m.workbenchMinecraftVersion()}
        hint="Type to see versions."
        minQueryLength={0}
        labels={{
          suggestions: m.searchSuggestions(),
          loading: "Loading",
          error: m.searchSuggestionError(),
          noResults: "No matching versions",
          count: (count) => `${count} suggestions`,
        }}
        load={(query) =>
          Promise.resolve({
            items: versionOptions
              .filter((value) => value.toLowerCase().includes(query.trim().toLowerCase()))
              .map((value) => ({ value, label: value, description: "Release" })),
          })}
      />
    </Variant>
    <Variant label="error">
      <Autocomplete
        name="story-credit"
        label="Credited builder"
        error="Enter a builder name."
        labels={{
          suggestions: "Builders",
          loading: "Loading",
          error: "Suggestions are unavailable.",
          noResults: "No builders found",
          count: (count) => `${count} suggestions`,
        }}
        load={() => Promise.resolve({ items: [] })}
      />
    </Variant>
  </Layout>
</Story>

<Story name="Combobox" source="forms/Combobox" description="Choose values from a set.">
  <Layout pattern="grid" min="card-wide">
    <Variant label="single">
      <Combobox
        label="Minecraft version"
        name="story-combobox-version"
        options={versions}
        bind:value={version}
        labels={comboboxLabels}
      />
    </Variant>
    <Variant label="multiple, can add new">
      <Combobox
        label="Tags"
        name="story-combobox-tags"
        hint="Pick tags or add your own."
        options={tags}
        multiple
        allowCreate
        bind:value={chosenTags}
        labels={comboboxLabels}
      />
    </Variant>
    <Variant label="loaded from the server">
      <Combobox
        label="Build to match"
        name="story-combobox-build"
        placeholder="Search builds"
        labels={comboboxLabels}
        load={(query) =>
          Promise.resolve(
            ["Copper Bolt", "Seamless Vault", "Tiny Flush"]
              .filter((title) => title.toLowerCase().includes(query.toLowerCase()))
              .map((title) => ({ value: title, label: title, description: "3×3 door" })),
          )}
      />
    </Variant>
    <Variant label="error">
      <Combobox
        label="Channel"
        name="story-combobox-channel"
        options={[{ value: "records", label: "#records" }]}
        error="Choose a channel."
        labels={comboboxLabels}
      />
    </Variant>
  </Layout>
</Story>

<Story name="ListField" source="forms/ListField" description="Repeatable rows of fields.">
  <div class="max-w-form">
    <ListField
      label="Profile links"
      hint="Up to four links."
      bind:items={links}
      newItem={() => ({ url: "" })}
      max={4}
      reorderable
      labels={{
        add: "Add link",
        remove: (row) => `Remove ${row}`,
        moveUp: (row) => `Move ${row} up`,
        moveDown: (row) => `Move ${row} down`,
        rowLabel: (n) => `Link ${n}`,
        removed: (n) => `Link ${n} removed`,
      }}
    >
      {#snippet row(link)}
        <TextField
          label="Address"
          type="url"
          value={link.url}
          oninput={(event) => (link.url = event.currentTarget.value)}
        />
      {/snippet}
    </ListField>
  </div>
</Story>
