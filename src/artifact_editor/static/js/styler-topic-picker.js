(() => {
    const picker = document.querySelector('[data-styler-topic-picker]');
    if (!picker) return;
    const topicTabs = picker.querySelector('[data-topic-tabs]');
    const status = picker.querySelector('[data-topic-status]');
    const buildButton = document.querySelector('[data-topic-build]');
    const promptForm = document.querySelector('.styler-base-prompt-nav form');
    const promptSelect = promptForm?.querySelector('[name="base_style_prompt"]');
    let topics = [];
    let selectedTopic = null;
    const topicPanel = name => {
        const tab = [...topicTabs.querySelectorAll('wa-tab[data-topic-name]')]
            .find(item => item.dataset.topicName === name);
        return tab && [...topicTabs.querySelectorAll('wa-tab-panel')]
            .find(panel => panel.getAttribute('name') === tab.getAttribute('panel'));
    };
    const categoryGroup = name => topicPanel(name)?.querySelector('[data-category-tab-group]');
    function subtopicPanel(parentName, subtopicName) {
        const childTabs = categoriesPanel(parentName);
        const tab = [...(childTabs?.querySelectorAll('wa-tab[data-subtopic-name]') || [])]
            .find(item => item.dataset.subtopicName === subtopicName);
        return tab && [...childTabs.querySelectorAll('wa-tab-panel')]
            .find(panel => panel.getAttribute('name') === tab.getAttribute('panel'));
    }
    function ensureCategoryPlaceholder(panel) {
        let placeholder = panel?.querySelector('[data-category-placeholder]');
        if (!placeholder && panel) {
            placeholder = document.createElement('div');
            placeholder.dataset.categoryPlaceholder = '';
            placeholder.setAttribute('role', 'status');
            placeholder.textContent = 'Choose a category to load its tags.';
            panel.append(placeholder);
        }
        return placeholder;
    }

    promptForm?.addEventListener('submit', event => {
        event.preventDefault();
        const url = new URL(window.location.href);
        url.searchParams.set('base_style_prompt', promptSelect?.value || 'classic');
        window.location.assign(url.href);
    });
    promptSelect?.addEventListener('change', () => promptForm.requestSubmit());

    function decodeHashPath() {
        const encoded = window.location.hash.slice(1);
        if (!encoded) return null;
        const parts = encoded.split(',');
        if (parts.length < 1 || parts.length > 4) return null;
        try { return parts.map(part => decodeURIComponent(part)); }
        catch (_) { return null; }
    }

    function updateTopicHash(parts) {
        const current = decodeHashPath();
        if (current && current.length >= parts.length
                && parts.every((part, index) => current[index] === part)) return;
        const url = new URL(window.location.href);
        url.hash = parts.map(encodeURIComponent).join(',');
        if (url.href !== window.location.href) history.pushState(null, '', url);
    }

    function setStylePermalinks(root = document) {
        const group = root.closest?.('[data-category-tab-group]')
            || root.querySelector?.('[data-category-tab-group]')
            || picker.querySelector('[data-category-tab-group]');
        if (!group) return;
        const activeTab = [...group.querySelectorAll('wa-tab[data-style-category]')]
            .find(tab => tab.getAttribute('panel') === group.active);
        const parent = activeTab?.dataset.parentTopic;
        const category = activeTab?.dataset.subtopicName;
        const substyle = activeTab?.dataset.styleCategory;
        if (!parent || !category || !substyle) return;
        const cards = [];
        if (root.matches?.('[id^="card_"]')) cards.push(root);
        if (root.querySelectorAll) cards.push(...root.querySelectorAll('[id^="card_"]'));
        for (const card of cards) {
            const style = card.id.slice('card_'.length);
            const heading = card.querySelector('[data-style-name-view]');
            if (!heading || heading.querySelector('[data-style-permalink]')) continue;
            const link = document.createElement('a');
            link.dataset.stylePermalink = '';
            link.className = 'styler-style-permalink';
            link.href = '#' + [parent, category, substyle, style].map(encodeURIComponent).join(',');
            link.setAttribute('aria-label', `Link to ${card.querySelector('[data-style-name-text]')?.textContent || style}`);
            link.title = 'Link to this style';
            link.textContent = '🔗';
            heading.append(link);
        }
    }

    function categoriesPanel(parentName) {
        const parentTab = [...topicTabs.querySelectorAll('wa-tab[data-topic-name]')]
            .find(item => item.dataset.topicName === parentName);
        if (!parentTab) return null;
        const panelName = parentTab.getAttribute('panel');
        const panel = [...topicTabs.querySelectorAll('wa-tab-panel')]
            .find(item => item.getAttribute('name') === panelName);
        return panel?.querySelector('[data-topic-categories]') || null;
    }

    function addTagTab(tag, group) {
        let tab = [...group.querySelectorAll('wa-tab[data-style-category]')]
            .find(item => item.dataset.styleCategory === tag);
        if (!tab) {
            tab = document.createElement('wa-tab');
            tab.dataset.styleCategory = tag;
            tab.textContent = tag;
            tab.setAttribute('panel', `tag_${tag}`);
            tab.setAttribute('hx-trigger', 'styler-category-load');
            tab.setAttribute('hx-put', '/styles/choose_tag');
            tab.setAttribute('hx-vals', JSON.stringify({tag, base_style_prompt: group.dataset.baseStylePrompt || 'classic'}));
            tab.setAttribute('hx-swap', 'none');
            const panel = document.createElement('wa-tab-panel');
            panel.setAttribute('name', `tag_${tag}`);
            if (!['LOST', 'ABYSS'].includes(tag)) {
                const tools = document.createElement('div');
                tools.className = 'wa-cluster';
                tools.style.cssText = 'justify-content:flex-end;padding:0.5rem';
                const remove = document.createElement('wa-button');
                remove.setAttribute('size', 'small');
                remove.setAttribute('appearance', 'plain');
                remove.dataset.deleteImageTag = tag;
                remove.setAttribute('aria-label', `Delete ${tag} from all samples`);
                remove.title = 'Delete tag from all samples';
                const icon = document.createElement('wa-icon');
                icon.name = 'trash';
                remove.append(icon);
                tools.append(remove);
                panel.append(tools);
            }
            const content = document.createElement('div');
            content.id = `tag_${tag}`;
            panel.append(content);
            group.append(tab, panel);
            htmx.process(tab);
        }
        return tab;
    }

    function waitForTarget(find, timeout = 15000) {
        const existing = find();
        if (existing) return Promise.resolve(existing);
        return new Promise(resolve => {
            const observer = new MutationObserver(() => {
                const target = find();
                if (target) finish(target);
            });
            const timer = window.setTimeout(() => finish(null), timeout);
            function finish(target) {
                observer.disconnect();
                clearTimeout(timer);
                resolve(target);
            }
            observer.observe(document.body, {childList: true, subtree: true});
        });
    }

    async function openTargetFromUrl() {
        const query = new URLSearchParams(window.location.search);
        const hashPath = decodeHashPath();
        const style = hashPath?.[3] || query.get('target_style');
        const hashTopicIsKnown = hashPath?.length === 1
            && topics.some(item => item.name === hashPath[0]);
        const topicName = (hashTopicIsKnown || hashPath?.length > 1 ? hashPath?.[0] : null)
            || query.get('target_topic');
        const subtopicName = hashPath?.[1] || query.get('target_subtopic');
        const tag = hashPath?.[2] || query.get('target_tag');
        if (!topicName && style) {
            const baseStylePrompt = query.get('base_style_prompt') || 'classic';
            const cardTag = tag || style;
            let section = picker.querySelector('[data-styler-search-target]');
            if (!section) {
                section = document.createElement('section');
                section.className = 'wa-stack';
                section.dataset.stylerSearchTarget = '';
                section.innerHTML = '<h2>Search result <small>(not in the topic tree)</small></h2>';
                picker.append(section);
            }
            const response = await fetch(`/styles/target_card?style=${encodeURIComponent(style)}&tag=${encodeURIComponent(cardTag)}&base_style_prompt=${encodeURIComponent(baseStylePrompt)}`);
            if (!response.ok) throw new Error(`Style card could not be loaded (${response.status})`);
            section.querySelectorAll('[data-style-entry]').forEach(entry => entry.remove());
            section.insertAdjacentHTML('beforeend', await response.text());
            const card = section.querySelector(`#card_${CSS.escape(style)}`);
            if (!card) throw new Error(`Style ${style} did not load`);
            htmx.process(card);
            const details = card.querySelector('wa-details');
            if (details) {
                const detailContent = details.querySelector(`#style_details_${CSS.escape(style)}`);
                details.removeAttribute('hx-get');
                details.removeAttribute('hx-trigger');
                details.open = true;
                if (detailContent && !detailContent.childElementCount && cardTag !== style) {
                    const params = new URLSearchParams({style_tag: style, tag: cardTag, base_style_prompt: baseStylePrompt});
                    const detailResponse = await fetch(`/styles/style_details?${params}`);
                    if (!detailResponse.ok) throw new Error(`Style controls could not be loaded (${detailResponse.status})`);
                    detailContent.innerHTML = await detailResponse.text();
                    htmx.process(detailContent);
                }
            }
            const heading = card.querySelector('[data-style-name-text]') || card;
            heading.style.scrollMarginBlockStart = '5rem';
            heading.scrollIntoView({behavior: 'smooth', block: 'start', inline: 'nearest'});
            return;
        }
        if (!topicName) return;
        const topic = topics.find(item => item.name === topicName);
        const topicTab = [...topicTabs.querySelectorAll('wa-tab[data-topic-name]')]
            .find(item => item.dataset.topicName === topicName);
        if (!topic || !topicTab) throw new Error(`Topic ${topicName} is not available`);
        topicTab.click();
        if (!subtopicName) return;
        const childTabs = categoriesPanel(topicName);
        const subtopicTab = await waitForTarget(() => {
            // selectTopic inserts tabs before custom elements are ready and
            // before their wa-tab-show handler is attached. Wait for both so
            // the next click cannot be lost during initial deep-link restore.
            if (childTabs?.dataset.stylerHandler !== 'true') return null;
            return [...(childTabs?.querySelectorAll('wa-tab[data-subtopic-name]') || [])]
                .find(item => item.dataset.subtopicName === subtopicName);
        });
        if (!subtopicTab) throw new Error(`Category ${subtopicName} is not available`);
        subtopicTab.click();
        const group = await waitForTarget(() => categoryGroup(topicName));
        if (!group) throw new Error(`Tags in ${subtopicName} did not load`);
        if (!tag) return;
        let tagTab = await waitForTarget(() => [...group.querySelectorAll('wa-tab[data-style-category]')]
            .find(item => item.dataset.styleCategory === tag), 3000);
        if (!tagTab) {
            tagTab = addTagTab(tag, group);
            tagTab.dataset.parentTopic = topicName;
            tagTab.dataset.subtopicName = subtopicName;
        }
        tagTab.click();
        group.querySelectorAll('wa-tab[data-style-category]').forEach(tab => {
            tab.toggleAttribute('data-target-selected', tab === tagTab);
        });
        if (!style) return;
        let card = await waitForTarget(() => document.getElementById(`card_${style}`), 2500);
        if (!card) {
            const baseStylePrompt = query.get('base_style_prompt')
                || group.dataset.baseStylePrompt || 'classic';
            const response = await fetch(`/styles/target_card?style=${encodeURIComponent(style)}&tag=${encodeURIComponent(tag)}&base_style_prompt=${encodeURIComponent(baseStylePrompt)}`);
            if (!response.ok) throw new Error(`Style card could not be loaded (${response.status})`);
            const markup = await response.text();
            const content = group.querySelector(`#tag_${CSS.escape(tag)}`);
            if (!content) throw new Error(`Tag panel ${tag} is not available`);
            if (!document.getElementById(`card_${style}`)) content.insertAdjacentHTML('beforeend', markup);
            card = document.getElementById(`card_${style}`);
            if (card) {
                htmx.process(card);
                setStylePermalinks(content);
            }
        }
        if (!card) throw new Error(`Style ${style} did not load`);
        const details = card.querySelector('wa-details');
        if (details) {
            const detailContent = details.querySelector(`#style_details_${CSS.escape(style)}`);
            details.removeAttribute('hx-get');
            details.removeAttribute('hx-trigger');
            details.open = true;
            if (detailContent && !detailContent.childElementCount) {
                const baseStylePrompt = query.get('base_style_prompt')
                    || group.dataset.baseStylePrompt || 'classic';
                const params = new URLSearchParams({
                    style_tag: style, tag, base_style_prompt: baseStylePrompt,
                });
                const response = await fetch(`/styles/style_details?${params}`);
                if (!response.ok) throw new Error(`Style controls could not be loaded (${response.status})`);
                detailContent.innerHTML = await response.text();
                htmx.process(detailContent);
            }
        }
        const heading = card.querySelector('[data-style-name-text]') || card;
        heading.style.scrollMarginBlockStart = '5rem';
        heading.scrollIntoView({behavior: 'smooth', block: 'start', inline: 'nearest'});
        heading.classList.add('styler-queue-destination-focus');
        window.setTimeout(() => heading.classList.remove('styler-queue-destination-focus'), 1500);
    }

    new MutationObserver(records => {
        for (const record of records) {
            for (const node of record.addedNodes) {
                if (node.nodeType === Node.ELEMENT_NODE) setStylePermalinks(node);
            }
        }
    }).observe(picker, {childList: true, subtree: true});

    picker.addEventListener('wa-tab-show', event => {
        const group = event.target.closest?.('[data-category-tab-group]');
        if (!group) return;
        group.querySelectorAll('wa-tab[data-style-category]').forEach(tab => {
            if (tab !== event.target) tab.removeAttribute('data-target-selected');
        });
        setStylePermalinks();
    });
    window.addEventListener('hashchange', () => {
        if (!decodeHashPath() || !topics.length) return;
        openTargetFromUrl().catch(error => {
            status.textContent = `Unable to open style link: ${error.message}`;
        });
    });

    async function selectTopic(topic) {
        selectedTopic = topic.name;
        const childTabs = categoriesPanel(topic.name);
        if (!childTabs) return;
        childTabs.replaceChildren();
        childTabs.active = '';
        picker.querySelectorAll('[data-category-tab-group]').forEach(group => group.remove());
        topicTabs.querySelectorAll('[data-topic-name]').forEach(button => {
            button.setAttribute('aria-selected', String(button.dataset.topicName === selectedTopic));
        });
        status.textContent = `Loading categories under ${topic.name}…`;
        try {
            const response = await fetch(`/styles/topics/${encodeURIComponent(topic.name)}/subtopics`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
            const subtopics = data.topics || [];
            if (!subtopics.length) {
                status.textContent = `No subtopics could be formed for ${topic.name}.`;
                return;
            }
            subtopics.forEach((subtopic, index) => {
                const panelName = `subtopic_panel_${index}`;
                const tab = document.createElement('wa-tab');
                tab.dataset.subtopicName = subtopic.name;
                tab.setAttribute('panel', panelName);
                tab.textContent = subtopic.name;
                const panel = document.createElement('wa-tab-panel');
                panel.setAttribute('name', panelName);
                const placeholder = document.createElement('div');
                placeholder.dataset.categoryPlaceholder = '';
                placeholder.setAttribute('role', 'status');
                placeholder.textContent = 'Tags will appear here when this category is selected.';
                panel.append(placeholder);
                childTabs.append(tab, panel);
            });
            childTabs.dataset.parentTopic = topic.name;
            childTabs._stylerSubtopics = subtopics;
            await customElements.whenDefined('wa-tab');
            await customElements.whenDefined('wa-tab-panel');
            if (!childTabs.dataset.stylerHandler) {
                childTabs.dataset.stylerHandler = 'true';
                childTabs.addEventListener('wa-tab-show', event => {
                    // wa-tab-show bubbles from the nested tag tabs too. Only
                    // category-tab changes should reload this row's tag set.
                    if (event.target.closest('wa-tab-group') !== childTabs) return;
                    const activePanel = childTabs.active;
                    const activeTab = [...childTabs.querySelectorAll('wa-tab[data-subtopic-name]')]
                        .find(item => item.getAttribute('panel') === activePanel);
                    const activeSubtopic = childTabs._stylerSubtopics?.find(
                        item => item.name === activeTab?.dataset.subtopicName);
                    if (activeSubtopic) {
                        updateTopicHash([childTabs.dataset.parentTopic, activeSubtopic.name]);
                        selectSubtopic(childTabs.dataset.parentTopic, activeSubtopic);
                    }
                });
            }
            status.textContent = `${subtopics.length} categories in ${topic.name}. Choose a category to see its tags.`;
        } catch (error) {
            status.textContent = `Unable to load subtopics: ${error.message}`;
        }
    }

    async function selectSubtopic(parentName, subtopic) {
        const previousGroup = categoryGroup(parentName);
        if (previousGroup) {
            const priorPlaceholder = document.createElement('div');
            priorPlaceholder.dataset.categoryPlaceholder = '';
            priorPlaceholder.setAttribute('role', 'status');
            priorPlaceholder.textContent = 'Tags will appear here when this category is selected.';
            previousGroup.replaceWith(priorPlaceholder);
        }
        const panel = subtopicPanel(parentName, subtopic.name);
        const placeholder = ensureCategoryPlaceholder(panel);
        if (!placeholder) return;
        placeholder.textContent = `Loading tags in ${subtopic.name}…`;
        status.textContent = `Loading tags in ${subtopic.name}…`;
        try {
            const query = new URLSearchParams({base_style_prompt: promptSelect?.value || 'classic'});
            const url = `/styles/topics/${encodeURIComponent(parentName)}/subtopics/${encodeURIComponent(subtopic.name)}/tags?${query}`;
            await htmx.ajax('GET', url, {target: placeholder, swap: 'outerHTML'});
            const loaded = categoryGroup(parentName);
            if (loaded) {
                loaded.dataset.parentTopic = parentName;
                loaded.dataset.subtopicName = subtopic.name;
                loaded.querySelectorAll('wa-tab[data-style-category]').forEach(tab => {
                    tab.dataset.parentTopic = parentName;
                    tab.dataset.subtopicName = subtopic.name;
                });
                htmx.process(loaded);
                const count = loaded.querySelectorAll('wa-tab[data-style-category]').length;
                status.textContent = count
                    ? `${count} tags in ${subtopic.name}. Choose a tag tab to view its styles.`
                    : `No raw tags found in ${subtopic.name}.`;
            }
        } catch (error) {
            status.textContent = `Unable to load raw tags: ${error.message}`;
        }
    }

    async function buildTopics(refresh = false) {
        buildButton.disabled = true;
        if (refresh) {
            picker.querySelectorAll('[data-category-tab-group]').forEach(group => group.remove());
            buildButton.textContent = 'Queueing rebuild…';
            status.textContent = 'Queueing a full category tree rebuild for the background worker…';
        }
        try {
            const response = refresh
                ? await fetch('/styles/topics/rebuild', {method: 'POST'})
                : await fetch('/styles/topics');
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
            if (refresh) {
                buildButton.textContent = 'Refresh topics';
                status.textContent = data.state === 'already_queued'
                    ? 'A full category tree rebuild is already queued.'
                    : 'A full category tree rebuild is queued for the background worker.';
                return;
            }
            topics = data.topics;
            topicTabs.replaceChildren();
            topicTabs.active = '';
            selectedTopic = null;
            topics.forEach((topic, index) => {
                const panelName = `topic_panel_${index}`;
                const tab = document.createElement('wa-tab');
                tab.dataset.topicName = topic.name;
                tab.setAttribute('panel', panelName);
                tab.textContent = topic.name;
                if (topic.description) tab.title = topic.description;
                const panel = document.createElement('wa-tab-panel');
                panel.setAttribute('name', panelName);
                const categories = document.createElement('wa-tab-group');
                categories.className = 'styler-subtopic-tabs';
                categories.setAttribute('aria-label', `Categories in ${topic.name}`);
                categories.dataset.topicCategories = '';
                panel.append(categories);
                topicTabs.append(tab, panel);
            });
            if (topics.length) {
                await customElements.whenDefined('wa-tab');
                await customElements.whenDefined('wa-tab-panel');
            }
            if (decodeHashPath() || new URLSearchParams(window.location.search).has('target_style')) {
                try {
                    await openTargetFromUrl();
                } catch (error) {
                    status.textContent = `Unable to find queued style: ${error.message}`;
                }
            }
            buildButton.textContent = data.ready ? 'Refresh topics' : 'Build topics';
            status.textContent = topics.length ? '' : data.building
                ? 'The background worker is building the category tree.'
                : 'No completed category tree is available yet.';
        } catch (error) {
            buildButton.textContent = 'Build topics';
            status.textContent = `Unable to build topics: ${error.message}`;
        } finally {
            buildButton.disabled = false;
        }
    }

    topicTabs.addEventListener('wa-tab-show', () => {
        const activePanel = topicTabs.active;
        const tab = [...topicTabs.querySelectorAll('wa-tab[data-topic-name]')]
            .find(item => item.getAttribute('panel') === activePanel);
        const topic = topics.find(item => item.name === tab?.dataset.topicName);
        if (topic && selectedTopic !== topic.name) {
            updateTopicHash([topic.name]);
            selectTopic(topic);
        }
    });

    buildButton.addEventListener('click', () => buildTopics(true));
    buildTopics();
})();
