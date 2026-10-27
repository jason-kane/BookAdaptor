(() => {
    const dialog = document.getElementById('styler-image-viewer');
    const fullImage = document.getElementById('styler-image-full');
    const previous = document.getElementById('styler-image-previous');
    const next = document.getElementById('styler-image-next');
    let viewed = null;
    const progress = document.getElementById('styler-viewer-progress');
    const progressLabel = document.getElementById('styler-viewer-progress-label');
    const progressTags = document.getElementById('styler-viewer-progress-tags');
    const progressBar = document.getElementById('styler-viewer-progress-bar');
    const engineNames = {zit: 'Z-Image Turbo', krea2: 'Krea 2', qwen: 'Qwen Image 2.1'};
    let drawing = null;
    let dissolveTimer = null;
    document.addEventListener('styler:sample-status', ({detail: event}) => {
        if (event.state === 'running') {
            clearTimeout(dissolveTimer);
            progress.classList.remove('styler-progress-dissolving');
            drawing = event;
            window.TaskTiming?.bind(progressBar, event, progressLabel,
                {prefix: `${event.style_name} — ${engineNames[event.engine] || event.engine}: ${event.message || 'Generating sample'}`});
            progressTags.replaceChildren(...(event.tags || []).map(text => {
                const tag = document.createElement('wa-tag');
                tag.setAttribute('size', 's');
                tag.setAttribute('pill', '');
                tag.textContent = text;
                return tag;
            }));
            progressTags.hidden = !progressTags.childElementCount;
            progress.hidden = false;
        } else if (drawing?.style_tag === event.style_tag && drawing?.engine === event.engine
            && ['complete', 'loaded'].includes(event.state)) {
            progressBar.removeAttribute('indeterminate');
            progressBar.setAttribute('value', 100);
            window.TaskTiming?.bind(progressBar, event, progressLabel,
                {prefix: `${drawing.style_name} — ${engineNames[drawing.engine] || drawing.engine}`});
            drawing = null;
            progress.classList.add('styler-progress-dissolving');
            dissolveTimer = window.setTimeout(() => {
                progress.hidden = true;
                progress.classList.remove('styler-progress-dissolving');
                dissolveTimer = null;
            }, 650);
        } else if (drawing?.style_tag === event.style_tag && drawing?.engine === event.engine) {
            drawing = null;
            progress.hidden = true;
        }
    });
    function centerViewed() {
        if (viewed?.isConnected) {
            viewed.scrollIntoView({behavior: 'instant', block: 'center', inline: 'nearest'});
        }
    }
    function navigate(direction = 0) {
        // Re-read on navigation or completion, so newly completed samples join
        // the gallery without any work on idle cards.
        const images = [...document.querySelectorAll('[data-style-viewer]')].filter(button => {
            const panel = button.closest('wa-tab-panel');
            return button === viewed || !panel || (!panel.hidden && panel.active !== false);
        });
        const position = images.indexOf(viewed);
        const index = position + direction;
        if (position < 0 || !images[index]) return;
        viewed = images[index];
        const image = viewed.querySelector('img');
        fullImage.src = image.src;
        fullImage.alt = image.alt;
        dialog.label = viewed.getAttribute('aria-label');
        previous.disabled = index === 0;
        next.disabled = index === images.length - 1;
        centerViewed();
    }
    // Closing the dialog can restore focus to its original opener. Recenter
    // the current image after that restoration as well.
    dialog.addEventListener('wa-after-hide', centerViewed);
    document.addEventListener('styler:sample-complete', event => {
        if (!dialog.open) return;
        const opener = event.target.closest('[data-style-viewer]');
        if (!opener) return;
        viewed = opener;
        navigate();
    });
    document.addEventListener('click', event => {
        const opener = event.target.closest('[data-style-viewer]');
        if (opener) {
            viewed = opener;
            navigate();
            dialog.open = true;
        }
        if (event.target.closest('#styler-image-previous')) navigate(-1);
        if (event.target.closest('#styler-image-next')) navigate(1);
    });
    document.addEventListener('keydown', event => {
        if (!dialog.open) return;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault();
            navigate(event.key === 'ArrowLeft' ? -1 : 1);
        }
    });
})();
