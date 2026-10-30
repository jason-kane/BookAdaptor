(() => {
    window.ImageLeaves = {
        create(stage) {
            let nextRevealAt = 0;
            return {
                reset() { nextRevealAt = performance.now(); },
                show(item) {
                    if (!stage || !item.url) return;
                    const card = document.createElement('figure');
                    card.className = `image-leaf${Number(item.index) % 2 ? ' from-right' : ''}`;
                    card.setAttribute('aria-hidden', 'true');
                    const image = document.createElement('img');
                    image.alt = '';
                    image.decoding = 'async';
                    const remove = () => card.remove();
                    image.addEventListener('error', remove, {once: true});
                    image.src = item.url;
                    card.append(image);
                    const now = performance.now();
                    const delay = Math.max(0, nextRevealAt - now);
                    card.style.setProperty('--leaf-delay', `${delay}ms`);
                    nextRevealAt = now + delay + 650;
                    card.addEventListener('animationend', remove, {once: true});
                    stage.append(card);
                    // Also clean up when a browser suppresses animation events.
                    window.setTimeout(remove, delay + 8500);
                },
            };
        },
    };
})();
