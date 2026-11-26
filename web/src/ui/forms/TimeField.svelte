<script lang="ts">
  import { TimeField } from "bits-ui";
  import { onMount } from "svelte";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import {
    formatTimeText,
    pageLocale,
    parseTimeText,
    segmentAttributes,
    type DateLabels,
  } from "./dates";
  import FieldFrame from "./FieldFrame.svelte";
  import { boxClass, boxInputClass, segmentClass, segmentGroupClass } from "./styles";

  /**
   * A time of day in hours and minutes, typed in the page locale's 12- or 24-hour clock.
   * `value`, `min`, `max` and the submitted `name` are `HH:mm`, empty until every part is
   * filled. Before hydration the field is a native time input submitting the same text.
   * For a date and time together, use `DateField` with `time`. It takes the same `labels`
   * as the date fields and reads only `segments`.
   */
  type Props = FieldMessages & {
    name: string;
    value?: string;
    min?: string;
    max?: string;
    required?: boolean;
    disabled?: boolean;
    readonly?: boolean;
    labels: Pick<DateLabels, "segments">;
    onchange?: (value: string) => void;
    class?: string;
  };
  let {
    name,
    value = $bindable(""),
    min,
    max,
    required,
    disabled,
    readonly,
    labels,
    onchange,
    width = "sm",
    ...frame
  }: Props = $props();

  let hydrated = $state(false);
  let locale = $state("en");
  onMount(() => {
    locale = pageLocale();
    hydrated = true;
  });

  function changed(next: string): void {
    value = next;
    onchange?.(next);
  }
</script>

<FieldFrame {...frame} {width}>
  {#snippet children(attributes)}
    <div
      class={boxClass}
      data-disabled={disabled === true ? "" : undefined}
      data-readonly={readonly === true ? "" : undefined}
    >
      {#if !hydrated}
        <input
          {...attributes}
          type="time"
          {name}
          {value}
          {min}
          {max}
          {required}
          {disabled}
          {readonly}
          class={cn(boxInputClass, "px-3 py-2")}
        />
      {:else}
        <TimeField.Root
          value={parseTimeText(value)}
          onValueChange={(next) => changed(formatTimeText(next))}
          granularity="minute"
          minValue={parseTimeText(min)}
          maxValue={parseTimeText(max)}
          {locale}
          {required}
          {disabled}
          {readonly}
        >
          <TimeField.Input>
            {#snippet child({ props, segments })}
              <div
                {...props}
                id={attributes.id}
                aria-labelledby={`${attributes.id}-label`}
                class={segmentGroupClass}
              >
                {#each segments as { part, value: text }, index (index)}
                  <TimeField.Segment {part}>
                    {#snippet child({ props: segment })}<span
                        {...segmentAttributes(part, segment, labels.segments, attributes)}
                        class={segmentClass}>{text}</span
                      >{/snippet}
                  </TimeField.Segment>
                {/each}
              </div>
            {/snippet}
          </TimeField.Input>
        </TimeField.Root>
        <input type="hidden" {name} {value} />
      {/if}
    </div>
  {/snippet}
</FieldFrame>
