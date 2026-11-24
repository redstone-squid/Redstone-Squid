<script lang="ts">
  import { DropdownMenu } from "bits-ui";
  import Check from "@lucide/svelte/icons/check";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { triggerButtonProps, type MenuItem, type OverlayTrigger } from "./props";
  import { floatingPanelClass, menuItemClass } from "./styles";

  /**
   * A button that opens a list of actions or links, such as row actions or a sort order.
   *
   * By default it is an ARIA menu: arrow keys and typeahead move between items, Escape and Tab
   * close it, and focus returns to the trigger. It needs JavaScript.
   *
   * `nojs` renders a native popover of links and POST forms instead, which works without
   * JavaScript; the browser closes it on Escape or an outside click and returns focus. Use it
   * for navigation such as the language menu. Only `href` and `action` items work there, and
   * Tab moves through them as a plain list.
   */
  type Props = {
    trigger: OverlayTrigger;
    /** The list's accessible name. */
    label: string;
    items: MenuItem[];
    /** Which trigger edge the list lines up with. */
    align?: "start" | "end";
    nojs?: boolean;
    class?: string;
  };
  let { trigger, label, items, align = "end", nojs = false, class: className }: Props = $props();
  const id = $props.id();
  const button = $derived({
    ...triggerButtonProps(trigger),
    iconEnd: trigger.iconOnly === true ? undefined : ChevronDown,
  });
</script>

{#snippet itemContent(
  item: Extract<MenuItem, { label: string }>,
  checked = item.kind !== "checkbox" && item.current === true,
)}
  {#if item.kind !== "checkbox" && item.icon}<item.icon
      class="size-4 shrink-0 text-fg-muted"
      aria-hidden="true"
    />{/if}
  <span class="flex-1">{item.label}</span>
  {#if checked}<Check class="size-4 shrink-0" aria-hidden="true" />{/if}
{/snippet}

{#if nojs}
  <div class={cn("group w-fit", className)}>
    <Button {...button} popovertarget={id}>{trigger.label}</Button>
    <!-- The popover follows its button directly, so browsers add no implicit aria-details. -->
    <ul
      {id}
      popover="auto"
      aria-label={label}
      data-align={align}
      class={cn(
        floatingPanelClass,
        "max-h-[60vh] min-w-44 overflow-auto p-1",
        align === "start" ? "dropdown-start" : "dropdown-end",
      )}
    >
      {#each items as item, index (index)}
        {#if item.kind === "separator"}
          <li role="separator" class="my-1 h-px bg-line"></li>
        {:else if item.kind !== "checkbox" && item.href !== undefined}
          <li>
            <a
              href={item.href}
              lang={item.lang}
              hreflang={item.lang}
              aria-current={item.current === true ? "true" : undefined}
              class={cn(menuItemClass, item.danger === true && "text-danger")}
              >{@render itemContent(item)}</a
            >
          </li>
        {:else if item.kind !== "checkbox" && item.action !== undefined}
          <li>
            <form method="post" action={item.action}>
              <button
                type="submit"
                disabled={item.disabled}
                class={cn(menuItemClass, item.danger === true && "text-danger")}
                >{@render itemContent(item)}</button
              >
            </form>
          </li>
        {/if}
      {/each}
    </ul>
  </div>
{:else}
  <DropdownMenu.Root>
    <DropdownMenu.Trigger>
      {#snippet child({ props })}<Button {...button} {...props} class={className}
          >{trigger.label}</Button
        >{/snippet}
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        {align}
        sideOffset={6}
        collisionPadding={16}
        aria-label={label}
        class={cn(
          floatingPanelClass,
          "max-h-(--bits-dropdown-menu-content-available-height) min-w-44 overflow-y-auto p-1",
        )}
      >
        {#each items as item, index (index)}
          {#if item.kind === "separator"}
            <DropdownMenu.Separator class="my-1 h-px bg-line" />
          {:else if item.kind === "checkbox"}
            <DropdownMenu.CheckboxItem
              checked={item.checked}
              onCheckedChange={item.onCheckedChange}
              disabled={item.disabled}
              closeOnSelect={false}
              class={menuItemClass}
            >
              {#snippet children({ checked })}{@render itemContent(item, checked)}{/snippet}
            </DropdownMenu.CheckboxItem>
          {:else}
            {@const tone = cn(menuItemClass, item.danger === true && "text-danger")}
            <DropdownMenu.Item disabled={item.disabled} onSelect={item.onSelect}>
              {#snippet child({ props })}
                {#if item.href !== undefined}
                  <a
                    {...props}
                    href={item.href}
                    lang={item.lang}
                    hreflang={item.lang}
                    aria-current={item.current === true ? "true" : undefined}
                    class={tone}>{@render itemContent(item)}</a
                  >
                {:else if item.action !== undefined}
                  <form method="post" action={item.action} class="contents">
                    <button {...props} type="submit" class={tone}
                      >{@render itemContent(item)}</button
                    >
                  </form>
                {:else}
                  <div {...props} class={tone}>{@render itemContent(item)}</div>
                {/if}
              {/snippet}
            </DropdownMenu.Item>
          {/if}
        {/each}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>
{/if}
