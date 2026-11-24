// Without anchor positioning the popover opens centered in the viewport, detached from its
// button. Place it below the button instead, flipping above and clamping to the viewport as
// the dropdown utilities do.
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
  for (const menu of document.querySelectorAll<HTMLElement>("[popover][data-align]")) {
    menu.addEventListener("toggle", (event) => {
      if (event.newState === "open") place(menu);
    });
  }
}
