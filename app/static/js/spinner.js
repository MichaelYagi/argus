'use strict';
(function () {
  const style = document.createElement('style');
  style.textContent = `
@keyframes btn-spin { to { transform: rotate(360deg); } }
.btn-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: btn-spin .6s linear infinite;
  vertical-align: middle;
}`;
  document.head.appendChild(style);

  // withSpinner(btn, asyncFn)
  // Shows a CSS spinner on btn while asyncFn runs, then restores the original
  // innerHTML and re-enables the button.  If the button is no longer in the DOM
  // when asyncFn settles (e.g. the containing card was removed on success) the
  // restore is skipped.  Passing null for btn runs asyncFn directly — used by
  // keyboard shortcuts that have no button reference.
  window.withSpinner = async function (btn, asyncFn) {
    if (!btn) return asyncFn();
    const savedHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-spinner"></span>';
    try {
      await asyncFn();
    } finally {
      if (document.contains(btn)) {
        btn.innerHTML = savedHtml;
        btn.disabled = false;
      }
    }
  };
})();
