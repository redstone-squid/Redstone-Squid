<script lang="ts">
  import ArrowDownUp from "@lucide/svelte/icons/arrow-down-up";
  import Columns3 from "@lucide/svelte/icons/columns-3";
  import Copy from "@lucide/svelte/icons/copy";
  import Ellipsis from "@lucide/svelte/icons/ellipsis";
  import Flag from "@lucide/svelte/icons/flag";
  import Info from "@lucide/svelte/icons/info";
  import Pencil from "@lucide/svelte/icons/pencil";
  import SlidersHorizontal from "@lucide/svelte/icons/sliders-horizontal";
  import Trash from "@lucide/svelte/icons/trash-2";
  import Button from "@/ui/actions/Button.svelte";
  import CheckField from "@/ui/forms/CheckField.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import Dialog from "@/ui/overlays/Dialog.svelte";
  import Menu from "@/ui/overlays/Menu.svelte";
  import Popover from "@/ui/overlays/Popover.svelte";
  import Sheet from "@/ui/overlays/Sheet.svelte";
  import Tooltip from "@/ui/overlays/Tooltip.svelte";
  import DraftDialog from "@/workbench/DraftDialog.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  let confirmOpen = $state(false);
  let controlledOpen = $state(false);
  let saving = $state(false);
  let columns = $state({ creator: true, version: true, date: false });
  let lastAction = $state("");

  function save(): void {
    saving = true;
    setTimeout(() => {
      saving = false;
      controlledOpen = false;
    }, 1500);
  }
</script>

<Story
  name="Dialog"
  source="overlays/Dialog"
  description="Modal task with a focus trap; focus returns to the trigger on close. Before hydration a trigger with fallbackHref is a link."
>
  <Variant label="form, focus on the first field">
    <DraftDialog />
  </Variant>
  <Variant label="sizes">
    <div class="flex flex-wrap gap-3">
      {#each ["sm", "md", "lg", "full-mobile"] as const as size (size)}
        <Dialog
          {size}
          title={`Size ${size}`}
          description="Width follows the content's needs."
          closeLabel="Close"
          trigger={{ label: size }}
        >
          <p>
            Small dialogs suit confirmations, large ones suit pickers. full-mobile rises from the
            bottom of narrow screens.
          </p>
        </Dialog>
      {/each}
    </div>
  </Variant>
  <Variant label="destructive confirmation, focus on the safe action">
    <Dialog
      bind:open={confirmOpen}
      size="sm"
      title="Revoke Laptop CLI?"
      description="The device signs out at once. You can link it again later."
      closeLabel="Close"
      initialFocus="[data-cancel]"
      trigger={{ label: "Revoke", variant: "danger", icon: Trash }}
      fallbackHref="#dialog"
    >
      <p>Tokens issued to this device stop working.</p>
      {#snippet footer()}
        <Button variant="danger" onclick={() => (confirmOpen = false)}>Revoke device</Button>
        <Button data-cancel onclick={() => (confirmOpen = false)}>Keep device</Button>
      {/snippet}
    </Dialog>
  </Variant>
  <Variant label="controlled, not dismissible while saving">
    <div class="flex flex-wrap gap-3">
      <Button onclick={() => (controlledOpen = true)}>Rename from code</Button>
      <Dialog
        bind:open={controlledOpen}
        title="Rename build"
        closeLabel="Close"
        dismissible={!saving}
      >
        <TextField label="Title" value="Copper Bolt 5×5" />
        {#snippet footer()}
          <Button variant="primary" pending={saving} pendingLabel="Saving" onclick={save}
            >Save</Button
          >
        {/snippet}
      </Dialog>
    </div>
  </Variant>
  <Variant label="long content scrolls between header and footer">
    <Dialog title="Changelog" closeLabel="Close" trigger={{ label: "Show changelog" }} size="lg">
      {#each { length: 24 }, index (index)}
        <p class="mb-3">Version {24 - index}: tightened the piston timing on the third layer.</p>
      {/each}
      {#snippet footer()}<Button variant="primary">Done</Button>{/snippet}
    </Dialog>
  </Variant>
</Story>

<Story
  name="Sheet"
  source="overlays/Sheet"
  description="A dialog attached to a screen edge, for filters and navigation."
>
  <div class="flex flex-wrap gap-3">
    {#each ["start", "end", "bottom"] as const as side (side)}
      <Sheet
        {side}
        title="Filters"
        description={`Slides in from the ${side} edge.`}
        closeLabel="Close"
        trigger={{ label: `Filters (${side})`, icon: SlidersHorizontal }}
      >
        <div class="grid gap-3">
          <CheckField label="Has a world download" />
          <CheckField label="Survival friendly" />
          <CheckField label="Silent" />
        </div>
        {#snippet footer()}<Button variant="primary">Show 42 builds</Button>{/snippet}
      </Sheet>
    {/each}
  </div>
</Story>

<Story
  name="Menu"
  source="overlays/Menu"
  description="Actions or links behind a button. nojs is a native popover of links and forms."
>
  <Variant label="actions with icons, a separator, and a danger item">
    <div class="flex flex-wrap items-center gap-3">
      <Menu
        label="Build actions"
        align="start"
        trigger={{ label: "Actions" }}
        items={[
          { label: "Edit", icon: Pencil, onSelect: () => (lastAction = "Edit") },
          { label: "Copy link", icon: Copy, onSelect: () => (lastAction = "Copy link") },
          { label: "Report", icon: Flag, disabled: true },
          { kind: "separator" },
          { label: "Delete", icon: Trash, danger: true, onSelect: () => (lastAction = "Delete") },
        ]}
      />
      <Menu
        label="Row actions"
        trigger={{ label: "More actions", iconOnly: true, icon: Ellipsis, variant: "quiet" }}
        items={[
          { label: "Open", href: "#menu" },
          { label: "Archive", action: "#menu" },
        ]}
      />
      <p role="status" class="text-sm text-fg-muted">
        {lastAction === "" ? "" : `Chose ${lastAction}`}
      </p>
    </div>
  </Variant>
  <Variant label="checkbox items stay open">
    <Menu
      label="Columns"
      align="start"
      trigger={{ label: "Columns", icon: Columns3, size: "sm" }}
      items={Object.entries(columns).map(([column, checked]) => ({
        kind: "checkbox",
        label: column,
        checked,
        onCheckedChange: (next: boolean) => (columns = { ...columns, [column]: next }),
      }))}
    />
  </Variant>
  <Variant label="nojs: links with the current choice">
    <Menu
      nojs
      label="Sort order"
      align="start"
      trigger={{ label: "Newest", name: "Sort: Newest", icon: ArrowDownUp, size: "sm" }}
      items={[
        { label: "Newest", href: "#menu-newest", current: true },
        { label: "Most votes", href: "#menu-votes" },
        { label: "Fastest", href: "#menu-fastest" },
      ]}
    />
  </Variant>
</Story>

<Story
  name="Popover"
  source="overlays/Popover"
  description="Non-modal help beside its trigger; the page stays usable."
>
  <div class="flex flex-wrap gap-3">
    {#each ["bottom", "top", "end"] as const as placement (placement)}
      <Popover
        {placement}
        label="What pending means"
        closeLabel="Close"
        trigger={{ label: `Status help (${placement})`, icon: Info, variant: "quiet", size: "sm" }}
      >
        <p class="font-semibold">Pending review</p>
        <p class="text-fg-muted">
          A moderator checks the evidence before the record appears in listings.
        </p>
      </Popover>
    {/each}
  </div>
</Story>

<Story
  name="Tooltip"
  source="overlays/Tooltip"
  description="Shown on hover or focus. It repeats the trigger's name, never adds to it."
>
  <div class="flex flex-wrap gap-3">
    <Tooltip content="Edit">
      {#snippet children(props)}<Button {...props} iconOnly icon={Pencil} label="Edit" />{/snippet}
    </Tooltip>
    <Tooltip content="Delete" placement="bottom">
      {#snippet children(props)}<Button
          {...props}
          iconOnly
          variant="danger"
          icon={Trash}
          label="Delete"
        />{/snippet}
    </Tooltip>
  </div>
</Story>
