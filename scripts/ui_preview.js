const preview = document.querySelector("main");
const feedback = document.querySelector('[role="status"]');
let pending = false;

preview.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-squid-action]");
  if (!button || button.disabled || pending) return;
  pending = true;
  feedback.textContent = "";
  try {
    const response = await fetch(`/preview/${preview.dataset.session}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: button.dataset.squidAction,
        generation: Number(preview.dataset.generation),
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    preview.innerHTML = await response.text();
    preview.dataset.generation = response.headers.get("X-Generation");
    Array.from(preview.querySelectorAll("button")).find(
      (next) => next.dataset.squidAction === button.dataset.squidAction,
    )?.focus();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    pending = false;
  }
});
