(() => {
    const start = () => {
        const group = document.querySelector('#chapter-preview-content wa-tab-group');
        const imageScroll = group?.querySelector('wa-tab-panel[name="image"] .chapter-rendered-scroll');
        const rainbowScroll = group?.querySelector('wa-tab-panel[name="rainbow"] .chapter-rendered-scroll');
        if (!group || !imageScroll || !rainbowScroll) return;

        let savedTop = 0;
        let restoring = false;
        const save = event => {
            if (!restoring) savedTop = event.currentTarget.scrollTop;
        };
        imageScroll.addEventListener('scroll', save, {passive: true});
        rainbowScroll.addEventListener('scroll', save, {passive: true});

        const restoreToActivePanel = () => {
            requestAnimationFrame(() => requestAnimationFrame(() => {
                const active = group.active || group.getAttribute('active');
                const destination = active === 'image' ? imageScroll : active === 'rainbow' ? rainbowScroll : null;
                if (!destination) return;
                restoring = true;
                destination.scrollTop = savedTop;
                savedTop = destination.scrollTop;
                requestAnimationFrame(() => { restoring = false; });
            }));
        };

        group.addEventListener('wa-tab-show', event => {
            if (event.target.closest?.('wa-tab-group') === group) restoreToActivePanel();
        });
        group.querySelectorAll('wa-tab[panel="image"], wa-tab[panel="rainbow"]').forEach(tab => {
            tab.addEventListener('click', restoreToActivePanel);
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
})();
