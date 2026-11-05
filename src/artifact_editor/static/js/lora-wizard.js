// Restore after both selects and options have upgraded; early value attributes can
// be consumed before Web Awesome has initialized the available options.
if (!window.loraSourceImageLimitsBound) {
  window.loraSourceImageLimitsBound = true;
  const updateSourceCount = () => {
    const checkboxes = Array.from(document.querySelectorAll('#lora-wizard input[name="source_images"]'));
    if (!checkboxes.length) return;
    const selected = checkboxes.filter(input => input.checked).length;
    const status = document.getElementById('lora-source-selection-count');
    if (status) status.textContent = `${selected} selected. Choose 30–50 images.`;
    const submit = document.getElementById('lora-select-source-submit');
    if (submit) submit.disabled = selected < 30 || selected > 50;
  };
  document.addEventListener('change', event => {
    if (event.target.matches?.('#lora-wizard input[name="source_images"]')) updateSourceCount();
  });
  document.addEventListener('DOMContentLoaded', updateSourceCount);
  document.addEventListener('htmx:afterSettle', updateSourceCount);
  if (document.readyState !== 'loading') updateSourceCount();
}

// Persist source-image checkbox changes while the user is still on step 2.
// This keeps a reload from restoring the initial first-50 defaults.
if (!window.loraSourceSelectionAutosaveBound) {
  window.loraSourceSelectionAutosaveBound = true;
  let sourceSelectionSave = Promise.resolve();
  let sourceSelectionTimer = null;
  let sourceSelectionVersion = 0;
  document.addEventListener('change', event => {
    const input = event.target;
    if (!input.matches?.('#lora-wizard input[name="source_images"]')) return;
    const form = input.closest('form[data-source-selection-autosave]');
    if (!form) return;
    const status = document.getElementById('lora-source-selection-count');
    const version = ++sourceSelectionVersion;
    clearTimeout(sourceSelectionTimer);
    if (status) status.textContent = 'Saving selection…';
    sourceSelectionTimer = setTimeout(() => {
      const payload = new FormData(form);
      payload.set('action', 'save_source_selection');
      sourceSelectionSave = sourceSelectionSave.then(async () => {
        try {
          const response = await fetch(form.action, { method: 'POST', body: payload });
          if (!response.ok) throw new Error('Could not save selection.');
          if (version === sourceSelectionVersion && status) status.textContent = 'Selection saved.';
        } catch (error) {
          if (version === sourceSelectionVersion && status) status.textContent = `${error.message} Continue to save it.`;
        }
      });
    }, 250);
  });
}

if (!window.loraRestoreBound) {
  window.loraRestoreBound = true;
  const keepBelowImage = select => {
    const popup = select.shadowRoot?.querySelector('wa-popup');
    if (!popup) return;
    select.placement = 'bottom';
    popup.flip = false;
    popup.removeAttribute('flip');
    popup.placement = 'bottom-start';
    popup.autoSize = 'vertical';
    popup.autoSizePadding = 8;
    if (popup.active) popup.reposition();
  };
  document.addEventListener('wa-show', event => {
    const select = event.target;
    if (select.matches?.('#lora-wizard wa-select[name^="characters_"]')) keepBelowImage(select);
  });
  window.loraRestoreSelections = async () => {
    await Promise.all(['wa-select', 'wa-option'].map(tag => customElements.whenDefined(tag)));
    const selects = Array.from(document.querySelectorAll('#lora-wizard wa-select[data-saved-characters]'));
    await Promise.all(selects.map(async select => {
      if (select.dataset.selectionReady) return;
      select.dataset.selectionReady = 'restoring';
      await Promise.all(Array.from(select.querySelectorAll('wa-option')).map(option => option.updateComplete));
      await select.updateComplete;
      select.value = JSON.parse(select.dataset.savedCharacters);
      await select.updateComplete;
      keepBelowImage(select);
      select.dataset.selectionReady = 'ready';
    }));
  };
  document.addEventListener('DOMContentLoaded', window.loraRestoreSelections);
  document.addEventListener('htmx:afterSettle', window.loraRestoreSelections);
  if (document.readyState !== 'loading') window.loraRestoreSelections();
}

// Delegation also handles wizard panels refreshed by HTMX.
if (!window.loraCharacterFormBound) {
  window.loraCharacterFormBound = true;
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (form.id !== 'lora-add-character-form') return;
    event.preventDefault();
    const button = form.querySelector('wa-button');
    const message = form.querySelector('[data-character-message]');
    button.disabled = true;
    message.textContent = 'Adding character…';
    try {
      const response = await fetch(form.action, { method: 'POST', body: new FormData(form) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not add character.');
      document.querySelectorAll('#lora-wizard wa-select[name^="characters_"]').forEach(select => {
        if (Array.from(select.querySelectorAll('wa-option')).some(option => option.value === result.tag)) return;
        const option = document.createElement('wa-option');
        option.value = result.tag;
        option.textContent = result.name;
        select.append(option);
      });
      form.reset();
      message.textContent = `${result.name} is now available in every character selector.`;
    } catch (error) {
      message.textContent = error.message || 'Could not add character. Please retry.';
    } finally {
      button.disabled = false;
    }
  });
}

if (!window.loraImageAutosaveBound) {
  window.loraImageAutosaveBound = true;
  const edits = new Map();
  let restoredDraft = null;
  let requests = Promise.resolve();
  const entryState = card => {
    const key = card.dataset.saveUrl;
    if (!edits.has(key)) edits.set(key, { card, timer: null, tail: Promise.resolve(), pending: 0, error: false });
    const state = edits.get(key);
    state.card = card;
    return state;
  };
  const showSaveStatus = (card, message) => {
    document.querySelectorAll('[data-lora-image]').forEach(other => {
      if (other.dataset.loraImage === card.dataset.loraImage) {
        const status = other.querySelector('[data-image-save-status]');
        if (status) status.textContent = message;
      }
    });
  };
  const queueSave = state => {
    clearTimeout(state.timer);
    state.timer = null;
    const card = state.card;
    const caption = card.querySelector('wa-textarea');
    const characters = card.querySelector('wa-select');
    const payload = { caption: caption.value, character_tags: characters.value || [] };
    const status = card.querySelector('[data-image-save-status]');
    state.pending++;
    showSaveStatus(card, 'Saving…');
    state.tail = requests = requests.then(async () => {
      try {
        const response = await fetch(card.dataset.saveUrl, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), keepalive: true
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not save changes.');
        state.error = false;
        showSaveStatus(card, 'Saved');
        const wizard = document.getElementById('lora-wizard');
        if (wizard) wizard.dataset.lastModifiedImage = card.dataset.loraImage;
      } catch (error) {
        state.error = true;
        showSaveStatus(card, `Not saved: ${error.message} Edit again to retry.`);
      } finally {
        state.pending--;
      }
    });
    return state.tail;
  };
  const changed = event => {
    const control = event.target.closest('wa-textarea, wa-select');
    const card = control?.closest('[data-lora-image]');
    if (!card || control.disabled || (control.matches('wa-select') && control.dataset.selectionReady !== 'ready')) return;
    document.querySelectorAll('[data-lora-image]').forEach(other => {
      if (other !== card && other.dataset.loraImage === card.dataset.loraImage) {
        const counterpart = other.querySelector(control.matches('wa-select') ? 'wa-select' : 'wa-textarea');
        if (counterpart) counterpart.value = Array.isArray(control.value) ? [...control.value] : control.value;
      }
    });
    const state = entryState(card);
    clearTimeout(state.timer);
    showSaveStatus(card, 'Unsaved changes…');
    state.timer = setTimeout(() => queueSave(state), 400);
  };
  document.addEventListener('input', changed);
  document.addEventListener('change', changed);
  document.addEventListener('wa-input', changed);
  document.addEventListener('wa-change', changed);
  document.addEventListener('htmx:beforeRequest', event => {
    if (event.target.id === 'lora-wizard' &&
        (window.loraCaptionRequestActive || document.getElementById('lora-image-viewer')?.open ||
        (Array.from(edits.values()).some(state => state.timer || state.pending || state.error) ||
         document.activeElement?.closest('[data-lora-image]')))) event.preventDefault();
  });
  // Flush debounced edits before a full form navigation (including Remove image).
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.closest('#lora-wizard') || form.id === 'lora-add-character-form') return;
    const states = Array.from(edits.values()).filter(state => state.timer || state.pending || state.error);
    if (!states.length) return;
    event.preventDefault();
    const submitter = event.submitter;
    await Promise.all(states.map(state => state.timer || state.error ? queueSave(state) : state.tail));
    if (states.some(state => state.error)) return;
    if (submitter?.name) {
      const value = document.createElement('input');
      value.type = 'hidden';
      value.name = submitter.name;
      value.value = submitter.value;
      form.append(value);
    }
    HTMLFormElement.prototype.submit.call(form);
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest('.caption-wand');
    const card = button?.closest('[data-lora-image]');
    if (!card || button.disabled || window.loraCaptionRequestActive) return;
    window.loraCaptionRequestActive = true;
    const state = entryState(card);
    await queueSave(state);
    if (state.error) { window.loraCaptionRequestActive = false; return; }
    const controls = Array.from(document.querySelectorAll('#lora-wizard [data-lora-image] wa-select, #lora-wizard [data-lora-image] wa-textarea, #lora-wizard .caption-wand'));
    const disabled = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    const url = card.dataset.saveUrl + '/caption';
    try {
      showSaveStatus(card, 'Queued for automatic caption…');
      let response = await fetch(url, { method: 'POST' });
      let result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not queue caption.');
      while (!result.ready && ['queued', 'running'].includes(result.state)) {
        showSaveStatus(card, result.state === 'queued' ? 'Waiting for local Qwen…' : 'Generating caption…');
        await new Promise(resolve => setTimeout(resolve, 1200));
        response = await fetch(url, { cache: 'no-store' });
        result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not read caption status.');
      }
      if (!result.ready) throw new Error(result.error || 'Caption generation failed. Click the wand to retry.');
      document.querySelectorAll('[data-lora-image]').forEach(other => {
        if (other.dataset.loraImage === card.dataset.loraImage) other.querySelector('wa-textarea').value = result.caption;
      });
      showSaveStatus(card, 'Generated and saved');
      document.getElementById('lora-wizard').dataset.lastModifiedImage = card.dataset.loraImage;
    } catch (error) {
      showSaveStatus(card, error.message);
    } finally {
      controls.forEach((control, index) => { control.disabled = disabled[index]; });
      window.loraCaptionRequestActive = false;
    }
  });
  window.addEventListener('pagehide', () => {
    edits.forEach(state => { if (state.timer) queueSave(state); });
  });
  const restore = () => {
    const wizard = document.getElementById('lora-wizard');
    if (!wizard || restoredDraft === location.href) return;
    const id = wizard.dataset.lastModifiedImage;
    const card = id && document.getElementById(`lora-image-${id}`);
    if (!card) return;
    restoredDraft = location.href;
    Promise.all([window.loraRestoreSelections(), customElements.whenDefined('wa-textarea')]).then(() => {
      requestAnimationFrame(() => requestAnimationFrame(() => card.scrollIntoView({ block: 'center', behavior: 'instant' })));
    });
  };
  if (document.readyState !== 'loading') restore();
  document.addEventListener('DOMContentLoaded', restore);
  document.addEventListener('htmx:afterSettle', restore);
}


if (!window.loraImageViewerBound) {
  window.loraImageViewerBound = true;
  document.addEventListener('click', async event => {
    // Web components can retarget events, and some browsers expose a non-Element
    // target. Normalize before using closest() so the delegated handler survives.
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    const opener = target?.closest('.lora-image-open');
    const closer = target?.closest('[data-close-image-viewer]');
    const dialog = document.getElementById('lora-image-viewer');
    if (closer && dialog) { dialog.close(); return; }
    if (!opener || !dialog || opener.closest('#lora-image-viewer')) return;
    const original = opener.closest('[data-lora-image]');
    const image = original?.querySelector('.lora-image-open img');
    const content = dialog.querySelector('[data-viewer-content]');
    const title = dialog.querySelector('#lora-viewer-title');
    if (!original || !image || !content || !title || typeof dialog.showModal !== 'function') return;
    event.preventDefault();
    const card = original.cloneNode(true);
    card.removeAttribute('id');
    card.querySelector('button[name="remove_image"]')?.remove();
    const imageButton = card.querySelector('.lora-image-open');
    const enlargedImage = imageButton?.querySelector('img');
    if (!enlargedImage) return;
    imageButton.replaceWith(enlargedImage);
    content.replaceChildren(card);
    title.textContent = image.alt;
    dialog.showModal();
    const select = card.querySelector('wa-select');
    if (!select) return;
    select.removeAttribute('data-selection-ready');
    const sourceSelect = original.querySelector('wa-select');
    select.dataset.savedCharacters = sourceSelect?.dataset.selectionReady === 'ready'
      ? JSON.stringify(sourceSelect.value || []) : sourceSelect.dataset.savedCharacters;
    card.querySelector('wa-textarea').value = original.querySelector('wa-textarea').value;
    // Show the image immediately; initialize only this viewer's controls.
    await Promise.all(['wa-select', 'wa-option'].map(tag => customElements.whenDefined(tag)));
    await Promise.all(Array.from(select.querySelectorAll('wa-option')).map(option => option.updateComplete));
    await select.updateComplete;
    select.value = JSON.parse(select.dataset.savedCharacters);
    await select.updateComplete;
    select.dataset.selectionReady = 'ready';
    const popup = select.shadowRoot?.querySelector('wa-popup');
    if (popup) { popup.flip = false; popup.placement = 'bottom-start'; popup.autoSize = 'vertical'; }
  });
}
