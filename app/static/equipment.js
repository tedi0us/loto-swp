(function () {
  'use strict';

  var _dialog = null;
  var _body   = null;

  function _justClose() {
    if (_dialog) _dialog.close();
  }

  function _closeAndSave() {
    _justClose();
    var wl = document.getElementById('equip-work-log-fields');
    if (wl) wl.style.display = '';
    // Delay so HTMX finishes swapping the equip_id input before we trigger autosave
    setTimeout(function () {
      var form = document.querySelector('form[hx-post]');
      if (form) htmx.trigger(form, 'change');
    }, 80);
  }

  function initTagPreview() {
    var pn      = document.getElementById('equip-process-name');
    var preview = document.getElementById('equip-tag-preview');
    var plant   = document.querySelector('.equip-plant-sel');
    if (!pn || !preview) return;
    function update() {
      var p    = plant ? plant.value : '';
      var slug = pn.value.toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-+|-+$/g, '');
      preview.value = (p && slug) ? p + '-' + slug : '';
    }
    pn.addEventListener('input', update);
    if (plant) plant.addEventListener('change', update);
    update();
  }

  document.addEventListener('DOMContentLoaded', function () {
    _dialog = document.getElementById('equipment-dialog');
    _body   = document.getElementById('equipment-dialog-body');
    if (!_dialog) return;

    if (_body) {
      _body.addEventListener('htmx:afterSwap', function () { _dialog.showModal(); });
    }

    // Server signals equipment was selected/created → close + trigger autosave
    document.body.addEventListener('closeEquipmentDialog', _closeAndSave);

    // Backdrop click → just close, no autosave
    _dialog.addEventListener('click', function (e) {
      if (e.target === _dialog) _justClose();
    });

    // ✕ button — delegate from document so it works regardless of dialog internals
    document.addEventListener('click', function (e) {
      if (e.target.closest('.equip-dialog-close-btn')) _justClose();
    });
  });

  document.addEventListener('htmx:afterSettle', function (e) {
    var t = e.detail && e.detail.target;
    if (t && t.id === 'equipment-dialog-body') {
      initTagPreview();
    }
  });
})();
