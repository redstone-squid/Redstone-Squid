const root = document.querySelector("[data-header-search]");
const panel = root?.querySelector<HTMLElement>("[popover]");
const input = panel?.querySelector("input[type=search]");
const trigger = root?.querySelector("button");
const hint = panel?.querySelector("kbd");
if (panel && input instanceof HTMLInputElement && trigger && hint) {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  hint.textContent = mac ? "⌘ K" : "Ctrl K";
  hint.hidden = false;
  panel.addEventListener("beforetoggle", () => {
    const header = root?.closest("header");
    if (header && trigger.getClientRects().length > 0)
      panel.style.top = `${header.getBoundingClientRect().bottom}px`;
  });
  panel.addEventListener("toggle", () => {
    if (panel.matches(":popover-open")) input.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.defaultPrevented ||
      event.isComposing ||
      event.key.toLowerCase() !== "k" ||
      !(mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey) ||
      event.shiftKey ||
      event.altKey ||
      (event.target instanceof HTMLElement &&
        event.target !== input &&
        (event.target.isContentEditable ||
          event.target.closest("input, textarea, select, [role=textbox]")))
    )
      return;
    event.preventDefault();
    if (trigger.getClientRects().length > 0 && !panel.matches(":popover-open")) panel.showPopover();
    input.focus();
    input.select();
  });
}
