(() => {
  "use strict";
  const workspace = document.querySelector("[data-workload-monitor]");
  if (!workspace) return;
  const panels = Array.from(workspace.querySelectorAll("[data-workload-panel]"));
  const links = Array.from(workspace.querySelectorAll("[data-workload-faculty]"));
  const roster = workspace.querySelector("#faculty-roster");
  const mobile = window.matchMedia("(max-width: 650px)");
  const narrow = window.matchMedia("(max-width: 1199px)");
  let lastFacultyLink = null;
  const focusAndReveal = (element) => {
    element.focus({ preventScroll: true });
    if (narrow.matches) element.scrollIntoView({ block: "start", behavior: "auto" });
  };
  const selectPanel = (facultyId) => {
    const selected = panels.find((panel) => panel.dataset.workloadPanel === facultyId);
    if (!selected) return null;
    panels.forEach((panel) => { panel.hidden = panel !== selected; });
    links.forEach((link) => {
      if (link.dataset.workloadFaculty === facultyId) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
    workspace.querySelectorAll("[data-workload-row]").forEach((row) => {
      row.classList.toggle("is-selected", row.dataset.workloadRow === facultyId);
    });
    return selected;
  };
  workspace.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const facultyLink = event.target.closest("[data-workload-faculty]");
    if (facultyLink) {
      const selected = selectPanel(facultyLink.dataset.workloadFaculty);
      if (!selected) return;
      event.preventDefault();
      lastFacultyLink = facultyLink;
      workspace.classList.add("mobile-detail");
      window.history.pushState({}, "", facultyLink.href);
      focusAndReveal(selected);
      return;
    }
    const back = event.target.closest("[data-workload-back]");
    if (!back) return;
    event.preventDefault();
    workspace.classList.remove("mobile-detail");
    window.history.pushState({}, "", back.href);
    const target = lastFacultyLink || workspace.querySelector(".faculty-name[aria-current]") || roster;
    focusAndReveal(target);
  });
  window.addEventListener("popstate", () => {
    const url = new URL(window.location.href);
    const facultyId = url.searchParams.get("selected_faculty");
    const selected = facultyId ? selectPanel(facultyId) : null;
    workspace.classList.toggle("mobile-detail", Boolean(selected));
    if (selected) focusAndReveal(selected);
    else focusAndReveal(roster);
  });
  // A selected-faculty GET request must also reveal its detail on a phone.
  if (mobile.matches && workspace.classList.contains("mobile-detail")) {
    const selected = panels.find((panel) => !panel.hidden);
    if (selected) focusAndReveal(selected);
  }
})();
