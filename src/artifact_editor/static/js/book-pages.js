import Quill from '/static/js/quill.js';

const sizes = ['12px', '14px', '16px', '18px', '24px', '32px', '48px'];
const sizeFormat = Quill.import('attributors/style/size');
sizeFormat.whitelist = sizes;
Quill.register(sizeFormat, true);
const fonts = ['Helvetica', 'Cardo', 'Roboto Condensed', 'Times New Roman', 'Calibri', 'Calibri Light'];
const fontFormat = Quill.import('attributors/style/font');
fontFormat.whitelist = fonts;
Quill.register(fontFormat, true);

for (const template of document.querySelectorAll('.book-page-template')) {
    const editor = new Quill(template.querySelector('.book-page-editor'), {
        theme: 'snow',
        modules: {toolbar: [[{font: fonts}, {size: sizes}], ['bold', 'italic', 'underline'], [{align: []}], ['clean']]}
    });
    editor.setContents(JSON.parse(template.querySelector('.page-delta').textContent));
    const status = template.querySelector('.page-save-status');
    editor.on('text-change', () => { status.textContent = 'Unsaved changes'; });
    template.querySelector('.save-page-template').addEventListener('click', async event => {
        const button = event.currentTarget;
        button.loading = true;
        const contents = editor.getContents();
        try {
            const response = await fetch(template.dataset.saveUrl, {
                method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(contents)
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Could not save page');
            status.textContent = JSON.stringify(editor.getContents()) === JSON.stringify(contents) ? 'Saved' : 'Unsaved changes';
        } catch (error) {
            status.textContent = error.message;
        } finally { button.loading = false; }
    });
}

const panel = document.getElementById('book-textoviva');
if (panel) {
    const button = document.getElementById('create-book-video');
    const status = document.getElementById('book-textoviva-status');
    const video = document.getElementById('book-video');
    let lastJob = '';
    let submitting = false;
    let polling = false;
    let requestError = '';
    async function refresh() {
        if (polling) return;
        polling = true;
        try {
            const response = await fetch(panel.dataset.statusUrl, {cache: 'no-store'});
            if (!response.ok) throw new Error('Could not check book video status');
            const data = await response.json();
            const job = data.job || {};
            const busy = ['queued', 'running', 'cancelling'].includes(job.state);
            document.getElementById('book-textoviva-tab').disabled = !data.enabled;
            document.getElementById('book-video-availability').textContent = data.enabled ? '' :
                `Textoviva requires a video for every chapter.${data.missing.length ? ' Missing: ' + data.missing.join(', ') + '.' : ''}`;
            button.disabled = submitting || busy || !data.enabled;
            button.loading = submitting || busy;
            status.textContent = requestError || (job.state === 'queued' ? 'Queued in CPU Queue' :
                job.state === 'running' ? 'Assembling all chapters from saved frames and narration…' :
                job.state === 'complete' ? 'Book video ready' : job.message || '');
            if (data.ready) {
                video.hidden = false;
                document.getElementById('book-video-download').hidden = false;
                if (job.state === 'complete' && job.job_id !== lastJob) {
                    video.src = `${panel.dataset.videoUrl}?v=${encodeURIComponent(job.job_id)}`;
                    video.load();
                    lastJob = job.job_id;
                }
            }
        } catch (error) {
            status.textContent = error.message;
        } finally { polling = false; }
    }
    button.addEventListener('click', async () => {
        submitting = true;
        requestError = '';
        button.disabled = true;
        button.loading = true;
        status.textContent = 'Submitting…';
        try {
            const response = await fetch(panel.dataset.statusUrl, {method: 'POST'});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Book video could not be queued');
        } catch (error) { requestError = error.message; }
        finally { submitting = false; await refresh(); }
    });
    refresh();
    const bookTabs = document.getElementById('book-tabs');
    bookTabs.addEventListener('wa-tab-show', event => {
        if (event.target.closest('wa-tab-group') === bookTabs && bookTabs.active === 'textoviva') refresh();
    });
    window.setInterval(() => {
        if (!document.hidden && bookTabs.active === 'textoviva') refresh();
    }, 5000);
}
