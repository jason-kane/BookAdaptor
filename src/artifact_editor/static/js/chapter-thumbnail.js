(() => {
    function initialize() {
        const workshop = document.getElementById('chapter-thumbnail-workshop');
        if (!workshop || workshop.dataset.initialized) return;
        workshop.dataset.initialized = 'true';
        const find = name => workshop.querySelector(`[data-thumbnail-${name}]`);
        const canvas = find('canvas');
        const context = canvas.getContext('2d');
        const status = find('status');
        let source = null;
        let width = 1280;
        let height = 720;
        let imageRequest = 0;
        let drag = null;
        // Video.js can replace the original video element, so resolve it each time.
        const video = () => document.querySelector('video.textoviva-video, #chapter-video video, video#chapter-video');
        function dimensions() {
            if (find('resolution').value === 'youtube') {
                width = 1280;
                height = 720;
                return true;
            }
            const player = video();
            if (!player?.videoWidth || !player.videoHeight) {
                status.textContent = 'Load a rendered chapter video in Textoviva first.';
                return false;
            }
            width = player.videoWidth;
            height = player.videoHeight;
            return true;
        }
        function framing() {
            const sw = source.naturalWidth || source.width;
            const sh = source.naturalHeight || source.height;
            const scale = Math.max(width / sw, height / sh) * (Number(find('zoom').value) / 100);
            return {imageWidth: sw * scale, imageHeight: sh * scale};
        }
        function resetFraming() {
            find('zoom').value = '100';
            find('pan-x').value = '0';
            find('pan-y').value = '0';
            drag = null;
            canvas.style.cursor = 'grab';
            draw();
        }
        function draw() {
            if (!source) return;
            canvas.width = width;
            canvas.height = height;
            const {imageWidth, imageHeight} = framing();
            const x = (width - imageWidth) / 2 * (1 - Number(find('pan-x').value) / 100);
            const y = (height - imageHeight) / 2 * (1 - Number(find('pan-y').value) / 100);
            context.drawImage(source, x, y, imageWidth, imageHeight);
            find('zoom-label').textContent = `${find('zoom').value}%`;
            const title = find('title').value.trim();
            if (title) {
                const size = height * Number(find('size').value) / 100;
                context.font = `bold ${size}px sans-serif`;
                context.textAlign = 'center';
                context.textBaseline = 'middle';
                context.lineJoin = 'round';
                context.strokeStyle = '#000000';
                context.lineWidth = size * 0.045;
                context.fillStyle = find('color').value;
                const lines = [];
                let line = '';
                for (const word of title.split(/\s+/)) {
                    const next = line ? `${line} ${word}` : word;
                    if (line && context.measureText(next).width > width * 0.88) {
                        lines.push(line);
                        line = word;
                    } else line = next;
                }
                if (line) lines.push(line);
                const lineHeight = size * 1.2;
                const blockHeight = lines.length * lineHeight;
                const position = find('position').value;
                const top = position === 'top' ? height * 0.08 : position === 'middle' ? (height - blockHeight) / 2 : height * 0.92 - blockHeight;
                lines.forEach((text, index) => {
                    const y = top + lineHeight * (index + 0.5);
                    context.strokeText(text, width / 2, y, width * 0.88);
                    context.fillText(text, width / 2, y, width * 0.88);
                });
            }
            const words = title ? title.split(/\s+/).length : 0;
            find('copy-status').textContent = words > 5
                ? `${words} words · Try shortening the text to 2–5 words for mobile readability.`
                : `${words} words · Make the thumbnail text complement the video title.`;
            canvas.hidden = false;
            find('stage').hidden = false;
            find('mobile').hidden = false;
            const small = find('small');
            small.height = Math.round(small.width * height / width);
            small.getContext('2d').drawImage(canvas, 0, 0, small.width, small.height);
            find('download').disabled = false;
            status.textContent = `${width} × ${height} pixels · ${find('format').value.toUpperCase()}`;
        }
        find('capture').addEventListener('click', () => {
            const player = video();
            if (!player?.videoWidth || !player.videoHeight) {
                status.textContent = 'Load a rendered chapter video in Textoviva first.';
                return;
            }
            if (!dimensions()) return;
            if (player.readyState < 2 || player.seeking) {
                status.textContent = 'Wait for the video frame to finish loading, then capture again.';
                return;
            }
            try {
                player.pause();
                const frame = document.createElement('canvas');
                frame.width = player.videoWidth;
                frame.height = player.videoHeight;
                frame.getContext('2d').drawImage(player, 0, 0);
                source = frame;
                imageRequest++;
                resetFraming();
            } catch (error) {
                status.textContent = 'Could not capture this frame. Try choosing an image instead.';
            }
        });
        find('image').addEventListener('change', () => {
            const file = find('image').files[0];
            if (!file || !dimensions()) { find('image').value = ''; return; }
            const request = ++imageRequest;
            const url = URL.createObjectURL(file);
            const image = new Image();
            image.onload = () => {
                URL.revokeObjectURL(url);
                if (request !== imageRequest) return;
                source = image;
                resetFraming();
            };
            image.onerror = () => {
                URL.revokeObjectURL(url);
                if (request === imageRequest) status.textContent = 'This image could not be opened. Choose another image.';
            };
            image.src = url;
        });
        for (const name of ['zoom', 'pan-x', 'pan-y']) {
            find(name).addEventListener('input', draw);
        }
        find('reset').addEventListener('click', resetFraming);
        canvas.addEventListener('pointerdown', event => {
            if (!source || event.button !== 0 || event.isPrimary === false) return;
            const bounds = canvas.getBoundingClientRect();
            if (!bounds.width || !bounds.height) return;
            drag = {
                id: event.pointerId, x: event.clientX, y: event.clientY,
                panX: Number(find('pan-x').value), panY: Number(find('pan-y').value),
                scaleX: width / bounds.width, scaleY: height / bounds.height,
                ...framing(),
            };
            canvas.setPointerCapture(event.pointerId);
            canvas.style.cursor = 'grabbing';
        });
        canvas.addEventListener('pointermove', event => {
            if (!drag || drag.id !== event.pointerId) return;
            const clamp = value => Math.max(-100, Math.min(100, value));
            const overflowX = drag.imageWidth - width;
            const overflowY = drag.imageHeight - height;
            find('pan-x').value = String(overflowX > 0
                ? clamp(drag.panX + (event.clientX - drag.x) * drag.scaleX * 200 / overflowX) : 0);
            find('pan-y').value = String(overflowY > 0
                ? clamp(drag.panY + (event.clientY - drag.y) * drag.scaleY * 200 / overflowY) : 0);
            draw();
        });
        function endDrag(event) {
            if (!drag || drag.id !== event.pointerId) return;
            drag = null;
            canvas.style.cursor = 'grab';
            if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
        }
        for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(event, endDrag);
        for (const name of ['title', 'color', 'size', 'position']) {
            for (const event of ['input', 'change', 'wa-input', 'wa-change']) find(name).addEventListener(event, draw);
        }
        for (const event of ['change', 'wa-change']) {
            find('resolution').addEventListener(event, () => { if (dimensions()) draw(); });
            find('format').addEventListener(event, draw);
        }
        find('guides').addEventListener('change', () => {
            find('overlay').hidden = !find('guides').checked;
        });
        find('download').addEventListener('click', () => {
            if (!source) return;
            try {
                const format = find('format').value;
                const mime = format === 'jpeg' ? 'image/jpeg' : 'image/png';
                const save = (blob, quality) => {
                    if (!blob) { status.textContent = 'Could not create the image. Try again.'; return; }
                    if (blob.size >= 2 * 1024 * 1024) {
                        if (format === 'jpeg' && quality > 0.5) {
                            canvas.toBlob(next => save(next, quality - 0.1), mime, quality - 0.1);
                        } else {
                            status.textContent = 'Export exceeds 2 MB. Choose JPEG or simplify the image and try again.';
                        }
                        return;
                    }
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.href = url;
                    link.download = workshop.dataset.filename.replace(/\.png$/, format === 'jpeg' ? '.jpg' : '.png');
                    link.click();
                    status.textContent = `${width} × ${height} · ${format.toUpperCase()} · ${(blob.size / 1024).toFixed(0)} KB`;
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                };
                canvas.toBlob(blob => save(blob, 0.92), mime, 0.92);
            } catch (error) {
                status.textContent = 'Could not export this frame. Choose a local image and try again.';
            }
        });
    }
    document.addEventListener('DOMContentLoaded', initialize);
    document.addEventListener('htmx:load', initialize);
    initialize();
})();
