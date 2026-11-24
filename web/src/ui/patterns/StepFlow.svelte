<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import { focusTarget } from "@/ui/lib/focus";

  type StepStatus = "pending" | "done" | "error";

  /**
   * The steps of a flow, such as entering a code, reviewing and confirming, or the phases of
   * a finalization job. The `current` step is marked `aria-current="step"`, and its content
   * (`children`) sits under a heading named after it.
   *
   * When `current` changes after the first render, focus moves to that heading, so keyboard
   * and screen reader users land on the new step. Status words come from `labels`, since the
   * markers alone are only shape and color.
   */
  type Props = {
    /** Names the list of steps. */
    label: string;
    steps: { id: string; label: string; status: StepStatus }[];
    current: string;
    labels: Record<StepStatus | "current", string>;
    headingLevel?: 2 | 3;
    children?: Snippet;
    class?: string;
  };
  let {
    label,
    steps,
    current,
    labels,
    headingLevel = 2,
    children,
    class: className,
  }: Props = $props();
  const id = $props.id();
  const currentStep = $derived(steps.find((step) => step.id === current));
  let heading = $state<HTMLElement>();
  let shown: string | undefined;
  $effect(() => {
    const step = current;
    if (shown !== undefined && shown !== step && heading) focusTarget(heading);
    shown = step;
  });
</script>

<div class={cn("@container grid min-w-0 gap-6", className)}>
  <ol aria-label={label} class="grid gap-3 @2xl:flex @2xl:gap-0">
    {#each steps as step, index (step.id)}
      {@const isCurrent = step.id === current}
      <li
        aria-current={isCurrent ? "step" : undefined}
        class="flex min-w-0 items-center gap-3 @2xl:flex-1 @2xl:not-last:after:mx-3 @2xl:not-last:after:h-px @2xl:not-last:after:min-w-4 @2xl:not-last:after:flex-1 @2xl:not-last:after:bg-line-strong"
      >
        <span
          class={cn(
            "grid size-8 shrink-0 place-items-center rounded-full border-2 text-sm font-semibold",
            step.status === "done" && "border-success bg-success-soft text-success",
            step.status === "error" && "border-danger bg-danger-soft text-danger",
            step.status === "pending" &&
              (isCurrent ? "border-accent text-accent" : "border-line-strong text-fg-muted"),
          )}
          aria-hidden="true"
        >
          {#if step.status === "done"}<Check
              class="size-4"
            />{:else if step.status === "error"}<CircleAlert class="size-4" />{:else}{index +
              1}{/if}
        </span>
        <span class="grid min-w-0 text-sm">
          <span class={cn("wrap-break-word", isCurrent ? "font-semibold text-fg" : "text-fg-muted")}
            >{step.label}</span
          >
          <span
            class={cn(
              "text-xs text-fg-muted",
              !isCurrent && step.status === "pending" && "sr-only",
            )}>{isCurrent && step.status === "pending" ? labels.current : labels[step.status]}</span
          >
        </span>
      </li>
    {/each}
  </ol>
  {#if currentStep}
    <div class="grid min-w-0 gap-4">
      <svelte:element
        this={`h${headingLevel}`}
        bind:this={heading}
        id={`${id}-heading`}
        class="text-xl font-bold tracking-tight">{currentStep.label}</svelte:element
      >
      {@render children?.()}
    </div>
  {/if}
</div>
