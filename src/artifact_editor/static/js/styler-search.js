(() => {
    const root = document.querySelector('[data-styler-search]');
    if (!root) return;
    const input = root.querySelector('#styler-search-input');
    const results = root.querySelector('#styler-search-results');
    let timer = null;
    let request = null;
    let options = [];
    let activeIndex = -1;

    function close() {
        results.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        activeIndex = -1;
    }

    function select(index) {
        const item = options[index];
        if (!item) return;
        if (item.kind === 'style') {
            const url = new URL('/styles/find_target', window.location.origin);
            url.searchParams.set('style', item.style_tag);
            for (const key of ['target_engine', 'target_action', 'base_style_prompt']) {
                const value = new URLSearchParams(window.location.search).get(key);
                if (value) url.searchParams.set(key.replace(/^target_/, ''), value);
            }
            window.location.assign(url.href);
        } else {
            const path = item.kind === 'topic' ? [item.parent]
                : item.kind === 'category' ? [item.parent, item.category]
                    : [item.parent, item.category, item.name];
            window.location.hash = path.map(encodeURIComponent).join(',');
        }
        close();
    }

    function render(items, query) {
        options = items;
        activeIndex = -1;
        results.replaceChildren();
        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'styler-typeahead-empty';
            empty.textContent = `No matches for “${query}”.`;
            results.append(empty);
        } else {
            items.forEach((item, index) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.id = `styler-search-option-${index}`;
                button.className = 'styler-typeahead-option';
                button.setAttribute('role', 'option');
                button.setAttribute('aria-selected', 'false');
                const name = document.createElement('span');
                name.textContent = item.name;
                const kind = document.createElement('span');
                kind.className = 'styler-typeahead-kind';
                kind.textContent = ({topic: 'Topic', category: 'Category', tag: 'Tag', style: 'Style'})[item.kind];
                button.append(name, kind);
                const location = item.kind === 'category' || item.kind === 'tag'
                    ? `${item.parent} › ${item.category}` : '';
                if (location) button.title = location;
                button.addEventListener('mousedown', event => event.preventDefault());
                button.addEventListener('click', () => select(index));
                results.append(button);
            });
        }
        results.hidden = false;
        input.setAttribute('aria-expanded', 'true');
    }

    async function search(query) {
        request?.abort();
        request = new AbortController();
        try {
            const response = await fetch(`/styles/search_suggestions?q=${encodeURIComponent(query)}`, {
                signal: request.signal, headers: {Accept: 'application/json'},
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `Search failed (${response.status})`);
            if (input.value.trim() === query) render(data.results || [], query);
        } catch (error) {
            if (error.name !== 'AbortError') {
                options = [];
                results.replaceChildren();
                const message = document.createElement('div');
                message.className = 'styler-typeahead-empty';
                message.textContent = 'Search is unavailable.';
                results.append(message);
                results.hidden = false;
                input.setAttribute('aria-expanded', 'true');
            }
        }
    }

    input.addEventListener('input', () => {
        clearTimeout(timer);
        const query = input.value.trim();
        if (query.length < 2) {
            request?.abort();
            close();
            return;
        }
        timer = window.setTimeout(() => search(query), 140);
    });
    input.addEventListener('keydown', event => {
        if (results.hidden) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const direction = event.key === 'ArrowDown' ? 1 : -1;
            activeIndex = (activeIndex + direction + options.length) % options.length;
            [...results.querySelectorAll('[role="option"]')].forEach((option, index) => {
                option.setAttribute('aria-selected', String(index === activeIndex));
            });
            input.setAttribute('aria-activedescendant', `styler-search-option-${activeIndex}`);
        } else if (event.key === 'Enter' && activeIndex >= 0) {
            event.preventDefault();
            select(activeIndex);
        } else if (event.key === 'Escape') {
            close();
        }
    });
    input.addEventListener('blur', () => window.setTimeout(close, 120));
    document.addEventListener('click', event => {
        if (!root.contains(event.target)) close();
    });
})();
