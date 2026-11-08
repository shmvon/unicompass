function countWords(str) {
  if (!str || !str.trim()) return 0;
  return str.trim().split(/\s+/).filter(function (w) { return w.length > 0; }).length;
}

function handlePopupContentInput(textarea) {
  var max = parseInt(textarea.dataset.maxwords || '40');
  var val = textarea.value;
  var words = val.trim().split(/\s+/).filter(function (w) { return w.length > 0; });
  var count = (words.length === 1 && words[0] === "") ? 0 : words.length;
  var display = document.getElementById('popupWordCountDisplay');

  if (count > max) {
    var truncated = words.slice(0, max).join(' ');
    textarea.value = truncated;
    count = max;
    if (display) {
      display.textContent = max + ' / ' + max + ' words (limit reached)';
      display.style.color = '#c00';
    }
  } else {
    if (display) {
      display.textContent = count + ' / ' + max + ' words';
      display.style.color = count >= Math.floor(max * 0.9) ? '#d97706' : '#666';
    }
  }
}

var activePopupField = null;
var activePopupMaxWords = 40;
var popupFieldLabels = {
  'comment': 'Comments',
  'other_changes': 'Other aspects',
  'outcome_reporting': 'Qualitative reporting'
};

function openPopup(fieldId, maxWords) {
  activePopupField = fieldId;
  activePopupMaxWords = maxWords || 40;
  var parts = fieldId.split('_');
  var rowNum = parseInt(parts.pop()) + 1;
  var fieldKey = parts.join('_');
  var label = popupFieldLabels[fieldKey] || fieldKey;
  document.getElementById('popupHeader').textContent = 'Edit ' + label + (isNaN(rowNum) ? '' : ' (#' + rowNum + ')');
  var titleEl = document.getElementById('hiddenTitle_' + fieldId);
  var contentEl = document.getElementById('hiddenContent_' + fieldId);
  var title = titleEl ? titleEl.value : '';
  var content = contentEl ? contentEl.value : '';
  document.getElementById('popupTitleInput').value = title;
  var textarea = document.getElementById('popupContentInput');
  textarea.value = content;
  textarea.dataset.maxwords = activePopupMaxWords;
  handlePopupContentInput(textarea);
  document.getElementById('popupBackdrop').style.display = 'flex';
  setTimeout(function () { document.getElementById('popupTitleInput').focus(); }, 150);
}

function closePopup() {
  document.getElementById('popupBackdrop').style.display = 'none';
  activePopupField = null;
}

function savePopup() {
  if (!activePopupField) return;
  var title = document.getElementById('popupTitleInput').value.trim();
  var textarea = document.getElementById('popupContentInput');
  var content = textarea.value.trim();
  var max = activePopupMaxWords || 40;
  var words = content.split(/\s+/).filter(function (w) { return w.length > 0; });
  if (words.length > max) {
    content = words.slice(0, max).join(' ');
    words = words.slice(0, max);
  }
  var count = (words.length === 1 && words[0] === "") ? 0 : words.length;

  var titleEl = document.getElementById('hiddenTitle_' + activePopupField);
  var contentEl = document.getElementById('hiddenContent_' + activePopupField);
  if (titleEl) titleEl.value = title;
  if (contentEl) contentEl.value = content;

  var displayEl = document.getElementById('popupTitle_' + activePopupField);
  var btnEl = document.getElementById('popupBtn_' + activePopupField) || (displayEl ? displayEl.closest('.report-btn') : null);
  if (displayEl) {
    if (title) {
      var shortTitle = title.length > 20 ? (title.substring(0, 20) + '…') : title;
      displayEl.textContent = shortTitle;
    } else if (count > 0) {
      displayEl.innerHTML = '<strong>' + count + '</strong> word' + (count !== 1 ? 's' : '');
    } else {
      displayEl.innerHTML = '<span style="opacity:0.75;">no input</span>';
    }
  }
  if (btnEl) {
    var tooltip = content ? (title ? (title + '\n\n' + content) : content) : title;
    if (tooltip) {
      btnEl.setAttribute('data-def', tooltip);
    } else {
      btnEl.removeAttribute('data-def');
    }
  }
  closePopup();
  if (typeof autoSaveCurrentForm === 'function') {
    autoSaveCurrentForm();
  }
}

document.getElementById('popupBackdrop').addEventListener('click', function (e) {
  if (e.target === this) closePopup();
});

function toggleTopbarDropdown(menuId, event) {
  if (event) event.stopPropagation();
  var allMenus = document.querySelectorAll('.topbar-menu');
  allMenus.forEach(function (m) {
    if (m.id !== menuId) m.classList.remove('show');
  });
  var target = document.getElementById(menuId);
  if (target) target.classList.toggle('show');
}

function toggleMobileNav(e) {
  if (e) e.stopPropagation();
  var links = document.getElementById('navLinks');
  var topbarRight = document.getElementById('topbarRight');
  var navBtn = document.getElementById('mobileNavToggle');
  var ctxBtn = document.getElementById('mobileContextToggle');

  if (topbarRight) {
    topbarRight.classList.remove('mobile-open');
    if (ctxBtn) ctxBtn.setAttribute('aria-expanded', 'false');
  }

  if (links) {
    var isOpen = links.classList.toggle('mobile-open');
    if (navBtn) navBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  }
}

function toggleMobileContext(e) {
  if (e) e.stopPropagation();
  var topbarRight = document.getElementById('topbarRight');
  var links = document.getElementById('navLinks');
  var navBtn = document.getElementById('mobileNavToggle');
  var ctxBtn = document.getElementById('mobileContextToggle');

  if (links) {
    links.classList.remove('mobile-open');
    if (navBtn) navBtn.setAttribute('aria-expanded', 'false');
  }

  if (topbarRight) {
    var isOpen = topbarRight.classList.toggle('mobile-open');
    if (ctxBtn) ctxBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  }
}

document.addEventListener('click', function (e) {
  if (!e.target.closest('#mainNav')) {
    var links = document.getElementById('navLinks');
    if (links) links.classList.remove('mobile-open');
    var navBtn = document.getElementById('mobileNavToggle');
    if (navBtn) navBtn.setAttribute('aria-expanded', 'false');
  }
  if (!e.target.closest('#topbarIdentity')) {
    var topbarRight = document.getElementById('topbarRight');
    if (topbarRight) topbarRight.classList.remove('mobile-open');
    var ctxBtn = document.getElementById('mobileContextToggle');
    if (ctxBtn) ctxBtn.setAttribute('aria-expanded', 'false');
  }
  if (!e.target.closest('.topbar-dropdown')) {
    document.querySelectorAll('.topbar-menu').forEach(function (m) {
      m.classList.remove('show');
    });
  }
});

function validateDecimalInput(el, allowNegative) {
  if (!el) return;
  var v = el.value.replace(/,/g, '.').replace(allowNegative ? /[^\d.-]/g : /[^\d.]/g, '');
  var isNeg = allowNegative && v.startsWith('-');
  v = v.replace(/-/g, '');
  if (isNeg) v = '-' + v;
  var parts = v.split('.');
  if (parts.length > 2) {
    v = parts[0] + '.' + parts.slice(1).join('');
  }
  parts = v.split('.');
  if (parts.length === 2 && parts[1].length > 2) {
    parts[1] = parts[1].substring(0, 2);
    v = parts[0] + '.' + parts[1];
  }
  el.value = v;
}

function validatePercentInput(el) {
  validateDecimalInput(el, true);
}

function validateIntegerInput(el) {
  if (!el) return;
  el.value = el.value.replace(/\D/g, '');
}

function validateDateInput(el) {
  if (!el) return;
  var digits = el.value.replace(/\D/g, '').substring(0, 6);
  var res = '';
  if (digits.length > 0) res += digits.substring(0, 2);
  if (digits.length > 2) res += '-' + digits.substring(2, 4);
  if (digits.length > 4) res += '-' + digits.substring(4, 6);
  el.value = res;
}

/* Auto-save engine - only active on data-entry forms */
var _autosaveTimer = null;
var _isAutosaving = false;
var _hasUnsavedChanges = false;
var _isExplicitSubmitting = false;

function markFormDirty() {
  _hasUnsavedChanges = true;
}

function autoSaveCurrentForm(callback) {
  var form = document.querySelector('form.data-entry-form, form#annual-results-form, form#agreements-form, form#companies-form, form#survey-form');
  if (!form) {
    if (callback) callback();
    return;
  }

  if (_autosaveTimer) {
    clearTimeout(_autosaveTimer);
    _autosaveTimer = null;
  }

  var indicator = document.getElementById('autosave-indicator');
  var textEl = document.getElementById('autosave-text');
  if (indicator && textEl) {
    textEl.textContent = 'Saving...';
    indicator.style.color = 'rgba(255,255,255,0.85)';
    indicator.style.opacity = '1';
  }

  _isAutosaving = true;
  var formData = new FormData(form);
  var postUrl = form.getAttribute('action') || window.location.href;

  fetch(postUrl, {
    method: 'POST',
    body: formData,
    keepalive: true,
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json'
    }
  }).then(function (res) {
    if (!res.ok) {
      throw new Error('HTTP ' + res.status);
    }
    return res.json().catch(function () { return null; });
  }).then(function (data) {
    _isAutosaving = false;
    _hasUnsavedChanges = false;
    var wasExplicitSave = sessionStorage.getItem('unicompass_saved_refreshed') === '1';
    if (wasExplicitSave) {
      sessionStorage.removeItem('unicompass_saved_refreshed');
    }
    if (indicator && textEl) {
      textEl.textContent = wasExplicitSave ? 'Saved and refreshed' : 'Automatically saved';
      indicator.style.color = '#4ade80';
      indicator.style.opacity = '1';
      setTimeout(function () {
        if (!_hasUnsavedChanges && !_isAutosaving) {
          indicator.style.opacity = '0';
        }
      }, wasExplicitSave ? 3500 : 2500);
    }
    if (typeof window.onAutoSaveSuccess === 'function') {
      window.onAutoSaveSuccess(data);
    }
    if (callback) callback(data);
  }).catch(function (err) {
    _isAutosaving = false;
    if (indicator && textEl) {
      textEl.textContent = 'Save error';
      indicator.style.color = '#f87171';
      indicator.style.opacity = '1';
    }
    console.error('Auto-save error:', err);
    if (callback) callback();
  });
}

function debounceAutoSave() {
  markFormDirty();
  if (_autosaveTimer) clearTimeout(_autosaveTimer);
  _autosaveTimer = setTimeout(function () {
    autoSaveCurrentForm();
  }, 1200);
}

document.addEventListener('DOMContentLoaded', function () {
  // Find active data-entry form
  var form = document.querySelector('form.data-entry-form, form#annual-results-form, form#agreements-form, form#companies-form, form#survey-form');
  if (!form) {
    // Not a data entry page (login, home, admin, export, example)
    // Never attach auto-save, unload prompts, or beforeunload listeners
    return;
  }

  // Check if page was refreshed after an explicit save button click
  if (sessionStorage.getItem('unicompass_saved_refreshed') === '1') {
    sessionStorage.removeItem('unicompass_saved_refreshed');
    var indicator = document.getElementById('autosave-indicator');
    var textEl = document.getElementById('autosave-text');
    if (indicator && textEl) {
      textEl.textContent = 'Saved and refreshed';
      indicator.style.color = '#4ade80';
      indicator.style.opacity = '1';
      setTimeout(function () {
        indicator.style.opacity = '0';
      }, 3500);
    }
  }

  // Track clicks on any save button on the data form
  form.addEventListener('click', function (e) {
    var btn = e.target.closest('button[type="submit"], input[type="submit"], .btn-save, .btn-action-primary');
    if (btn) {
      _isExplicitSubmitting = true;
      sessionStorage.setItem('unicompass_saved_refreshed', '1');
      var indicator = document.getElementById('autosave-indicator');
      var textEl = document.getElementById('autosave-text');
      if (indicator && textEl) {
        textEl.textContent = 'Saving...';
        indicator.style.color = 'rgba(255,255,255,0.85)';
        indicator.style.opacity = '1';
      }
    }
  });

  // Mark explicit submit on form submit
  form.addEventListener('submit', function () {
    _isExplicitSubmitting = true;
    sessionStorage.setItem('unicompass_saved_refreshed', '1');
  });

  form.addEventListener('change', function (e) {
    markFormDirty();
    autoSaveCurrentForm();
  });

  form.addEventListener('input', function (e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
      debounceAutoSave();
    }
  });

  // Flush immediately on blur / focusout from form fields
  form.addEventListener('focusout', function (e) {
    if (_hasUnsavedChanges && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT')) {
      if (_autosaveTimer) {
        clearTimeout(_autosaveTimer);
        _autosaveTimer = null;
      }
      autoSaveCurrentForm();
    }
  });

  // Intercept link navigation when there are unsaved changes
  document.addEventListener('click', function (e) {
    var link = e.target.closest('a[href]');
    if (!link) return;
    var href = link.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('javascript:') || link.target === '_blank') return;
    if (_hasUnsavedChanges) {
      e.preventDefault();
      autoSaveCurrentForm(function () {
        window.location.href = href;
      });
    }
  });

  // Flush pending changes on tab hidden / tab switch / window minimize
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && _hasUnsavedChanges) {
      if (_autosaveTimer) {
        clearTimeout(_autosaveTimer);
        _autosaveTimer = null;
      }
      autoSaveCurrentForm();
    }
  });

  // Flush pending changes on pagehide (mobile & desktop tab closing/back/forward)
  window.addEventListener('pagehide', function () {
    if (_hasUnsavedChanges) {
      if (_autosaveTimer) {
        clearTimeout(_autosaveTimer);
        _autosaveTimer = null;
      }
      autoSaveCurrentForm();
    }
  });

  // Prompt user before unloading if changes are unsaved or in-flight
  window.addEventListener('beforeunload', function (e) {
    if (_isExplicitSubmitting) return;
    if (_hasUnsavedChanges || _isAutosaving) {
      if (_hasUnsavedChanges) {
        autoSaveCurrentForm();
      }
      e.preventDefault();
      e.returnValue = '';
    }
  });
});
