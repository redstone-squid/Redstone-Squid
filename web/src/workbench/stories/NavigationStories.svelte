<script lang="ts">
  import LayoutGrid from "@lucide/svelte/icons/layout-grid";
  import Table from "@lucide/svelte/icons/table";
  import Bell from "@lucide/svelte/icons/bell";
  import User from "@lucide/svelte/icons/user";
  import * as m from "@/locales/messages.gen";
  import Breadcrumbs from "@/ui/navigation/Breadcrumbs.svelte";
  import NavList from "@/ui/navigation/NavList.svelte";
  import Pagination from "@/ui/navigation/Pagination.svelte";
  import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
  import Tabs from "@/ui/navigation/Tabs.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  // Each specimen needs its own name: several navigation landmarks share this page.
  let preview = $state("render");
  let unit = $state("ticks");
  let vote = $state<string>();

  const labels = (nav: string) => ({ nav, previous: m.commonPrevious(), next: m.commonNext() });
</script>

<Story name="Breadcrumbs" source="navigation/Breadcrumbs">
  <Breadcrumbs
    label={m.storyBreadcrumb()}
    items={[
      { label: m.navBuilds(), href: "#breadcrumbs" },
      { label: m.storyDoors(), href: "#breadcrumbs" },
      { label: m.storyCopperBolt55() },
    ]}
  />
</Story>

<Story name="Tabs" source="navigation/Tabs" description={m.storyTabsDescription()}>
  <Variant label={m.storyLinkModeForViewsKeptInTheUrl()}>
    <Tabs
      label={m.storySearchScope()}
      items={[
        { label: m.storyAll(), href: "#tabs", current: true, count: "312" },
        { label: m.navBuilds(), href: "#tabs-builds", count: "280" },
        { label: m.navRecords(), href: "#tabs-records", count: "32" },
      ]}
    />
  </Variant>
  <Variant label={m.storyPanelModeArrowKeysSwitchPanels()}>
    <Tabs
      mode="panel"
      label={m.storyPreview()}
      bind:value={preview}
      items={[
        { id: "render", label: m.storyRender() },
        { id: "schematic", label: m.storySchematic(), count: "3" },
        { id: "video", label: m.storyVideo(), disabled: true },
      ]}
    >
      {#snippet panel(tab)}
        <p class="text-sm text-fg-muted">{m.storyTabPreview({ tab: tab.label.toLowerCase() })}</p>
      {/snippet}
    </Tabs>
  </Variant>
</Story>

<Story name="SegmentedControl" source="navigation/SegmentedControl">
  <Variant label={m.storyLinkMode()}>
    <SegmentedControl
      label={m.storyView()}
      items={[
        { label: m.storyCards(), href: "#segmentedcontrol", current: true, icon: LayoutGrid },
        { label: m.storyTable(), href: "#segmentedcontrol-table", icon: Table },
      ]}
    />
  </Variant>
  <Variant label={m.storyRadioModeSizeMd()}>
    <SegmentedControl
      mode="radio"
      label={m.storyDurationUnit()}
      name="unit"
      size="md"
      bind:value={unit}
      items={[
        { label: m.storyTicks(), value: "ticks" },
        { label: m.storySeconds(), value: "seconds" },
        { label: m.storyMinutes(), value: "minutes", disabled: true },
      ]}
    />
  </Variant>
  <Variant label={m.storyRadioModeNothingChosenYet()}>
    <SegmentedControl
      mode="radio"
      label={m.storyYourVote()}
      name="vote"
      bind:value={vote}
      items={[
        { label: m.storyApprove(), value: "approve" },
        { label: m.storyReject(), value: "reject" },
      ]}
    />
  </Variant>
</Story>

<Story name="NavList" source="navigation/NavList">
  <NavList
    label={m.storyAccount()}
    class="max-w-64"
    groups={[
      {
        heading: m.storyAccount(),
        items: [
          { label: m.storyProfile(), href: "#navlist", current: true, icon: User },
          { label: m.storyNotifications(), href: "#navlist-notifications", count: "3", icon: Bell },
        ],
      },
    ]}
  />
</Story>

<Story
  name="Pagination"
  source="navigation/Pagination"
  description={m.storyPaginationDescription()}
>
  <Variant label={m.storyPreviousAndNext()}>
    <Pagination
      prevHref="#pagination-prev"
      nextHref="#pagination-next"
      range={m.story2140Of312()}
      labels={labels(m.navPagination())}
    />
  </Variant>
  <Variant label={m.storyFirstPage()}>
    <Pagination nextHref="#pagination-next" labels={labels(m.storyFirstPage())} />
  </Variant>
  <Variant label={m.storyNumbered()}>
    <Pagination
      prevHref="#pagination-1"
      nextHref="#pagination-3"
      range={m.story2140Of60()}
      labels={labels(m.storyNumberedPages())}
      pages={[
        { label: "1", href: "#pagination-1", name: m.storyPage1() },
        { label: "2", href: "#pagination-2", name: m.storyPage2(), current: true },
        { label: "3", href: "#pagination-3", name: m.storyPage3() },
      ]}
    />
  </Variant>
</Story>
