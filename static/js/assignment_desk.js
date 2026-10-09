/* Presentation enhancement only. Workload previews and saves use app.js and Django. */
(() => {
  const desk = document.querySelector('[data-assignment-desk]');
  if (!desk) return;
  const mobile = window.matchMedia('(max-width: 650px)');
  const links = [...desk.querySelectorAll('[data-subject-link]')];
  const panels = [...desk.querySelectorAll('[data-assignment-panel]')];
  const search = desk.querySelector('[data-subject-search]');
  const filters = [...desk.querySelectorAll('[data-subject-filter]')];
  let active = null;
  let filter = 'all';
  const choose = (id, focus = false) => {
    const panel = panels.find(item => item.id === id);
    if (!panel) return;
    active = panel;
    panels.forEach(item => { item.hidden = item !== panel; });
    links.forEach(link => {
      if (link.dataset.subjectId === id) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
    desk.classList.add('has-subject');
    if (focus) {
      panel.focus({preventScroll: true});
      if (mobile.matches) panel.scrollIntoView({block: 'start'});
    }
  };
  const back = () => {
    desk.classList.remove('has-subject');
    // A direct link may select a subject excluded by an active search. Restore
    // the complete list so keyboard focus always has a visible destination.
    search.value = '';
    filter = 'all';
    filterSubjects();
    links.find(link => link.dataset.subjectId === active?.id)?.focus();
    history.replaceState(null, '', location.pathname + location.search);
  };
  const filterSubjects = () => {
    const query = search.value.trim().toLocaleLowerCase();
    links.forEach(link => {
      link.hidden = !link.textContent.toLocaleLowerCase().includes(query) ||
        (filter === 'needs' && link.dataset.assignmentState === 'full');
    });
    const count = links.filter(link => !link.hidden).length;
    desk.querySelector('[data-subject-empty]').hidden = count > 0;
    desk.querySelector('[data-subject-results]').textContent = `${count} matching subjects`;
    filters.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.subjectFilter === filter)));
  };
  links.forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    choose(link.dataset.subjectId, true);
    history.replaceState(null, '', location.pathname + location.search + link.hash);
  }));
  filters.forEach(button => button.addEventListener('click', () => {
    filter = button.dataset.subjectFilter;
    filterSubjects();
  }));
  search.addEventListener('input', filterSubjects);
  desk.querySelector('[data-subject-tools]').hidden = false;
  desk.querySelectorAll('[data-subject-back]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', back);
  });
  desk.querySelectorAll('[data-desk-picker]').forEach(picker => {
    const form = picker.closest('form');
    const faculty = form.elements.namedItem('faculty');
    const summary = picker.querySelector('summary');
    const selected = picker.querySelector('[data-selected-faculty]');
    const choices = [...picker.querySelectorAll('[data-instructor-choice]')];
    const sync = () => {
      choices.forEach(choice => { choice.checked = choice.value === faculty.value; });
      const label = faculty.selectedOptions[0]?.textContent.trim();
      selected.textContent = faculty.value ? `Selected: ${label}` : 'Choose an instructor';
    };
    choices.forEach(choice => choice.addEventListener('change', () => {
      if (!choice.checked) return;
      faculty.value = choice.value;
      faculty.dispatchEvent(new Event('change', {bubbles: true}));
      picker.open = false;
      summary.focus();
    }));
    faculty.addEventListener('change', sync);
    const clear = form.querySelector('[data-clear-instructor]');
    clear.hidden = false;
    clear.addEventListener('click', () => {
      faculty.value = '';
      faculty.dispatchEvent(new Event('change', {bubbles: true}));
      summary.focus();
    });
    sync();
    form.querySelector('[data-native-faculty]').hidden = true;
    picker.hidden = false;
  });
  desk.classList.add('is-enhanced');
  const initial = panels.find(panel => panel.id === location.hash.slice(1)) ||
    panels.find(panel => panel.hasAttribute('data-submitted'));
  if (initial) choose(initial.id);
  else {
    choose(panels[0].id);
    if (mobile.matches) desk.classList.remove('has-subject');
  }
  window.addEventListener('hashchange', () => {
    if (location.hash) choose(location.hash.slice(1));
    else if (mobile.matches) back();
  });
  window.addEventListener('pageshow', event => {
    // A restored page must obtain a fresh server preview before another save.
    if (event.persisted) desk.querySelectorAll('[data-desk-form]').forEach(form =>
      form.elements.namedItem('faculty').dispatchEvent(new Event('change', {bubbles: true})));
  });
})();
