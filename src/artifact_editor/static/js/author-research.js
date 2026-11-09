(() => {
  const button = document.getElementById('author-research');
  const form = document.getElementById('author-profile');
  const status = document.getElementById('research-status');
  if (!button || !form) return;

  const regroupBooks = () => {
    const root = document.getElementById('bibliography-results');
    const active = document.activeElement;
    const selection = active && 'selectionStart' in active ? [active.selectionStart, active.selectionEnd] : null;
    const expansion = new Map([...root.querySelectorAll('.bibliography-series')].map(group => [group.dataset.seriesKey, group.open]));
    const groups = new Map();
    for (const card of root.querySelectorAll('.author-book')) {
      const name = card.dataset.series.trim();
      const key = name.toLocaleLowerCase();
      if (!groups.has(key)) groups.set(key, { name: name || 'No series recorded', cards: [] });
      groups.get(key).cards.push(card);
    }
    const publicationKey = card => card.dataset.date || (card.dataset.year ? `${card.dataset.year.padStart(4, '0')}-00-00` : '99999');
    const sections = [...groups].sort(([a], [b]) => Number(!a) - Number(!b) || a.localeCompare(b)).map(([key, group]) => {
      const section = document.createElement('details');
      section.className = 'bibliography-series';
      section.dataset.seriesKey = key;
      const hasProcessed = group.cards.some(card => card.dataset.processed === 'true');
      section.open = group.cards.some(card => card.contains(active)) || (expansion.has(key) ? expansion.get(key) : hasProcessed);
      const summary = document.createElement('summary');
      const heading = document.createElement('h3');
      heading.className = 'series-heading';
      heading.textContent = `${group.name} (${group.cards.length})`;
      const grid = document.createElement('div');
      grid.className = 'author-books';
      group.cards.sort((a, b) => publicationKey(a).localeCompare(publicationKey(b)) || a.dataset.title.localeCompare(b.dataset.title));
      grid.append(...group.cards);
      summary.append(heading);
      section.append(summary, grid);
      return section;
    });
    root.replaceChildren(...sections);
    if (active && root.contains(active)) {
      active.focus({ preventScroll: true });
      if (selection && selection[0] !== null) active.setSelectionRange(...selection);
    }
  };

  const autosavers = new WeakMap();
  const pendingSaves = new Set();
  const enableAutosave = target => {
    const notice = target.querySelector('.autosave-status');
    let queue = Promise.resolve();
    let waiting = 0;
    const failures = new Map();
    const save = fields => {
      if (!Object.keys(fields).length) return Promise.resolve(true);
      waiting += 1;
      notice.textContent = 'Saving…';
      queue = queue.then(async () => {
        const payload = { ...fields };
        if (target.classList.contains('bibliography-edit')) {
          payload.book_name = target.elements.namedItem('book_name').value;
        }
        let saved = false;
        try {
          const response = await fetch(target.action, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload), keepalive: true,
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not save changes.');
          for (const key of Object.keys(fields)) {
            failures.delete(key);
            target.elements.namedItem(key)?.removeAttribute('aria-invalid');
          }
          if (target.classList.contains('contributor-form') && data.name) {
            target.closest('.contributor-card').querySelector('.contributor-name').textContent = data.name;
          }
          if (target.classList.contains('bibliography-edit')) {
            const card = target.closest('.author-book');
            if (data.book) {
              card.dataset.series = data.book.series;
              card.dataset.year = data.book.publication_year;
              card.dataset.date = data.book.publication_date;
              card.querySelector('.publication-date').textContent = data.book.publication_date || data.book.publication_year || 'Publication date unknown';
              for (const key of ['publication_year', 'publication_date']) {
                const input = target.elements.namedItem(key);
                if (!(key in fields) && input !== document.activeElement) input.value = data.book[key];
              }
            }
            if ('series_source' in fields) {
              const link = card.querySelector('.series-source');
              link.hidden = !fields.series_source.trim();
              if (!link.hidden) link.href = fields.series_source;
            }
            if (['series', 'publication_year', 'publication_date'].some(key => key in fields)) regroupBooks();
            if ('copyright_status' in fields) card.querySelector('.copyright-status').textContent = fields.copyright_status.trim() || 'Unknown';
            if ('copyright_source' in fields) {
              const link = card.querySelector('.copyright-source');
              link.hidden = !fields.copyright_source.trim();
              if (!link.hidden) link.href = fields.copyright_source;
            }
          }
          saved = true;
        } catch (error) {
          for (const key of Object.keys(fields)) failures.set(key, error.message || 'Could not save changes.');
        } finally {
          waiting -= 1;
          notice.textContent = waiting ? 'Saving…' : failures.size
            ? `Not saved: ${[...new Set(failures.values())].join(' ')} Edit the affected field to retry.`
            : 'All changes saved.';
        }
        return saved;
      });
      const task = queue;
      pendingSaves.add(task);
      task.finally(() => pendingSaves.delete(task));
      return task;
    };
    target.addEventListener('submit', event => event.preventDefault());
    target.addEventListener('change', event => {
      const input = event.target;
      if (!input.name || input.name === 'pen_name' || input.readOnly || input.type === 'hidden') return;
      if (!input.checkValidity()) {
        input.setAttribute('aria-invalid', 'true');
        failures.set(input.name, input.validationMessage);
        notice.textContent = `Not saved: ${input.validationMessage}`;
        return;
      }
      save({ [input.name]: input.value });
    });
    autosavers.set(target, save);
  };
  enableAutosave(form);
  document.querySelectorAll('.bibliography-edit').forEach(enableAutosave);

  const identityType = form.elements.namedItem('identity_type');
  const contributorsSection = document.getElementById('contributors-section');
  const contributorsList = document.getElementById('contributors-list');
  const syncIdentity = () => {
    const collective = identityType.value === 'collective';
    contributorsSection.hidden = !collective;
    form.querySelectorAll('[data-personal-field]').forEach(field => { field.hidden = collective; });
  };
  identityType.addEventListener('change', syncIdentity);
  syncIdentity();
  const bindContributorForms = root => root.querySelectorAll('.contributor-form').forEach(target => {
    if (!autosavers.has(target)) enableAutosave(target);
  });
  bindContributorForms(contributorsList);
  let individualRequests = 0;
  const addPerson = document.getElementById('add-contributor');
  addPerson.addEventListener('submit', async event => {
    event.preventDefault();
    if (!addPerson.reportValidity() || individualRequests) return;
    individualRequests += 1;
    const notice = addPerson.querySelector('.add-contributor-status');
    const controls = [...contributorsSection.querySelectorAll('input, textarea, wa-button')];
    controls.forEach(control => { control.disabled = true; });
    notice.textContent = 'Adding individual author…';
    try {
      const saved = await Promise.all([...pendingSaves]);
      if (saved.some(value => !value) || [...contributorsList.querySelectorAll('.autosave-status')].some(item => item.textContent.startsWith('Not saved:'))) throw new Error('Correct unsaved fields before adding an individual.');
      const response = await fetch(addPerson.action, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: addPerson.elements.namedItem('name').value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not add individual.');
      contributorsList.innerHTML = data.html;
      bindContributorForms(contributorsList);
      identityType.value = 'collective';
      syncIdentity();
      addPerson.reset();
      notice.textContent = 'Individual added. Changes save automatically.';
    } catch (error) {
      notice.textContent = error.message || 'Could not add individual.';
    } finally {
      controls.forEach(control => { control.disabled = false; });
      individualRequests -= 1;
    }
  });
  contributorsList.addEventListener('click', async event => {
    const research = event.target.closest('.research-contributor');
    if (!research || research.disabled || individualRequests) return;
    individualRequests += 1;
    const card = research.closest('.contributor-card');
    const notice = card.querySelector('.contributor-research-status');
    const controls = [...card.querySelectorAll('input, textarea, wa-button')];
    controls.forEach(control => { control.disabled = true; });
    notice.textContent = 'Researching this individual…';
    try {
      const saved = await Promise.all([...pendingSaves]);
      if (saved.some(value => !value) || card.querySelector('.autosave-status').textContent.startsWith('Not saved:')) throw new Error('Correct unsaved fields before researching.');
      const response = await fetch(research.dataset.url, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Individual research failed.');
      const template = document.createElement('template');
      template.innerHTML = data.html;
      const updated = template.content.firstElementChild;
      card.replaceWith(updated);
      bindContributorForms(updated);
      updated.querySelector('.contributor-research-status').textContent = data.warnings?.length
        ? 'Known details saved. Some source lookups were unavailable; retry to fill more blanks.'
        : 'Known details saved. Existing values were preserved.';
    } catch (error) {
      notice.textContent = error.message || 'Individual research failed.';
    } finally {
      controls.forEach(control => { control.disabled = false; });
      individualRequests -= 1;
    }
  });

  const penName = form.elements.namedItem('pen_name');
  const renameStatus = document.getElementById('rename-status');
  let renaming = false;
  penName.addEventListener('change', async () => {
    if (renaming || !penName.checkValidity()) return;
    if (button.disabled || document.getElementById('bibliography-research').disabled || individualRequests) {
      renameStatus.textContent = 'Wait for Research to finish, then change the pen name again.';
      return;
    }
    renaming = true;
    renameStatus.textContent = 'Saving changes and renaming author…';
    const controls = [...document.querySelectorAll('.author-page input, .author-page textarea, .author-page wa-button')];
    const disabled = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    try {
      const saves = await Promise.all([...pendingSaves]);
      const failed = [...document.querySelectorAll('.autosave-status')].some(notice => notice.textContent.startsWith('Not saved:'));
      if (failed || saves.some(value => !value)) throw new Error('Correct the unsaved fields before renaming the author.');
      const response = await fetch(penName.dataset.renameUrl, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pen_name: penName.value.trim() }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The author could not be renamed.');
      renameStatus.textContent = 'Author renamed. Opening the new address…';
      window.location.assign(data.url);
    } catch (error) {
      renameStatus.textContent = error.message || 'The author could not be renamed.';
      controls.forEach((control, index) => { control.disabled = disabled[index]; });
      renaming = false;
    }
  });

  const bibliographyButton = document.getElementById('bibliography-research');
  const bibliographyStatus = document.getElementById('bibliography-research-status');
  bibliographyButton.addEventListener('click', async () => {
    if (bibliographyButton.disabled) return;
    bibliographyButton.disabled = true;
    bibliographyButton.loading = true;
    bibliographyStatus.textContent = 'Researching missing books and bibliographic details…';
    const inputs = [...document.querySelectorAll('.bibliography-edit input')];
    inputs.forEach(input => { input.disabled = true; });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    try {
      const saved = await Promise.all([...pendingSaves]);
      const unsaved = [...document.querySelectorAll('.bibliography-edit .autosave-status')].some(notice => notice.textContent.startsWith('Not saved:'));
      if (unsaved || saved.some(value => !value)) throw new Error('Some edits could not be saved. Correct them before researching the bibliography.');
      const response = await fetch(bibliographyButton.dataset.url, {
        method: 'POST', signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Bibliography research failed. Please try again.');
      const root = document.getElementById('bibliography-results');
      const expansion = new Map([...root.querySelectorAll('.bibliography-series')].map(group => [group.dataset.seriesKey, group.open]));
      root.innerHTML = data.html;
      for (const group of root.querySelectorAll('.bibliography-series')) {
        if (expansion.has(group.dataset.seriesKey)) group.open = expansion.get(group.dataset.seriesKey);
      }
      document.getElementById('bibliography-count').textContent = `(${data.total})`;
      document.querySelectorAll('.bibliography-edit').forEach(enableAutosave);
      bibliographyStatus.textContent = `Added ${data.added} books; updated ${data.updated} existing entries; ${data.unchanged} already complete.`;
      if (data.skipped) bibliographyStatus.textContent += ` Skipped ${data.skipped} entries that could not be safely matched or added.`;
      if (!data.added && !data.updated && !data.unchanged) bibliographyStatus.textContent = 'No additional books were found in the source catalogue for this author.';
      if (data.warnings?.length) bibliographyStatus.textContent += ' Some source lookups were unavailable; retry to find more.';
    } catch (error) {
      bibliographyStatus.textContent = error.name === 'AbortError'
        ? 'Bibliography research timed out. Retry to check for additional entries.'
        : error.message || 'Bibliography research failed. Please try again.';
    } finally {
      clearTimeout(timer);
      inputs.forEach(input => { input.disabled = false; });
      bibliographyButton.disabled = false;
      bibliographyButton.loading = false;
    }
  });

  const portraitButton = document.getElementById('download-portrait');
  const portraitStatus = document.getElementById('portrait-status');
  const portraitInput = form.elements.namedItem('portrait_url');
  const preview = document.getElementById('portrait-preview');
  let portraitRequest = 0;
  const downloadPortrait = async () => {
    const request = ++portraitRequest;
    const url = portraitInput.value.trim();
    if (!url) {
      preview.hidden = true;
      portraitStatus.textContent = '';
      return;
    }
    portraitButton.disabled = true;
    portraitButton.loading = true;
    portraitStatus.textContent = 'Downloading portrait…';
    try {
      const response = await fetch(portraitButton.dataset.url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      const data = await response.json();
      if (request !== portraitRequest || portraitInput.value.trim() !== url) return;
      if (!response.ok) throw new Error(data.error || 'Portrait download failed.');
      document.getElementById('portrait-thumbnail').src = data.thumbnail_url;
      document.getElementById('portrait-image-link').href = data.image_url;
      document.getElementById('portrait-caption').textContent = form.elements.namedItem('portrait_credit').value;
      preview.hidden = false;
      portraitStatus.textContent = 'Portrait downloaded. Click the thumbnail to view the image.';
    } catch (error) {
      if (request === portraitRequest) {
        preview.hidden = true;
        portraitStatus.textContent = error.message || 'Portrait download failed. Please try again.';
      }
    } finally {
      if (request === portraitRequest) {
        portraitButton.disabled = false;
        portraitButton.loading = false;
      }
    }
  };
  portraitButton.addEventListener('click', downloadPortrait);
  portraitInput.addEventListener('change', () => { preview.hidden = true; downloadPortrait(); });
  if (portraitInput.value.trim() && preview.hidden) downloadPortrait();

  button.addEventListener('click', async () => {
    if (button.disabled || individualRequests) return;
    button.disabled = true;
    button.loading = true;
    status.textContent = 'Researching biography and individual authors…';
    const peopleControls = [...contributorsList.querySelectorAll('input, textarea, wa-button')];
    peopleControls.forEach(control => { control.disabled = true; });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    let count = 0;
    const fill = (target, key, value) => {
      const input = target.elements.namedItem(key);
      // Recheck after the lookup so edits typed while waiting also survive.
      if (input && !input.readOnly && !input.value.trim() && typeof value === 'string' && value.trim()) {
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        count += 1;
        return true;
      }
      return false;
    };
    try {
      const pending = await Promise.all([...pendingSaves]);
      if (pending.some(value => !value) || [...contributorsList.querySelectorAll('.autosave-status')].some(item => item.textContent.startsWith('Not saved:'))) throw new Error('Correct unsaved fields before researching.');
      const response = await fetch(button.dataset.url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
        signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Research failed. Please try again.');
      let profileChanged = false;
      const profileChanges = {};
      const saves = [];
      if (data.contributors_html) {
        contributorsList.innerHTML = data.contributors_html;
        bindContributorForms(contributorsList);
        identityType.value = 'collective';
        syncIdentity();
        profileChanges.identity_type = 'collective';
        profileChanged = true;
      }
      const portrait = form.elements.namedItem('portrait_url').value.trim();
      for (const [key, value] of Object.entries(data.profile || {})) {
        // Credits for a found image must not be attached to a different existing portrait.
        if (key === 'pen_name' || key === 'identity_type') continue;
        if (key.startsWith('portrait_') && portrait && portrait !== data.profile.portrait_url) continue;
        if (fill(form, key, value)) {
          profileChanged = true;
          profileChanges[key] = value;
        }
      }
      if (profileChanged) {
        if (data.profile?.portrait_url && form.elements.namedItem('portrait_url').value === data.profile.portrait_url) {
          form.querySelector('details').open = true;
        }
        const sourcesInput = form.elements.namedItem('research_sources');
        const sources = [...new Set([...JSON.parse(sourcesInput.value || '[]'), ...(data.sources || [])])];
        sourcesInput.value = JSON.stringify(sources);
        profileChanges.research_sources = sourcesInput.value;
        const links = document.getElementById('research-sources');
        links.replaceChildren();
        const caption = document.createElement('p');
        caption.append('Research sources (Wikipedia text: ');
        const license = document.createElement('a');
        license.href = 'https://creativecommons.org/licenses/by-sa/4.0/';
        license.textContent = 'CC BY-SA';
        caption.append(license, '):');
        links.append(caption);
        for (const source of sources) {
          const link = document.createElement('a');
          link.href = source;
          link.textContent = source;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          links.append(link, document.createElement('br'));
        }
      }
      if (profileChanged) saves.push(autosavers.get(form)(profileChanges));
      for (const entry of document.querySelectorAll('.bibliography-edit')) {
        const found = data.books?.[entry.elements.namedItem('book_name').value];
        if (!found) continue;
        const fields = {};
        const existingYear = entry.elements.namedItem('publication_year').value.trim();
        if (!existingYear || existingYear === found.publication_year) {
          for (const key of ['publication_year', 'source_url']) {
            if (fill(entry, key, found[key])) fields[key] = found[key];
          }
        }
        const existingCopyright = entry.elements.namedItem('copyright_status').value.trim();
        if (!existingCopyright || existingCopyright === found.copyright_status) {
          for (const key of ['copyright_status', 'copyright_source']) {
            if (fill(entry, key, found[key])) fields[key] = found[key];
          }
        }
        if (Object.keys(fields).length) {
          entry.closest('details').open = true;
          saves.push(autosavers.get(entry)(fields));
        }
      }
      const saved = (await Promise.all(saves)).every(Boolean);
      status.textContent = count
        ? `Filled ${count} blank fields. ${saved ? 'Changes saved automatically.' : 'Some changes could not be saved; see the save status below.'} Unknown details stay blank.`
        : 'No additional blank fields could be filled from these sources. Existing values were preserved.';
      if (data.contributors_html) status.textContent += ` Added ${data.contributors_added} individuals; updated ${data.contributors_updated} profiles.`;
      if (portraitInput.value.trim()) await downloadPortrait();
      if (data.warnings?.length) status.textContent += ' Some lookups were unavailable; you can retry Research.';
    } catch (error) {
      status.textContent = error.name === 'AbortError'
        ? 'Research timed out. Please try again.'
        : error.message || 'Research failed. Please try again.';
    } finally {
      clearTimeout(timer);
      peopleControls.forEach(control => { control.disabled = false; });
      button.disabled = false;
      button.loading = false;
    }
  });
})();
