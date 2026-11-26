<script lang="ts">
  import CalendarIcon from "@lucide/svelte/icons/calendar";
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { getLocalTimeZone, toCalendarDateTime, today } from "@internationalized/date";
  import { DatePicker } from "bits-ui";
  import { onMount } from "svelte";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import { floatingPanelClass } from "@/ui/overlays/styles";
  import {
    formatDateText,
    pageLocale,
    parseDateText,
    segmentAttributes,
    type DateLabels,
  } from "./dates";
  import FieldFrame from "./FieldFrame.svelte";
  import { boxClass, boxInputClass, segmentClass, segmentGroupClass } from "./styles";

  /**
   * A calendar date, typed in the page locale's order or picked from a calendar. `time` adds
   * hours and minutes. `value`, `min`, `max` and the submitted `name` are ISO text:
   * `YYYY-MM-DD`, or `YYYY-MM-DDTHH:mm` with `time`, and empty until every part is filled.
   *
   * A time is wall-clock time. `timeZone` names the zone it is read in, such as
   * `Europe/Berlin`, beside the value; the server converts it to an instant. Before
   * hydration the field is a native date or `datetime-local` input submitting the same text.
   * Validate in the feature; `min` and `max` only disable days outside them.
   */
  type Props = FieldMessages & {
    name: string;
    value?: string;
    time?: boolean;
    timeZone?: string;
    min?: string;
    max?: string;
    required?: boolean;
    disabled?: boolean;
    readonly?: boolean;
    labels: DateLabels;
    onchange?: (value: string) => void;
    class?: string;
  };
  let {
    name,
    value = $bindable(""),
    time = false,
    timeZone,
    min,
    max,
    required,
    disabled,
    readonly,
    labels,
    onchange,
    width = time ? "full" : "md",
    ...frame
  }: Props = $props();

  let hydrated = $state(false);
  let locale = $state("en");
  onMount(() => {
    locale = pageLocale();
    hydrated = true;
  });
  const zoneId = $props.id();
  // A date-time field needs a date-time placeholder, or bits-ui builds date-only segments.
  const placeholder = $derived(time ? toCalendarDateTime(today(getLocalTimeZone())) : undefined);

  const buttonClass = cn(
    "grid w-control shrink-0 cursor-pointer place-items-center text-fg-muted hover:text-fg focus-visible:-outline-offset-3",
    "disabled:cursor-not-allowed disabled:hover:text-fg-muted",
  );
  const monthButtonClass = cn(buttonClass, "size-control-sm rounded-sm hover:bg-surface-muted");
  const dayClass = cn(
    "grid size-control-sm cursor-pointer place-items-center rounded-sm text-sm text-fg tabular-nums",
    "hover:bg-surface-muted data-outside-month:text-fg-muted data-today:font-bold data-today:underline",
    "data-selected:forced-highlight data-selected:bg-accent data-selected:text-on-accent",
    "data-disabled:cursor-not-allowed data-disabled:text-fg-muted data-disabled:not-data-outside-month:line-through data-disabled:hover:bg-transparent",
    "data-unavailable:cursor-not-allowed data-unavailable:text-fg-muted data-unavailable:line-through",
    "focus-visible:outline-offset-1",
  );

  let panel = $state<HTMLElement | null>(null);
  /**
   * Focus the chosen day, today, or the first open day, so arrow keys move through the grid.
   * bits-ui focuses before the panel is positioned, which scrolls the page to its top.
   */
  function focusDay(event: Event): void {
    event.preventDefault();
    const day = ["[data-selected]", "[data-today]", ":not([data-disabled])"]
      .map((state) => panel?.querySelector<HTMLElement>(`[data-bits-day]${state}`))
      .find((found) => found != null);
    day?.focus({ preventScroll: true });
  }

  function changed(next: string): void {
    value = next;
    onchange?.(next);
  }
</script>

{#snippet zone()}{#if timeZone !== undefined}<span
      id={zoneId}
      class="flex shrink-0 items-center pe-3 text-fg-muted select-none">{timeZone}</span
    >{/if}{/snippet}

<FieldFrame {...frame} {width} aria-describedby={timeZone === undefined ? undefined : zoneId}>
  {#snippet children(attributes)}
    <div
      class={boxClass}
      data-disabled={disabled === true ? "" : undefined}
      data-readonly={readonly === true ? "" : undefined}
    >
      {#if !hydrated}
        <input
          {...attributes}
          type={time ? "datetime-local" : "date"}
          {name}
          {value}
          {min}
          {max}
          {required}
          {disabled}
          {readonly}
          class={cn(boxInputClass, "px-3 py-2")}
        />
        {@render zone()}
      {:else}
        <DatePicker.Root
          value={parseDateText(value, time)}
          onValueChange={(next) => changed(formatDateText(next))}
          {placeholder}
          granularity={time ? "minute" : "day"}
          minValue={parseDateText(min, time)}
          maxValue={parseDateText(max, time)}
          {locale}
          {required}
          {disabled}
          {readonly}
          calendarLabel={labels.calendar}
          fixedWeeks
        >
          <DatePicker.Input>
            {#snippet child({ props, segments })}
              <div
                {...props}
                id={attributes.id}
                aria-labelledby={`${attributes.id}-label`}
                class={segmentGroupClass}
              >
                {#each segments as { part, value: text }, index (index)}
                  <DatePicker.Segment {part}>
                    {#snippet child({ props: segment })}<span
                        {...segmentAttributes(part, segment, labels.segments, attributes)}
                        class={segmentClass}>{text}</span
                      >{/snippet}
                  </DatePicker.Segment>
                {/each}
              </div>
            {/snippet}
          </DatePicker.Input>
          {@render zone()}
          <DatePicker.Trigger aria-label={labels.calendar} class={buttonClass}
            ><CalendarIcon class="size-4" aria-hidden="true" /></DatePicker.Trigger
          >
          <DatePicker.Portal>
            <DatePicker.Content
              bind:ref={panel}
              sideOffset={4}
              align="end"
              collisionPadding={16}
              role="dialog"
              aria-label={labels.calendar}
              onOpenAutoFocus={focusDay}
              class={cn(floatingPanelClass, "p-3")}
            >
              <DatePicker.Calendar>
                {#snippet children({ months, weekdays })}
                  <DatePicker.Header class="flex items-center justify-between gap-2">
                    <!-- bits-ui names these buttons in English, so ours replace its aria-label. -->
                    <DatePicker.PrevButton>
                      {#snippet child({ props })}<button
                          {...props}
                          aria-label={labels.previousMonth}
                          class={monthButtonClass}
                          ><ChevronLeft class="size-4" aria-hidden="true" /></button
                        >{/snippet}
                    </DatePicker.PrevButton>
                    <DatePicker.Heading class="text-sm font-semibold text-fg" />
                    <DatePicker.NextButton>
                      {#snippet child({ props })}<button
                          {...props}
                          aria-label={labels.nextMonth}
                          class={monthButtonClass}
                          ><ChevronRight class="size-4" aria-hidden="true" /></button
                        >{/snippet}
                    </DatePicker.NextButton>
                  </DatePicker.Header>
                  {#each months as month (month.value.toString())}
                    <DatePicker.Grid class="mt-2 border-collapse select-none">
                      <DatePicker.GridHead>
                        <DatePicker.GridRow class="flex">
                          {#each weekdays as weekday, index (index)}
                            <DatePicker.HeadCell
                              class="w-control-sm text-xs font-normal text-fg-muted"
                              >{weekday}</DatePicker.HeadCell
                            >
                          {/each}
                        </DatePicker.GridRow>
                      </DatePicker.GridHead>
                      <DatePicker.GridBody>
                        {#each month.weeks as week, index (index)}
                          <DatePicker.GridRow class="flex">
                            {#each week as date (date.toString())}
                              <DatePicker.Cell {date} month={month.value} class="p-0">
                                <DatePicker.Day class={dayClass} />
                              </DatePicker.Cell>
                            {/each}
                          </DatePicker.GridRow>
                        {/each}
                      </DatePicker.GridBody>
                    </DatePicker.Grid>
                  {/each}
                {/snippet}
              </DatePicker.Calendar>
            </DatePicker.Content>
          </DatePicker.Portal>
        </DatePicker.Root>
        <input type="hidden" {name} {value} />
      {/if}
    </div>
  {/snippet}
</FieldFrame>
