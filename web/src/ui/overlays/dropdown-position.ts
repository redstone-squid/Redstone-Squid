// Places Menu's `nojs` popovers in browsers without anchor positioning, where a popover opens
// centered in the viewport, detached from its button. It goes below the button instead,
// flipping above and clamping to the viewport as the dropdown utilities do. DocumentHead loads
// it on every page.
const gap = 6;
function place(menu: HTMLElement): void {
  const button = menu.previousElementSibling?.getBoundingClientRect();
  if (button === undefined) return;
  const { width, height } = menu.getBoundingClientRect();
  const left = menu.dataset.align === "start" ? button.left : button.right - width;
  const below = button.bottom + gap;
  const top = below + height > innerHeight ? button.top - gap - height : below;
  menu.style.margin = "0";
  menu.style.inset = `${Math.max(0, top)}px auto auto ${Math.min(Math.max(0, left), innerWidth - width)}px`;
}
if (!CSS.supports("position-area", "block-end")) {
  // `toggle` does not bubble, so listen in the capture phase; this also reaches menus that
  // islands render after the script runs.
  document.addEventListener(
    "toggle",
    (event) => {
      const menu = event.target;
      if (
        menu instanceof HTMLElement &&
        menu.matches("[popover][data-align]") &&
        event.newState === "open"
      )
        place(menu);
    },
    true,
  );
}
