(() => {
  const initialize = () => {
    document.querySelectorAll('[data-load-control]').forEach(control => {
      if (control.dataset.initialized) return;
      control.dataset.initialized = 'true';
      const exact = control.querySelector('input[type="number"]');
      const slider = control.querySelector('[data-load-slider]');
      const output = control.querySelector('[data-load-value]');
      const synchronize = () => {
        const value = exact.valueAsNumber;
        const valid = exact.value !== '' && exact.validity.valid && Number.isFinite(value);
        // Keep an expanded range stable while dragging or using arrow keys.
        slider.max = String(valid ? Math.min(Number(exact.max), Math.max(30, Number(slider.max), Math.ceil(value))) : 30);
        slider.value = valid ? exact.value : '0';
        slider.disabled = exact.disabled || exact.readOnly;
        const description = exact.value === '' ? 'Inherited value; move slider to set a value.' :
          valid ? `${exact.value} weighted workload units` : 'Correct the exact value above.';
        slider.setAttribute('aria-valuetext', description);
        output.textContent = `Helper range: 0–${slider.max}. ${description}`;
      };
      exact.addEventListener('input', synchronize);
      exact.addEventListener('change', synchronize);
      slider.addEventListener('input', () => {
        exact.value = slider.value;
        exact.dispatchEvent(new Event('input', {bubbles: true}));
      });
      exact.form?.addEventListener('reset', () => setTimeout(synchronize, 0));
      synchronize();
      control.querySelector('[data-load-helper]').hidden = false;
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
  else initialize();
})();
