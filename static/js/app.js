(() => {
  const context = document.querySelector('.workbench-context');
  if (!context) return;
  context.addEventListener('keydown', event => {
    if (event.key === 'Escape' && context.open) {
      context.open = false;
      context.querySelector('summary').focus();
    }
  });
  document.addEventListener('click', event => {
    if (!context.contains(event.target)) context.open = false;
  });
})();

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
  const desktop = window.matchMedia('(min-width: 992px)');
  let collapsed = readCollapsed();
  const renderCollapsed = () => {
    const compact = desktop.matches && collapsed;
    // Keep disclosure state intact; compact mode hides only the child links.
    document.body.classList.toggle('sidebar-collapsed', compact);
    button.setAttribute('aria-expanded', String(!compact));
    button.title = compact ? 'Show sidebar' : 'Hide sidebar';
    button.querySelector('.visually-hidden').textContent = button.title;
    button.querySelector('[aria-hidden]').textContent = compact ? '⇥' : '⇤';
    button.querySelector('.sidebar-toggle-label').textContent = button.title;
  };
  renderCollapsed();
  desktop.addEventListener('change', renderCollapsed);
  window.addEventListener('pageshow', () => {
    collapsed = readCollapsed();
    renderCollapsed();
  });
  button.addEventListener('click', () => {
    collapsed = !collapsed;
    saveCollapsed(collapsed);
    renderCollapsed();
  });
  groups.forEach(group => {
    group.querySelector('summary').addEventListener('click', event => {
      if (!desktop.matches || !collapsed) return;
      event.preventDefault();
      collapsed = false;
      saveCollapsed(false);
      renderCollapsed();
      group.open = true;
    });
  });
})();

(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('navigation-toggle');
  if (!sidebar || !toggle) return;
  const syncToggle = expanded => {
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Close navigation' : 'Open navigation');
  };
  sidebar.addEventListener('shown.bs.offcanvas', () => syncToggle(true));
  sidebar.addEventListener('hidden.bs.offcanvas', () => syncToggle(false));
  sidebar.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => {
      if (!window.matchMedia('(min-width: 992px)').matches)
        bootstrap.Offcanvas.getInstance(sidebar)?.hide();
    });
  });
  // Clear the drawer on restored history pages, including its backdrop/scroll lock.
  window.addEventListener('pageshow', () => {
    bootstrap.Offcanvas.getInstance(sidebar)?.hide();
    syncToggle(false);
  });
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
        if (errors.length) output.textContent = errors.join(' ');
        else if (report?.summary_html) {
          // This fragment comes from our autoescaped Django summary template.
          // Never interpolate form values or raw error messages into HTML.
          output.innerHTML = report.summary_html;
          const notice = document.createElement('p');
          notice.className = 'text-secondary mt-2';
          notice.textContent = 'Preview only; nothing saved. Saving rechecks the latest limits.';
          output.append(notice);
        } else output.textContent = report ?
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
