document.addEventListener('chapter-typeset-complete', event => {
    const stamp = Date.now();
    if (event.detail.pdf_url && window.latex_rerender_url) {
        const pdf = new URL(event.detail.pdf_url, window.location.href);
        pdf.searchParams.set('t', stamp);
        window.latex_rerender_url(pdf.href);
    }
    for (const image of document.querySelectorAll('[data-typeset-preview]')) {
        const url = new URL(image.src, window.location.href);
        url.searchParams.set('t', stamp);
        image.src = url.href;
    }
});
