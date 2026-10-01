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
    button.title = collapsed ? 'Expand navigation' : 'Collapse navigation';
    button.querySelector('.visually-hidden').textContent = button.title;
    button.querySelector('[aria-hidden]').textContent = collapsed ? '⇥' : '⇤';
    saveCollapsed(collapsed);
  };
  groups.forEach(group => { group.dataset.navLabel = group.querySelector('.nav-group-label').textContent.trim(); });
  if (readCollapsed()) setCollapsed(true);
  button.addEventListener('click', () => setCollapsed(!document.body.classList.contains('sidebar-collapsed')));
})();
