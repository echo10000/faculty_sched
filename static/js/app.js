(() => {
  const back = document.getElementById('page-back');
  if (!back || !document.referrer) return;
  try {
    const previous = new URL(document.referrer);
    const current = new URL(window.location.href);
    const previousPath = decodeURIComponent(previous.pathname);
    const isAccountTransition = /\/(?:login|logout)\/?$/i.test(previousPath);
    if (previous.origin !== current.origin || previous.username || previous.password || isAccountTransition ||
        (previous.pathname === current.pathname && previous.search === current.search)) return;
    // A normal GET link avoids resubmitting a previous form from browser history.
    back.href = previous.href;
    back.title = 'Return to previous page';
  } catch { /* The dashboard link remains usable without a valid same-site referrer. */ }
})();

(() => {
  const button = document.getElementById('sidebar-collapse');
  const groups = [...document.querySelectorAll('[data-nav-group]')];
  if (!button) return;

  const storageKey = 'campusload.sidebar.collapsed';
  const readCollapsed = () => {
    try { return localStorage.getItem(storageKey) === 'true'; }
    catch { return false; }
  };
  const saveCollapsed = (value) => {
    try { localStorage.setItem(storageKey, String(value)); }
    catch { /* Navigation still works when storage is unavailable. */ }
  };
  let expandedGroups = [];
  const setCollapsed = (collapsed) => {
    if (collapsed) {
      expandedGroups = groups.filter(group => group.open).map(group => group.dataset.navLabel);
      groups.forEach(group => { group.open = true; });
    } else {
      groups.forEach(group => {
        group.open = expandedGroups.includes(group.dataset.navLabel) || !!group.querySelector('.nav-link.active');
      });
    }
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    button.setAttribute('aria-expanded', String(!collapsed));
    button.title = collapsed ? 'Show sidebar' : 'Hide sidebar';
    button.querySelector('.visually-hidden').textContent = button.title;
    button.querySelector('[aria-hidden]').textContent = collapsed ? '⇥' : '⇤';
    button.querySelector('.sidebar-toggle-label').textContent = collapsed ? 'Show sidebar' : 'Hide sidebar';
    saveCollapsed(collapsed);
  };
  groups.forEach(group => { group.dataset.navLabel = group.querySelector('.nav-group-label').textContent.trim(); });
  if (readCollapsed()) setCollapsed(true);
  button.addEventListener('click', () => setCollapsed(!document.body.classList.contains('sidebar-collapsed')));
})();

(() => {
  document.querySelectorAll('[data-section-selection]').forEach(form => {
    ['academic_term', 'department', 'year_level'].forEach(name => {
      const input = form.elements.namedItem(name);
      if (input && input.type !== 'hidden') input.addEventListener('change', () => {
        form.elements.namedItem('section').value = '';
        form.requestSubmit();
      });
    });
    form.elements.namedItem('section').addEventListener('change', () => form.requestSubmit());
  });
  document.querySelectorAll('[data-assignment-preview]').forEach(form => {
    const output = form.querySelector('[data-preview-result]');
    const faculty = form.elements.namedItem('faculty');
    let controller;
    const formatLoad = value => value == null ? 'Not configured' :
      Number(value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const preview = async () => {
      if (controller) controller.abort();
      if (!faculty.value) { output.textContent = ''; return; }
      controller = new AbortController();
      const active = controller;
      output.textContent = 'Checking workload…';
      const data = new FormData(form);
      data.set('intent', 'preview');
      try {
        const response = await fetch(form.action, {method: 'POST', body: data,
          headers: {'Accept': 'application/json'}, signal: active.signal});
        if (!response.ok) throw new Error('Request failed');
        const result = await response.json();
        if (controller !== active) return;
        const errors = Object.values(result.errors).flat().map(error => error.message);
        const report = result.preview;
        output.classList.toggle('text-danger', errors.length > 0);
        output.textContent = errors.length ? errors.join(' ') : report ?
          `After assignment: ${formatLoad(report.assigned_load)} / ${formatLoad(report.maximum_load)} workload units · ${report.status}. ${report.warnings.join(' ')} Preview only; nothing saved.` :
          'Select a valid instructor and teaching share.';
      } catch (error) {
        if (error.name !== 'AbortError' && controller === active)
          output.textContent = 'Could not check workload. Try Check workload again. Saving will recheck the latest limits.';
      }
    };
    faculty.addEventListener('change', preview);
    form.elements.namedItem('share').addEventListener('change', preview);
    form.addEventListener('submit', event => {
      if (event.submitter?.value === 'preview') { event.preventDefault(); preview(); }
      else if (controller) controller.abort();
    });
  });
})();

(() => {
  document.querySelectorAll('[data-generation-submit]').forEach(form => {
    form.addEventListener('submit', event => {
      const button = event.submitter;
      if (!button) return;
      button.dataset.generationLabel = button.textContent;
      button.disabled = true;
      button.textContent = 'Generating proposal…';
      form.setAttribute('aria-busy', 'true');
    });
  });
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('[data-generation-submit] [data-generation-label]').forEach(button => {
      button.disabled = false;
      button.textContent = button.dataset.generationLabel;
      button.form.removeAttribute('aria-busy');
    });
  });
  document.querySelectorAll('[data-prepare-context]').forEach(form => {
    const schedule = form.elements.namedItem('schedule');
    ['academic_term', 'department'].forEach(name => {
      form.elements.namedItem(name).addEventListener('change', () => {
        schedule.value = '';
        form.requestSubmit();
      });
    });
  });
  document.querySelectorAll('[data-schedule-context]').forEach(input => {
    const term = input.form.querySelector('[name="academic_term"]');
    if (term) term.addEventListener('change', () => {
      input.disabled = term.value !== input.dataset.term;
    });
  });
  document.querySelectorAll('[data-faculty-picker]').forEach(picker => {
    const search = picker.querySelector('[data-faculty-search]');
    const options = [...picker.querySelectorAll('[data-faculty-option]')];
    const empty = picker.querySelector('[data-faculty-empty]');
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      options.forEach(option => {
        option.hidden = !option.textContent.toLocaleLowerCase().includes(query);
      });
      empty.hidden = options.some(option => !option.hidden);
    });
  });
})();
