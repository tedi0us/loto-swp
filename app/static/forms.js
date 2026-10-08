(function () {
  'use strict';

  // Dirty-track autosave forms and warn before leaving with unsaved changes
  var form = document.querySelector('form[hx-post]');
  if (form) {
    var dirty = false;
    form.addEventListener('change', function () { dirty = true; });
    document.addEventListener('htmx:afterRequest', function () { dirty = false; });
    window.addEventListener('beforeunload', function (e) {
      if (dirty) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  // LOTO historical-record toggle (SWP uses CSS :has() instead)
  var historicalCb     = document.getElementById('is_historical');
  var historicalFields = document.getElementById('historical-fields');
  if (historicalCb && historicalFields) {
    function toggleHistorical() {
      historicalFields.style.display = historicalCb.checked ? '' : 'none';
    }
    toggleHistorical();
    historicalCb.addEventListener('change', toggleHistorical);
  }
})();
