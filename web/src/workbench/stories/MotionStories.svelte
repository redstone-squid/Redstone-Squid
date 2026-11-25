<script lang="ts">
  import Info from "@lucide/svelte/icons/info";
  import Play from "@lucide/svelte/icons/play";
  import Button from "@/ui/actions/Button.svelte";
  import Card from "@/ui/data/Card.svelte";
  import Disclosure from "@/ui/data/Disclosure.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Switch from "@/ui/forms/Switch.svelte";
  import SaveStatus from "@/ui/feedback/SaveStatus.svelte";
  import type { SaveState } from "@/ui/feedback/save-state";
  import Layout from "@/ui/layout/Layout.svelte";
  import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
  import Tabs from "@/ui/navigation/Tabs.svelte";
  import Dialog from "@/ui/overlays/Dialog.svelte";
  import Menu from "@/ui/overlays/Menu.svelte";
  import Popover from "@/ui/overlays/Popover.svelte";
  import Sheet from "@/ui/overlays/Sheet.svelte";
  import Tooltip from "@/ui/overlays/Tooltip.svelte";
  import ResultsView from "@/ui/patterns/ResultsView.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  // Static class names so Tailwind detects them; each bar plays one token.
  const durations = [
    ["duration-fast", "duration-fast"],
    ["duration-normal", "duration-normal"],
    ["duration-slow", "duration-slow"],
  ] as const;
  const easings = [
    ["ease-standard", "ease-standard"],
    ["ease-emphasized", "ease-emphasized"],
  ] as const;
  let played = $state(false);

  // The tokens differ by tenths of a second, too little to compare side by side. Slow motion
  // stretches every token on the page, overlays included, while the switch is on.
  let slow = $state<boolean>(false);
  $effect(() => {
    if (!slow) return;
    const root = document.documentElement.style;
    for (const [token, ms] of [
      ["fast", 120],
      ["normal", 200],
      ["slow", 320],
    ] as const)
      root.setProperty(`--duration-${token}`, `${ms * 5}ms`);
    return () => {
      for (const token of ["fast", "normal", "slow"]) root.removeProperty(`--duration-${token}`);
    };
  });

  const menuItems = [{ label: "Edit" }, { label: "Duplicate" }, { label: "Report" }];
  const linkItems = [
    { label: "English", href: "#overlays", current: true },
    { label: "简体中文", href: "#overlays" },
  ];

  let unit = $state("ticks");
  const tabs = [
    { id: "opening", label: "Opening" },
    { id: "closing", label: "Closing time" },
    { id: "evidence", label: "Evidence" },
  ];

  let results = $state<"ready" | "loading">("ready");
  function reload(): void {
    results = "loading";
    setTimeout(() => (results = "ready"), 1200);
  }

  let saveState = $state<SaveState>("dirty");
  const saveLabels = {
    idle: "No changes",
    dirty: "Unsaved changes",
    saving: "Saving",
    saved: "Saved",
    offline: "Offline, changes kept",
    error: "Could not save",
    conflict: "Changed elsewhere",
    retry: "Retry",
  };
  function save(): void {
    saveState = "saving";
    setTimeout(() => (saveState = "saved"), 900);
  }
</script>

{#snippet reducedNote()}
  <!-- Only shown when the device asks for reduced motion, which turns all of this off. -->
  <div class="hidden motion-reduce:block">
    <Notice>This device asks for reduced motion, so every change below is instant.</Notice>
  </div>
{/snippet}

<Story
  name="Tokens"
  heading="Motion tokens"
  source="app.css"
  description="Durations and easings for every transition."
>
  {@render reducedNote()}
  <div class="flex flex-wrap items-center gap-6">
    <Button icon={Play} onclick={() => (played = !played)} aria-pressed={played}>Play</Button>
    <Switch label="Slow motion (×5)" bind:checked={slow} />
  </div>
  <Variant label="Durations, with ease-standard">
    <div class="grid gap-3">
      {#each durations as [token, duration] (token)}
        <div class="grid gap-1">
          <code class="font-mono text-xs text-fg-muted">{token}</code>
          <span
            class={[
              "block h-2 origin-left rounded-full bg-accent ease-standard motion-safe:transition-transform",
              duration,
              played ? "scale-x-100" : "scale-x-10",
            ]}
          ></span>
        </div>
      {/each}
    </div>
  </Variant>
  <Variant label="Easings, with duration-slow">
    <div class="grid gap-3">
      {#each easings as [token, easing] (token)}
        <div class="grid gap-1">
          <code class="font-mono text-xs text-fg-muted">{token}</code>
          <span
            class={[
              "block h-2 origin-left rounded-full bg-accent duration-slow motion-safe:transition-transform",
              easing,
              played ? "scale-x-100" : "scale-x-10",
            ]}
          ></span>
        </div>
      {/each}
    </div>
  </Variant>
</Story>

<Story
  name="Overlays"
  heading="Overlays enter and leave"
  source="overlays/Dialog"
  description="Dialogs, sheets, popovers, menus, and tooltips opening and closing."
>
  {@render reducedNote()}
  <div class="flex flex-wrap items-center gap-3">
    <Dialog title="Rename build" closeLabel="Close" trigger={{ label: "Dialog" }}>
      <p>Close it to see it leave.</p>
    </Dialog>
    <Sheet title="Filters" closeLabel="Close" trigger={{ label: "Sheet" }}>
      <p>The sheet slides back to its edge.</p>
    </Sheet>
    <Popover trigger={{ label: "Popover" }} label="About motion" closeLabel="Close">
      <p class="text-sm">Fades in beside its trigger and out again.</p>
    </Popover>
    <Menu trigger={{ label: "Menu" }} label="Build actions" items={menuItems} />
    <Menu trigger={{ label: "Language" }} label="Language" items={linkItems} nojs />
    <Tooltip content="More about motion">
      {#snippet children(props)}<Button
          {...props}
          variant="quiet"
          iconOnly
          icon={Info}
          label="More about motion"
        />{/snippet}
    </Tooltip>
  </div>
</Story>

<Story
  name="Disclosure"
  heading="Disclosure grows to its content"
  source="data/Disclosure"
  description="Opened content growing to its full height."
>
  {@render reducedNote()}
  <Disclosure summary="Door evidence" variant="card">
    <p>Measured with a tick counter on Java 1.21.4, opening in 12 ticks and closing in 9.</p>
    <p class="mt-2">The recording shows both directions twice.</p>
  </Disclosure>
</Story>

<Story
  name="Selection"
  heading="Indicators move to the new choice"
  source="navigation/Tabs"
  description="The indicator in panel tabs and radio segmented controls."
>
  {@render reducedNote()}
  <Tabs mode="panel" label="Record details" items={tabs}>
    {#snippet panel(tab)}<p class="text-sm text-fg-muted">{tab.label} details.</p>{/snippet}
  </Tabs>
  <SegmentedControl
    mode="radio"
    label="Time unit"
    name="motion-unit"
    bind:value={unit}
    items={[
      { label: "Ticks", value: "ticks" },
      { label: "Seconds", value: "seconds" },
      { label: "Redstone ticks", value: "redstone" },
    ]}
  />
</Story>

<Story
  name="Loading"
  heading="Content replaces skeletons"
  source="patterns/ResultsView"
  description="Results and save states fading in as they arrive."
>
  {@render reducedNote()}
  <Variant label="ResultsView">
    <div class="grid gap-3">
      <Button class="w-fit" onclick={reload} pending={results === "loading"}>Reload</Button>
      <ResultsView view="cards" status={results} busyLabel="Loading builds">
        {#snippet cards()}
          <Layout pattern="grid">
            {#each ["Copper Bolt 5×5", "Flush 3×3", "Seamless 4×4"] as title (title)}
              <Card {title}><p class="text-sm text-fg-muted">Piston door</p></Card>
            {/each}
          </Layout>
        {/snippet}
      </ResultsView>
    </div>
  </Variant>
  <Variant label="SaveStatus">
    <div class="flex flex-wrap items-center gap-3">
      <Button onclick={save} disabled={saveState === "saving"}>Save</Button>
      <Button variant="quiet" onclick={() => (saveState = "dirty")}>Edit</Button>
      <SaveStatus state={saveState} labels={saveLabels} />
    </div>
  </Variant>
</Story>
