<script lang="ts">
  import * as m from "@/locales/messages.gen";
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

<Story name="Dialog" source="overlays/Dialog" description={m.storyDialogDescription()}>
  <Variant label={m.storyFormFocusOnTheFirstField()}>
    <DraftDialog />
  </Variant>
  <Variant label={m.storySizes()}>
    <div class="flex flex-wrap gap-3">
      {#each ["sm", "md", "lg", "full-mobile"] as const as size (size)}
        <Dialog
          {size}
          title={m.storyDialogSize({ size })}
          description={m.storyWidthFollowsTheContentSNeeds()}
          closeLabel={m.storyClose()}
          trigger={{ label: size }}
        >
          <p>
            {m.storySmallDialogsSuitConfirmationsLargeOnesSuitPickers()}
          </p>
        </Dialog>
      {/each}
    </div>
  </Variant>
  <Variant label={m.storyDestructiveConfirmationFocusOnTheSafeAction()}>
    <Dialog
      bind:open={confirmOpen}
      size="sm"
      title={m.storyRevokeLaptopCli()}
      description={m.storyTheDeviceSignsOutAtOnceYouCan()}
      closeLabel={m.storyClose()}
      initialFocus="[data-cancel]"
      trigger={{ label: m.storyRevoke(), variant: "danger", icon: Trash }}
      fallbackHref="#dialog"
    >
      <p>{m.storyTokensIssuedToThisDeviceStopWorking()}</p>
      {#snippet footer()}
        <Button variant="danger" onclick={() => (confirmOpen = false)}
          >{m.storyRevokeDevice()}</Button
        >
        <Button data-cancel onclick={() => (confirmOpen = false)}>{m.storyKeepDevice()}</Button>
      {/snippet}
    </Dialog>
  </Variant>
  <Variant label={m.storyControlledNotDismissibleWhileSaving()}>
    <div class="flex flex-wrap gap-3">
      <Button onclick={() => (controlledOpen = true)}>{m.storyRenameFromCode()}</Button>
      <Dialog
        bind:open={controlledOpen}
        title={m.storyRenameBuild()}
        closeLabel={m.storyClose()}
        dismissible={!saving}
      >
        <TextField label={m.storyTitle()} value={m.storyCopperBolt55()} />
        {#snippet footer()}
          <Button variant="primary" pending={saving} pendingLabel={m.storySaving()} onclick={save}
            >{m.storySave()}</Button
          >
        {/snippet}
      </Dialog>
    </div>
  </Variant>
  <Variant label={m.storyLongContentScrollsBetweenHeaderAndFooter()}>
    <Dialog
      title={m.storyChangelog()}
      closeLabel={m.storyClose()}
      trigger={{ label: m.storyShowChangelog() }}
      size="lg"
    >
      {#each { length: 24 }, index (index)}
        <p class="mb-3">{m.storyChangelogEntry({ version: 24 - index })}</p>
      {/each}
      {#snippet footer()}<Button variant="primary">{m.storyDone()}</Button>{/snippet}
    </Dialog>
  </Variant>
</Story>

<Story name="Sheet" source="overlays/Sheet" description={m.storySheetDescription()}>
  <div class="flex flex-wrap gap-3">
    {#each ["start", "end", "bottom"] as const as side (side)}
      <Sheet
        {side}
        title={m.storyFilters()}
        description={m.storySheetEdge({ side })}
        closeLabel={m.storyClose()}
        trigger={{ label: m.storySheetTrigger({ side }), icon: SlidersHorizontal }}
      >
        <div class="grid gap-3">
          <CheckField label={m.storyHasAWorldDownload()} />
          <CheckField label={m.storySurvivalFriendlyLabel()} />
          <CheckField label={m.storySilent()} />
        </div>
        {#snippet footer()}<Button variant="primary">{m.storyShow42Builds()}</Button>{/snippet}
      </Sheet>
    {/each}
  </div>
</Story>

<Story name="Menu" source="overlays/Menu" description={m.storyMenuDescription()}>
  <Variant label={m.storyActionsWithIconsASeparatorAndADanger()}>
    <div class="flex flex-wrap items-center gap-3">
      <Menu
        label={m.storyBuildActions()}
        align="start"
        trigger={{ label: m.storyActions() }}
        items={[
          { label: m.storyEdit(), icon: Pencil, onSelect: () => (lastAction = m.storyEdit()) },
          {
            label: m.storyCopyLink(),
            icon: Copy,
            onSelect: () => (lastAction = m.storyCopyLink()),
          },
          { label: m.storyReport(), icon: Flag, disabled: true },
          { kind: "separator" },
          {
            label: m.storyDelete(),
            icon: Trash,
            danger: true,
            onSelect: () => (lastAction = m.storyDelete()),
          },
        ]}
      />
      <Menu
        label={m.storyRowActions()}
        trigger={{ label: m.storyMoreActions(), iconOnly: true, icon: Ellipsis, variant: "quiet" }}
        items={[
          { label: m.storyOpen(), href: "#menu" },
          { label: m.storyArchive(), action: "#menu" },
        ]}
      />
      <p role="status" class="text-sm text-fg-muted">
        {lastAction === "" ? "" : m.storyChoseAction({ lastAction })}
      </p>
    </div>
  </Variant>
  <Variant label={m.storyCheckboxItemsStayOpen()}>
    <Menu
      label={m.storyColumns()}
      align="start"
      trigger={{ label: m.storyColumns(), icon: Columns3, size: "sm" }}
      items={Object.entries(columns).map(([column, checked]) => ({
        kind: "checkbox",
        label: column,
        checked,
        onCheckedChange: (next: boolean) => (columns = { ...columns, [column]: next }),
      }))}
    />
  </Variant>
  <Variant label={m.storyNojsLinksWithTheCurrentChoice()}>
    <Menu
      nojs
      label={m.storySortOrder()}
      align="start"
      trigger={{ label: m.storyNewest(), name: m.storySortNewest(), icon: ArrowDownUp, size: "sm" }}
      items={[
        { label: m.storyNewest(), href: "#menu-newest", current: true },
        { label: m.storyMostVotes(), href: "#menu-votes" },
        { label: m.storyFastest(), href: "#menu-fastest" },
      ]}
    />
  </Variant>
</Story>

<Story name="Popover" source="overlays/Popover" description={m.storyPopoverDescription()}>
  <div class="flex flex-wrap gap-3">
    {#each ["bottom", "top", "end"] as const as placement (placement)}
      <Popover
        {placement}
        label={m.storyWhatPendingMeans()}
        closeLabel={m.storyClose()}
        trigger={{
          label: m.storyStatusHelp({ placement }),
          icon: Info,
          variant: "quiet",
          size: "sm",
        }}
      >
        <p class="font-semibold">{m.storyPendingReview()}</p>
        <p class="text-fg-muted">
          {m.storyAModeratorChecksTheEvidenceBeforeTheRecord()}
        </p>
      </Popover>
    {/each}
  </div>
</Story>

<Story name="Tooltip" source="overlays/Tooltip" description={m.storyTooltipDescription()}>
  <div class="flex flex-wrap gap-3">
    <Tooltip content={m.storyEdit()}>
      {#snippet children(props)}<Button
          {...props}
          iconOnly
          icon={Pencil}
          label={m.storyEdit()}
        />{/snippet}
    </Tooltip>
    <Tooltip content={m.storyDelete()} placement="bottom">
      {#snippet children(props)}<Button
          {...props}
          iconOnly
          variant="danger"
          icon={Trash}
          label={m.storyDelete()}
        />{/snippet}
    </Tooltip>
  </div>
</Story>
