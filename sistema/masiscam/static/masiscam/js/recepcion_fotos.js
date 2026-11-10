document.querySelectorAll("[data-foto-selector]").forEach((container) => {
  const select = container.querySelector("select");
  const gallery = container.querySelector(".mf-photo-gallery");
  const endpoint = container.dataset.previewEndpoint;
  if (!select || !gallery || !endpoint) return;

  const choices = [];
  for (const option of select.options) {
    if (!option.value) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mf-photo-choice";
    button.dataset.value = option.value;
    button.setAttribute("aria-pressed", "false");

    const image = document.createElement("img");
    image.src = `${endpoint}?token=${encodeURIComponent(option.value)}`;
    image.alt = "";
    image.loading = "lazy";
    button.append(image);

    const label = document.createElement("span");
    label.textContent = option.textContent;
    button.append(label);
    button.addEventListener("click", () => {
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    gallery.append(button);
    choices.push(button);
  }

  select.addEventListener("change", () => {
    for (const button of choices) {
      button.setAttribute("aria-pressed", String(button.dataset.value === select.value));
    }
  });
  select.dispatchEvent(new Event("change"));
});
