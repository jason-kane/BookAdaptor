(() => {
    const pending = new WeakSet();
    let options;
    function refreshOptions() {
        if (!options) {
            options = document.createElement('datalist');
            options.id = 'styler-tag-options';
            document.body.append(options);
        }
        options.replaceChildren(...[...document.querySelectorAll('[data-style-category]')].map(tab => {
            const option = document.createElement('option');
            option.value = tab.dataset.styleCategory;
            return option;
        }));
    }
    document.addEventListener('focusin', event => {
        if (event.target.matches('.style-tag-picker')) refreshOptions();
    });
    document.addEventListener('click', event => {
        const editButton = event.target.closest?.('[data-style-name-edit]');
        if (editButton) {
            event.preventDefault();
            event.stopPropagation();
            const heading = editButton.closest('.styler-style-heading');
            const view = heading?.querySelector('[data-style-name-view]');
            const form = heading?.querySelector('[data-style-name-form]');
            const input = form?.querySelector('[name="name"]');
            if (!view || !form || !input) return;
            form.querySelector('[data-style-name-status]').textContent = '';
            view.hidden = true;
            form.hidden = false;
            input.focus();
            input.select();
            return;
        }
        const form = event.target.closest?.('[data-style-name-form]');
        if (form && !form.dataset.stopSummaryToggle) {
            form.dataset.stopSummaryToggle = 'true';
            form.addEventListener('click', click => click.stopPropagation());
        }
    }, true);
    document.addEventListener('wa-hide', event => {
        if (event.target.localName !== 'wa-details') return;
        const heading = event.target.querySelector('.styler-style-heading');
        const view = heading?.querySelector('[data-style-name-view]');
        const form = heading?.querySelector('[data-style-name-form]');
        if (!view || !form) return;
        form.hidden = true;
        view.hidden = false;
    });
    document.addEventListener('htmx:beforeRequest', event => {
        const form = event.detail.elt?.closest?.('[data-style-name-form]');
        if (form) form.querySelector('[data-style-name-status]').textContent = 'Saving…';
    });
    document.addEventListener('htmx:afterRequest', event => {
        const form = event.detail.elt?.closest?.('[data-style-name-form]');
        if (!form) return;
        const status = form.querySelector('[data-style-name-status]');
        const xhr = event.detail.xhr;
        if (xhr.status < 200 || xhr.status >= 300) {
            status.textContent = xhr.responseText || 'Unable to save name';
            return;
        }
        try {
            const result = JSON.parse(xhr.responseText);
            const card = form.closest('.card-overview');
            const heading = form.closest('.styler-style-heading');
            const nameView = heading?.querySelector('[data-style-name-view]');
            const nameText = heading?.querySelector('[data-style-name-text]');
            if (nameText) nameText.textContent = result.name;
            for (const sample of card?.querySelectorAll('[data-style-sample]') || []) {
                sample.dataset.styleName = result.name;
            }
            form.querySelector('[name="name"]').value = result.name;
            form.hidden = true;
            if (nameView) nameView.hidden = false;
        } catch (error) {
            status.textContent = 'Saved';
        }
    });
    async function addTag(input, cloud = input.closest('.tags-removable[data-style-tag]'), value = (input.value || '').trim()) {
        if (!cloud || !value || pending.has(input)) return;
        pending.add(input);
        input.disabled = true;
        try {
            await htmx.ajax('POST', '/styles/add_tag', {
                values: {style_tag: cloud.dataset.styleTag, tag: value},
                swap: 'none',
                handler: (_element, response) => {
                    if (response.xhr.status < 200 || response.xhr.status >= 300) return;
                    const result = JSON.parse(response.xhr.responseText);
                    const exists = [...cloud.querySelectorAll('wa-tag')].some(tag => tag.textContent.trim() === result.tag);
                    if (!exists) {
                        const tag = document.createElement('wa-tag');
                        tag.setAttribute('with-remove', '');
                        tag.setAttribute('pill', '');
                        tag.textContent = result.tag;
                        cloud.insertBefore(tag, cloud.querySelector('.style-tag-picker-label, label'));
                    }
                    document.dispatchEvent(new CustomEvent('styler:category-added', {detail: result}));
                    input.value = '';
                }
            });
        } finally {
            input.disabled = false;
            pending.delete(input);
        }
    }
    document.addEventListener('change', event => {
        if (event.target.matches('.style-tag-picker')) addTag(event.target);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Enter' && event.target.matches('.style-tag-picker')) {
            event.preventDefault();
            addTag(event.target);
        }
    });
    async function removeTag(tag) {
        const cloud = tag?.closest('.tags-removable[data-style-tag]');
        if (!cloud || pending.has(tag)) return;
        pending.add(tag);
        try {
            await htmx.ajax('DELETE', '/styles/tag', {
                values: {tag: tag.textContent.trim(), style_tag: cloud.dataset.styleTag},
                swap: 'none',
                handler: (_element, response) => {
                    if (response.xhr.status >= 200 && response.xhr.status < 300) {
                        const result = JSON.parse(response.xhr.responseText);
                        for (const tab of document.querySelectorAll('[data-style-category]')) {
                            if (tab.dataset.styleCategory === result.tag) {
                                tab.dataset.styleCount = result.count;
                                const count = result.count + Number(tab.dataset.stockpileCount || 0);
                                tab.textContent = `${result.tag} (${count})`;
                            }
                        }
                        const panel = cloud.closest('wa-tab-panel');
                        if (panel?.getAttribute('name') === `tag_${result.tag}`) {
                            const entry = cloud.closest('[data-style-entry]') || cloud.closest('.card-overview');
                            entry?.remove();
                        }
                        tag.remove();
                        if (result.count === 0) {
                            document.dispatchEvent(new CustomEvent('styler:category-empty', {detail: {tag: result.tag}}));
                        }
                    }
                }
            });
        } finally {
            pending.delete(tag);
        }
    }
    document.addEventListener('wa-remove', event => removeTag(event.target.closest('wa-tag')));
    document.addEventListener('click', async event => {
        const button = event.target.closest('[data-delete-image-tag]');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        const tag = button.dataset.deleteImageTag;
        if (!window.confirm(`Delete “${tag}” from every sample and style? This cannot be undone.`)) return;
        if (pending.has(button)) return;
        pending.add(button);
        button.disabled = true;
        try {
            await htmx.ajax('DELETE', '/styles/tag/all', {
                values: {tag},
                swap: 'none',
                handler: (_element, response) => {
                    if (response.xhr.status < 200 || response.xhr.status >= 300) return;
                    const tabs = [...document.querySelectorAll('[data-style-category]')];
                    const tab = tabs.find(item => item.dataset.styleCategory === tag);
                    const panel = document.querySelector(`wa-tab-panel[name="tag_${CSS.escape(tag)}"]`);
                    const group = tab?.closest('wa-tab-group');
                    const wasActive = group?.getAttribute('active') === `tag_${tag}`;
                    tab?.remove();
                    panel?.remove();
                    if (wasActive) {
                        const next = group?.querySelector('[data-style-category]');
                        if (next) group.setAttribute('active', `tag_${next.dataset.styleCategory}`);
                    }
                    document.dispatchEvent(new CustomEvent('styler:category-empty', {detail: {tag}}));
                }
            });
        } finally {
            button.disabled = false;
            pending.delete(button);
        }
    }, true);
    async function toggleImageTag(tag) {
        const card = tag.closest('.card-overview');
        const column = tag.closest('[data-image-tags]');
        const cloud = card?.querySelector('[id^="tag_cloud_"][data-style-tag]');
        if (!cloud || !column || pending.has(cloud)) return;
        const text = tag.textContent.trim();
        const existing = [...cloud.querySelectorAll('wa-tag')].find(chip => chip.textContent.trim() === text);
        pending.add(cloud);
        tag.setAttribute('aria-busy', 'true');
        try {
            if (existing) await removeTag(existing);
            else await addTag(tag, cloud, text);
        } finally {
            pending.delete(cloud);
            tag.removeAttribute('aria-busy');
        }
    }
    // Image tags live in the summary; activation must not toggle the panel.
    document.addEventListener('click', event => {
        const tag = event.target.closest('[data-image-tags] wa-tag');
        if (!tag) return;
        event.preventDefault();
        event.stopPropagation();
        toggleImageTag(tag);
    }, true);
    document.addEventListener('keydown', event => {
        if (!['Enter', ' '].includes(event.key)) return;
        const tag = event.target.closest('[data-image-tags] wa-tag');
        if (!tag) return;
        event.preventDefault();
        event.stopPropagation();
        toggleImageTag(tag);
    }, true);
})();
