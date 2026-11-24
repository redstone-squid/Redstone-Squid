<script lang="ts">
  import { tick } from "svelte";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { inputClass } from "./styles";

  /**
   * A one-time code, such as a sign-in or merge code. It is one input, so paste works and
   * screen readers read one field. Typing uppercases letters, drops anything outside
   * `charset`, and shows the code in `groups` joined by hyphens ("ABCD-EFGH"); `value` holds
   * the bare code. A form without JavaScript submits what was typed, so the server strips
   * separators too. `autoSubmit` submits the form once the code is complete.
   */
  type Props = Omit<FieldMessages, "width" | "labelVisibility"> & {
    name: string;
    length: number;
    groups?: number[];
    charset?: "alnum" | "digits";
    value?: string;
    autoSubmit?: boolean;
    required?: boolean;
    disabled?: boolean;
    class?: string;
  };
  let {
    name,
    length,
    groups = [length],
    charset = "alnum",
    value = $bindable(""),
    autoSubmit = false,
    required,
    disabled,
    ...frame
  }: Props = $props();
  let input: HTMLInputElement | undefined = $state();
  const strip = (raw: string) =>
    raw
      .toUpperCase()
      .replace(charset === "digits" ? /[^0-9]/g : /[^0-9A-Z]/g, "")
      .slice(0, length);

  function group(code: string): string {
    const parts: string[] = [];
    let start = 0;
    for (const size of groups) {
      if (start >= code.length) break;
      parts.push(code.slice(start, start + size));
      start += size;
    }
    if (start < code.length) parts.push(code.slice(start));
    return parts.join("-");
  }

  async function changed(event: Event & { currentTarget: HTMLInputElement }): Promise<void> {
    const target = event.currentTarget;
    // Keep the caret after the same character once hyphens are added or removed.
    const before = strip(target.value.slice(0, target.selectionStart ?? target.value.length));
    const wasIncomplete = value.length < length;
    value = strip(target.value);
    target.value = group(value);
    const caret = group(before).length;
    target.setSelectionRange(caret, caret);
    if (autoSubmit && wasIncomplete && value.length === length) {
      await tick();
      input?.form?.requestSubmit();
    }
  }
</script>

<FieldFrame {...frame}>
  {#snippet children(attributes)}<input
      {...attributes}
      bind:this={input}
      {name}
      {required}
      {disabled}
      value={group(value)}
      oninput={changed}
      autocomplete="one-time-code"
      autocapitalize="characters"
      spellcheck="false"
      inputmode={charset === "digits" ? "numeric" : "text"}
      class={cn(inputClass, "font-mono text-lg tracking-widest uppercase")}
      size={length + groups.length}
    />{/snippet}
</FieldFrame>
