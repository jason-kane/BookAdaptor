(() => {
    const tiers = ['F', 'D', 'C', 'B', 'A', 'S', 'SS', 'SSS'];
    const ready = customElements.whenDefined('wa-slider');
    function configure(root) {
        ready.then(() => {
            const sliders = [...root.querySelectorAll('.style-tier-slider')];
            if (root.matches?.('.style-tier-slider')) sliders.push(root);
            for (const slider of sliders) {
                slider.valueFormatter = value => `${tiers[Math.round(Number(value))]} Tier`;
            }
        });
    }
    document.addEventListener('htmx:load', event => configure(event.detail.elt));
    configure(document);
})();
