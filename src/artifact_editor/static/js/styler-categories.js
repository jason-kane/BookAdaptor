(async () => {
    await Promise.all(['wa-tab-group', 'wa-tab', 'wa-tab-panel'].map(name => customElements.whenDefined(name)));
    if (document.readyState === 'loading') {
        await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
    }
    const groups = () => [...document.querySelectorAll('[data-category-tab-group]')];
    const tabs = group => [...group.querySelectorAll('wa-tab[data-style-category]')];

    async function loadStockpileSlots(root) {
        const slots = root.matches?.('[data-stockpile-slot]') ? [root] : [];
        if (root.querySelectorAll) slots.push(...root.querySelectorAll('[data-stockpile-slot]'));
        for (const slot of slots) {
            if (slot.dataset.stockpileLoading || slot.dataset.stockpileLoaded) continue;
            slot.dataset.stockpileLoading = 'true';
            try {
                const url = new URL(slot.dataset.stockpileUrl, window.location.href);
                url.searchParams.set('tag', slot.dataset.stockpileTag);
                const response = await fetch(url, {headers: {Accept: 'text/html'}});
                if (!response.ok) throw new Error(`Stockpile images failed (${response.status})`);
                slot.innerHTML = await response.text();
                slot.dataset.stockpileLoaded = 'true';
                htmx.process(slot);
            } catch (error) {
                console.warn('Unable to load stockpile images', error);
                slot.textContent = 'Stockpile images unavailable.';
            } finally {
                delete slot.dataset.stockpileLoading;
            }
        }
    }

    function select(group, tab, updateUrl) {
        if (!tab) return;
        group.active = tab.getAttribute('panel');
        if (updateUrl) {
            const url = new URL(window.location.href);
            url.hash = tab.dataset.parentTopic && tab.dataset.subtopicName
                ? [tab.dataset.parentTopic, tab.dataset.subtopicName, tab.dataset.styleCategory].map(encodeURIComponent).join(',')
                : encodeURIComponent(tab.dataset.styleCategory);
            if (url.href !== window.location.href) history.pushState(null, '', url);
        }
        htmx.trigger(tab, 'styler-category-load');
    }

    function initialize(group) {
        if (group.dataset.categoryInitialized) return;
        group.dataset.categoryInitialized = 'true';
        htmx.process(group);
        let selected = null;
        group.addEventListener('wa-tab-show', event => {
            // Web Awesome versions differ on whether wa-tab-show targets the
            // tab or its group. Read the selected panel from the group, and
            // ignore bubbled events from nested tab groups.
            if (event.target.closest?.('wa-tab-group') !== group) return;
            const active = tabs(group).find(tab => tab.getAttribute('panel') === group.active);
            if (!active || active.dataset.styleCategory === selected) return;
            selected = active.dataset.styleCategory;
            select(group, active, true);
        });
        const hashParts = window.location.hash.slice(1).split(',');
        if (hashParts.length >= 3) {
            let tag;
            try { tag = decodeURIComponent(hashParts[2]); } catch (_) { return; }
            const target = tabs(group).find(tab => tab.dataset.styleCategory === tag);
            if (target) {
                selected = target.dataset.styleCategory;
                group.active = target.getAttribute('panel');
                target.toggleAttribute('data-target-selected', true);
                htmx.trigger(target, 'styler-category-load');
            }
        } else if (hashParts.length === 2) {
            // A parent/category link has no third path segment to choose the
            // tag. Web Awesome selects the first tag tab automatically, so
            // explicitly load its styles instead of waiting for a show event
            // that won't fire when the already-selected tab is clicked.
            const target = tabs(group)[0];
            if (target) {
                selected = target.dataset.styleCategory;
                select(group, target, false);
            }
        } else if (hashParts.length === 1 && (() => {
            let name;
            try { name = decodeURIComponent(hashParts[0]); } catch (_) { return false; }
            return [...document.querySelectorAll('.styler-topic-picker [data-topic-name]')]
                .some(tab => tab.dataset.topicName === name);
        })()) {
            // A parent hash is navigation state, not a flat tag.
            return;
        } else {
            let category;
            try { category = decodeURIComponent(window.location.hash.slice(1)); } catch (_) { return; }
            const target = tabs(group).find(tab => tab.dataset.styleCategory === category)
                || tabs(group).find(tab => tab.dataset.styleCategory === 'custom') || tabs(group)[0];
            if (target) {
                selected = target.dataset.styleCategory;
                select(group, target, false);
            }
        }
    }

    function scan(root = document) {
        if (root.matches?.('[data-category-tab-group]')) initialize(root);
        root.querySelectorAll?.('[data-category-tab-group]').forEach(initialize);
        loadStockpileSlots(root);
    }
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
        if (node.nodeType === Node.ELEMENT_NODE) scan(node);
    }))).observe(document.body, {childList: true, subtree: true});
    scan();

    document.addEventListener('styler:category-added', ({detail}) => {
        for (const group of groups()) {
            const tab = tabs(group).find(item => item.dataset.styleCategory === detail.tag);
            if (!tab) continue;
            tab.dataset.styleCount = detail.count;
            if (detail.stockpile_count !== undefined) tab.dataset.stockpileCount = detail.stockpile_count;
            tab.textContent = `${detail.tag} (${Number(detail.count) + Number(tab.dataset.stockpileCount || 0)})`;
            const content = group.querySelector(`#tag_${CSS.escape(detail.tag)}`);
            if (content?.childElementCount && (Number(detail.count) > 100) !== Boolean(content.matches('[data-style-page]')))
                htmx.trigger(tab, 'styler-category-load');
        }
    });
    document.addEventListener('styler:category-empty', ({detail}) => {
        for (const group of groups()) {
            const tab = tabs(group).find(item => item.dataset.styleCategory === detail.tag);
            if (!tab) continue;
            if (Number(tab.dataset.stockpileCount || 0)) {
                tab.textContent = `${detail.tag} (${tab.dataset.stockpileCount})`;
                continue;
            }
            const panelName = tab.getAttribute('panel');
            tab.remove();
            group.querySelector(`wa-tab-panel[name="${CSS.escape(panelName)}"]`)?.remove();
        }
    });
    document.body.addEventListener('stockpile-refresh', async () => {
        try {
            const response = await fetch('/styles/stockpile/tags');
            if (!response.ok) return;
            const {categories} = await response.json();
            const current = new Map(categories.map(category => [category.tag, category]));
            for (const group of groups()) for (const tab of tabs(group)) {
                const category = current.get(tab.dataset.styleCategory);
                const count = Number(tab.dataset.styleCount || 0);
                if (!category && Number(tab.dataset.stockpileCount || 0)) {
                    tab.dataset.stockpileCount = 0;
                    tab.textContent = count ? `${tab.dataset.styleCategory} (${count})` : tab.dataset.styleCategory;
                } else if (category) {
                    tab.dataset.stockpileCount = category.stockpile_count ?? category.count ?? 0;
                    tab.textContent = `${category.tag} (${Number(tab.dataset.stockpileCount) + count})`;
                }
            }
        } catch (error) { console.error('Unable to refresh stockpile tags', error); }
    });
    document.body.dispatchEvent(new CustomEvent('stockpile-refresh'));
})();
