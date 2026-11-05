const sourceImagePickers = new WeakMap();

function sourceCropBox(width, height, aspect, region) {
    const w = Math.min(width, height * aspect) / region.zoom;
    const h = w / aspect;
    const cx = Math.min(width - w / 2, Math.max(w / 2, region.x * width));
    const cy = Math.min(height - h / 2, Math.max(h / 2, region.y * height));
    return { x: cx - w / 2, y: cy - h / 2, w, h };
}

function sourceMotionBox(first, last, progress) {
    const t = progress * progress * (3 - 2 * progress);
    return Object.fromEntries(['x', 'y', 'w', 'h'].map(key => [key, first[key] + (last[key] - first[key]) * t]));
}

function newSourceCrop(width, height, aspect) {
    const w = Math.min(1, height * aspect / width);
    const h = Math.min(1, width / aspect / height);
    return { x: (1 - w) / 2, y: (1 - h) / 2, width: w, height: h };
}

function initializeSourcePicker(picker) {
    if (sourceImagePickers.has(picker) || !picker.querySelector('[data-source-editor]')) return;
    const image = picker.querySelector('[data-source-preview]');
    const canvas = picker.querySelector('[data-source-editor]');
    const field = picker.querySelector('input[name="source_motion"]');
    const selected = picker.querySelector('input[name="image_source"]');
    const state = {
        picker, image, canvas, field, selected,
        aspect: Number(picker.dataset.outputWidth) / Number(picker.dataset.outputHeight),
        motion: JSON.parse(field.value), active: 'first', progress: 0, playing: false,
        regions: new Map(), request: null, cropMode: false, crop: null, cropDrag: null,
    };
    sourceImagePickers.set(picker, state);
    state.regions.set(selected.value, state.motion);

    state.stop = () => {
        cancelAnimationFrame(state.request);
        state.playing = false;
        picker.querySelector('[data-source-play]').textContent = 'Preview motion';
    };
    const chooserToggle = picker.querySelector('[data-source-chooser-toggle]');
    const chooser = picker.querySelector('[data-source-chooser]');
    chooserToggle.addEventListener('click', () => {
        chooser.hidden = !chooser.hidden;
        chooserToggle.setAttribute('aria-expanded', String(!chooser.hidden));
        if (!chooser.hidden) state.paint();
    });
    function drawCrop(target, box) {
        const w = 480, h = Math.max(1, Math.round(w / state.aspect));
        if (target.width !== w || target.height !== h) { target.width = w; target.height = h; }
        const context = target.getContext('2d');
        context.clearRect(0, 0, w, h);
        context.drawImage(image, box.x, box.y, box.w, box.h, 0, 0, w, h);
    }
    state.paint = () => {
        if (!image.complete || !image.naturalWidth) return;
        const width = image.naturalWidth, height = image.naturalHeight;
        if (canvas.width !== width || canvas.height !== height) {
            canvas.width = width;
            canvas.height = height;
        }
        const context = canvas.getContext('2d');
        context.drawImage(image, 0, 0, width, height);
        const boxes = {};
        for (const frame of ['first', 'last']) {
            const region = state.motion[frame];
            const box = sourceCropBox(width, height, state.aspect, region);
            region.x = (box.x + box.w / 2) / width;
            region.y = (box.y + box.h / 2) / height;
            boxes[frame] = box;
        }
        const active = boxes[state.active];
        context.fillStyle = 'rgba(0,0,0,.48)';
        context.fillRect(0, 0, width, height);
        context.drawImage(image, active.x, active.y, active.w, active.h, active.x, active.y, active.w, active.h);
        const scale = Math.max(1, width / Math.max(1, canvas.getBoundingClientRect().width));
        for (const frame of ['first', 'last']) {
            const box = boxes[frame];
            context.strokeStyle = frame === 'first' ? '#7dd3fc' : '#fbbf24';
            context.lineWidth = (frame === state.active ? 3 : 1.5) * scale;
            context.setLineDash(frame === state.active ? [] : [6 * scale, 4 * scale]);
            context.strokeRect(box.x, box.y, box.w, box.h);
            context.setLineDash([]);
            context.fillStyle = context.strokeStyle;
            context.font = `${12 * scale}px sans-serif`;
            context.fillText(frame === 'first' ? 'First' : 'Last', box.x + 5 * scale, box.y + (frame === 'first' ? 16 : 32) * scale);
            drawCrop(picker.querySelector(`[data-source-frame="${frame}"]`), box);
            picker.querySelector(`[data-source-zoom="${frame}"]`).value = state.motion[frame].zoom;
            const percent = picker.querySelector(`[data-source-zoom-percent="${frame}"]`);
            if (document.activeElement !== percent) percent.value = Math.round(state.motion[frame].zoom * 100);
            const edit = picker.querySelector(`[data-source-edit="${frame}"]`);
            edit.setAttribute('aria-pressed', String(frame === state.active));
            edit.setAttribute('appearance', frame === state.active ? 'accent' : 'outlined');
        }
        if (state.cropMode && state.crop) {
            const box = state.crop;
            const x = box.x * width, y = box.y * height;
            const w = box.width * width, h = box.height * height;
            context.setLineDash([]);
            context.lineWidth = Math.max(2, 3 * scale);
            context.strokeStyle = '#000';
            context.strokeRect(x, y, w, h);
            context.lineWidth = Math.max(1, scale);
            context.strokeStyle = '#fff';
            context.strokeRect(x + scale, y + scale, w - 2 * scale, h - 2 * scale);
            context.fillStyle = '#000';
            for (const [hx, hy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) {
                context.fillRect(hx - 5 * scale, hy - 5 * scale, 10 * scale, 10 * scale);
                context.strokeStyle = '#fff';
                context.lineWidth = scale;
                context.strokeRect(hx - 5 * scale, hy - 5 * scale, 10 * scale, 10 * scale);
            }
        }
        field.value = JSON.stringify(state.motion);
        drawCrop(picker.querySelector('[data-source-motion-preview]'), sourceMotionBox(boxes.first, boxes.last, state.progress));
        picker.querySelector('[data-source-scrub]').value = Math.round(state.progress * 1000);
    };
    function position(event) {
        const bounds = canvas.getBoundingClientRect();
        return { x: (event.clientX - bounds.left) / bounds.width, y: (event.clientY - bounds.top) / bounds.height };
    }
    canvas.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !image.naturalWidth) return;
        state.stop();
        const point = position(event);
        if (state.cropMode) {
            const crop = state.crop;
            const px = point.x, py = point.y;
            const handleRadiusX = 12 / canvas.getBoundingClientRect().width;
            const handleRadiusY = 12 / canvas.getBoundingClientRect().height;
            const corners = {
                nw: [crop.x, crop.y], ne: [crop.x + crop.width, crop.y],
                sw: [crop.x, crop.y + crop.height], se: [crop.x + crop.width, crop.y + crop.height],
            };
            const handle = Object.entries(corners).find(([, p]) =>
                Math.abs(px - p[0]) <= handleRadiusX && Math.abs(py - p[1]) <= handleRadiusY
            )?.[0];
            if (handle) {
                state.cropDrag = { mode: handle, point, crop: { ...crop } };
            } else if (px >= crop.x && px <= crop.x + crop.width
                && py >= crop.y && py <= crop.y + crop.height) {
                state.cropDrag = { mode: 'move', point, crop: { ...crop } };
            } else {
                return;
            }
            canvas.setPointerCapture(event.pointerId);
            event.preventDefault();
            return;
        }
        const hits = ['first', 'last'].filter(frame => {
            const box = sourceCropBox(image.naturalWidth, image.naturalHeight, state.aspect, state.motion[frame]);
            return point.x * image.naturalWidth >= box.x && point.x * image.naturalWidth <= box.x + box.w
                && point.y * image.naturalHeight >= box.y && point.y * image.naturalHeight <= box.y + box.h;
        });
        if (hits.length === 1) state.active = hits[0];
        const region = state.motion[state.active];
        state.drag = { point, region: { ...region } };
        if (!hits.length) {
            region.x = point.x;
            region.y = point.y;
            state.drag.region = { ...region };
        }
        canvas.focus({ preventScroll: true });
        canvas.setPointerCapture(event.pointerId);
        state.paint();
        event.preventDefault();
    });
    canvas.addEventListener('pointermove', event => {
        if (state.cropDrag) {
            const point = position(event);
            const drag = state.cropDrag;
            const dx = point.x - drag.point.x, dy = point.y - drag.point.y;
            if (drag.mode === 'move') {
                state.crop.x = Math.max(0, Math.min(1 - drag.crop.width, drag.crop.x + dx));
                state.crop.y = Math.max(0, Math.min(1 - drag.crop.height, drag.crop.y + dy));
            } else {
                const opposite = {
                    nw: [drag.crop.x + drag.crop.width, drag.crop.y + drag.crop.height],
                    ne: [drag.crop.x, drag.crop.y + drag.crop.height],
                    sw: [drag.crop.x + drag.crop.width, drag.crop.y],
                    se: [drag.crop.x, drag.crop.y],
                }[drag.mode];
                const cornerX = drag.mode.endsWith('w') ? drag.crop.x + dx : drag.crop.x + drag.crop.width + dx;
                const cornerY = drag.mode.startsWith('n') ? drag.crop.y + dy : drag.crop.y + drag.crop.height + dy;
                const aspect = state.aspect * image.naturalHeight / image.naturalWidth;
                let w = Math.abs(cornerX - opposite[0]);
                let h = Math.abs(cornerY - opposite[1]);
                if (w / Math.max(h, 1e-6) > aspect) h = w / aspect;
                else w = h * aspect;
                w = Math.min(w, drag.mode.endsWith('w') ? opposite[0] : 1 - opposite[0]);
                h = w / aspect;
                h = Math.min(h, drag.mode.startsWith('n') ? opposite[1] : 1 - opposite[1]);
                w = h * aspect;
                const movingX = drag.mode.endsWith('w') ? opposite[0] - w : opposite[0];
                const movingY = drag.mode.startsWith('n') ? opposite[1] - h : opposite[1];
                state.crop = { x: movingX, y: movingY, width: w, height: h };
            }
            state.paint();
            return;
        }
        if (!state.drag) return;
        const point = position(event);
        const region = state.motion[state.active];
        region.x = state.drag.region.x + point.x - state.drag.point.x;
        region.y = state.drag.region.y + point.y - state.drag.point.y;
        state.paint();
    });
    const finish = () => { state.drag = null; state.cropDrag = null; };
    canvas.addEventListener('pointerup', finish);
    canvas.addEventListener('pointercancel', finish);
    canvas.addEventListener('lostpointercapture', finish);
    canvas.addEventListener('keydown', event => {
        const steps = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (!steps[event.key]) return;
        event.preventDefault();
        state.stop();
        const step = event.shiftKey ? .05 : .005;
        state.motion[state.active].x += steps[event.key][0] * step;
        state.motion[state.active].y += steps[event.key][1] * step;
        state.paint();
    });
    picker.querySelectorAll('[data-source-edit]').forEach(button => button.addEventListener('click', () => {
        state.active = button.dataset.sourceEdit;
        state.stop();
        state.paint();
    }));
    const cropStart = picker.querySelector('[data-source-crop-start]');
    const cropCancel = picker.querySelector('[data-source-crop-cancel]');
    const cropSave = picker.querySelector('[data-source-crop-save]');
    const cropField = picker.querySelector('input[name="source_crop"]');
    const cropHelp = picker.querySelector('[data-source-crop-help]');
    cropStart.addEventListener('click', () => {
        state.stop();
        state.cropMode = true;
        state.crop = newSourceCrop(image.naturalWidth, image.naturalHeight, state.aspect);
        cropStart.hidden = true;
        cropCancel.hidden = false;
        cropSave.hidden = false;
        cropHelp.textContent = 'Drag inside the rectangle to position it; drag a corner to resize. The black border shows the saved crop.';
        state.paint();
    });
    cropCancel.addEventListener('click', () => {
        state.cropMode = false;
        state.crop = null;
        cropStart.hidden = false;
        cropCancel.hidden = true;
        cropSave.hidden = true;
        cropHelp.textContent = 'Draw and position a crop rectangle, then use the camera button to add it as a source option.';
        state.paint();
    });
    cropSave.addEventListener('htmx:configRequest', event => {
        Object.assign(event.detail.parameters, {
            image_source: state.selected.value,
            source_motion: state.field.value,
            source_crop: JSON.stringify(state.crop),
        });
    });
    cropSave.addEventListener('click', event => {
        if (!state.cropMode || !state.crop) {
            event.preventDefault();
            return;
        }
        cropField.value = JSON.stringify(state.crop);
    });
    picker.querySelectorAll('[data-source-zoom]').forEach(input => input.addEventListener('input', () => {
        state.active = input.dataset.sourceZoom;
        state.motion[state.active].zoom = Number(input.value);
        state.stop();
        state.paint();
    }));
    picker.querySelectorAll('[data-source-zoom-percent]').forEach(input => {
        function updateZoom(commit) {
            const percent = Number(input.value);
            if (!commit && (!input.value || !Number.isFinite(percent) || percent < 100 || percent > 800)) return;
            const value = Number.isFinite(percent) && input.value
                ? Math.max(100, Math.min(800, Math.round(percent)))
                : Math.round(state.motion[input.dataset.sourceZoomPercent].zoom * 100);
            if (commit) input.value = value;
            state.active = input.dataset.sourceZoomPercent;
            state.motion[state.active].zoom = value / 100;
            state.stop();
            state.paint();
        }
        input.addEventListener('input', () => updateZoom(false));
        input.addEventListener('change', () => updateZoom(true));
    });
    picker.querySelector('[data-source-scrub]').addEventListener('input', event => {
        state.stop();
        state.progress = Number(event.target.value) / 1000;
        state.paint();
    });
    picker.querySelector('[data-source-play]').addEventListener('click', () => {
        if (state.playing) { state.stop(); return; }
        state.playing = true;
        const start = performance.now();
        const duration = Math.max(.25, Number(picker.dataset.motionDuration) || 4) * 1000;
        picker.querySelector('[data-source-play]').textContent = 'Stop preview';
        function tick(now) {
            if (!picker.isConnected) { state.stop(); return; }
            state.progress = Math.min(1, (now - start) / duration);
            state.paint();
            if (state.progress < 1) state.request = requestAnimationFrame(tick);
            else state.stop();
        }
        state.request = requestAnimationFrame(tick);
    });
    image.addEventListener('load', state.paint);
    image.addEventListener('error', () => {
        picker.querySelector('[data-source-caption]').textContent = 'Unable to load this illustration. Choose another thumbnail.';
    });
    state.paint();
}

function initializeSourcePickers(root) {
    if (root.matches?.('.source-image-picker')) initializeSourcePicker(root);
    root.querySelectorAll('.source-image-picker').forEach(initializeSourcePicker);
}

document.addEventListener('click', event => {
    const button = event.target.closest('button[data-source-image]');
    if (!button) return;
    const picker = button.closest('.source-image-picker');
    if (!picker) return;
    initializeSourcePicker(picker);
    const state = sourceImagePickers.get(picker);
    state.stop();
    picker.querySelectorAll('button[data-source-image]').forEach(candidate => {
        const selected = candidate === button;
        candidate.setAttribute('aria-pressed', String(selected));
        candidate.style.borderColor = selected ? 'var(--wa-color-brand-fill-loud, #2563eb)' : 'var(--wa-color-surface-border, #aaa)';
    });
    const filename = button.dataset.sourceImage;
    if (!state.regions.has(filename)) {
        state.regions.set(filename, { first: { x: .5, y: .5, zoom: 1 }, last: { x: .5, y: .5, zoom: 1 } });
    }
    state.motion = state.regions.get(filename);
    state.progress = 0;
    if (state.cropMode) {
        state.cropMode = false;
        state.crop = null;
        picker.querySelector('[data-source-crop-start]').hidden = false;
        picker.querySelector('[data-source-crop-cancel]').hidden = true;
        picker.querySelector('[data-source-crop-save]').hidden = true;
        picker.querySelector('[data-source-crop-help]').textContent = 'Draw and position a crop rectangle, then use the camera button to add it as a source option.';
    }
    picker.querySelector('input[name="source_crop"]').value = '';
    state.selected.value = filename;
    state.field.value = JSON.stringify(state.motion);
    state.image.src = button.dataset.sourceUrl;
    state.image.alt = filename;
    picker.querySelector('[data-source-caption]').textContent = filename;
});
document.addEventListener('htmx:load', event => initializeSourcePickers(event.detail.elt));
initializeSourcePickers(document);
