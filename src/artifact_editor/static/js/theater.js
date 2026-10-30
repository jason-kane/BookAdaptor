(() => {
  const root = document.getElementById('theater-project');
  if (!root) return;
  const status = root.querySelector('.theater-status');
  const readAlong = root.querySelector('.read-along-toggle');
  const conversation = root.querySelector('.director-conversation');
  const scriptEditor = root.querySelector('.script-editor');
  const createScriptButton = root.querySelector('.create-script-button');
  const scriptTaskStatus = root.querySelector('.script-task-status');
  const form = root.querySelector('.director-chat-form');
  const scriptPreview = root.querySelector('.script-preview');
  const filmTitle = root.querySelector('[data-film-title]');
  const production = root.querySelector('[data-production]');
  const productionStatus = production.querySelector('[data-production-status]');
  const roughButton = production.querySelector('.rough-draft-button');
  const roughFilmButtons = [...production.querySelectorAll('.rough-film-button')];
  const roughFilmButton = roughFilmButtons.shift();
  // Keep a single control for this action if stale/duplicated markup is present.
  for (const duplicate of roughFilmButtons) duplicate.remove();
  const finalButton = production.querySelector('.final-render-button');
  const resetImagesButton = production.querySelector('.reset-images-button');
  const videoPreviews = production.querySelector('[data-video-previews]');
  const resetButton = root.querySelector('.reset-project-button');
  const imageRevealStage = root.querySelector('[data-image-reveal-stage]');
  const imageLeaves = window.ImageLeaves.create(imageRevealStage);
  const seenGeneratedImages = new Set();
  let imageRevealInitialized = false;
  let imageRevealEnabled = false;
  let settingsSaveTimer;
  let renderSocket;
  let renderReconnectTimer;
  let pageIsClosing = false;
  let watchedRenderJobId = root.dataset.renderJobId || '';

  function settingsForm() {
    const data = new FormData();
    if (readAlong.checked) data.set('read_along', 'on');
    return data;
  }

  async function saveSettings(quiet = false) {
    const response = await fetch(root.dataset.configureUrl, {method: 'POST', body: settingsForm()});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save direction.');
    if (!quiet) status.textContent = 'Direction saved.';
  }

  function scheduleSettingsSave() {
    clearTimeout(settingsSaveTimer);
    settingsSaveTimer = setTimeout(async () => {
      try { await saveSettings(); } catch (error) { status.textContent = error.message; }
    }, 250);
  }

  readAlong.addEventListener('change', scheduleSettingsSave);

  function isRendering(status) {
    return ['rough_queued', 'rough_rendering', 'rough_film_queued', 'rough_film_rendering', 'final_queued', 'final_rendering'].includes(status);
  }

  function updateVideoPreview(label, url, version) {
    if (!version) return;
    let preview = videoPreviews.querySelector(`[data-video-kind="${label.toLowerCase()}"]`);
    if (!preview) {
      preview = element('div', '', 'video-preview');
      preview.dataset.videoKind = label.toLowerCase();
      preview.append(element('h3', label));
      const video = document.createElement('video');
      video.controls = true;
      video.playsInline = true;
      video.preload = 'metadata';
      preview.append(video);
      videoPreviews.append(preview);
    }
    const video = preview.querySelector('video');
    const mediaVersion = String(version);
    if (video.dataset.mediaVersion === mediaVersion) return;
    const wasPlaying = !video.paused && !video.ended;
    const previousTime = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const nextUrl = new URL(url, window.location.href);
    nextUrl.searchParams.set('v', mediaVersion);
    video.dataset.mediaVersion = mediaVersion;
    video.src = nextUrl.href;
    if (previousTime > 0 || wasPlaying) {
      video.addEventListener('loadedmetadata', () => {
        if (Number.isFinite(video.duration)) video.currentTime = Math.min(previousTime, Math.max(0, video.duration - 0.05));
        if (wasPlaying) video.play().catch(() => {});
      }, {once: true});
    }
  }

  function ensureEditorLink() {
    let link = production.querySelector('[data-editor-link]');
    if (!link) {
      link = element('a', 'Open video edit room', 'editor-room-link');
      link.dataset.editorLink = '';
      link.href = root.dataset.editorUrl;
      production.querySelector('.script-heading').append(link);
    }
    root.dataset.timelineExists = 'true';
  }

  function reflectRenderState(result) {
    const state = result.status || 'planning';
    const failedStage = result.render_stage || root.dataset.renderStage;
    const canRough = state === 'script_ready' || (state === 'render_failed' && failedStage === 'rough' && !result.rough_exists);
    const canRoughFilm = ['rough_ready', 'rough_film_ready'].includes(state) || (state === 'render_failed' && failedStage === 'rough_film');
    const canFinal = result.rough_exists && ['rough_film_ready', 'final_ready'].includes(state) || (state === 'render_failed' && failedStage === 'final');
    if (roughButton) {
      roughButton.hidden = !canRough;
      roughButton.disabled = isRendering(state);
      roughButton.textContent = state === 'render_failed' && failedStage === 'rough' ? 'Retry rough draft' : 'Create rough draft';
    }
    if (roughFilmButton) {
      roughFilmButton.hidden = !canRoughFilm;
      roughFilmButton.disabled = isRendering(state);
      roughFilmButton.textContent = state === 'render_failed' && failedStage === 'rough_film' ? 'Retry rough film' : (state === 'rough_film_ready' ? 'Render rough film again' : 'Render rough film');
    }
    finalButton.hidden = !canFinal;
    finalButton.disabled = isRendering(state);
    if (resetButton) resetButton.disabled = isRendering(state);
    if (resetImagesButton) resetImagesButton.disabled = isRendering(state);
    finalButton.textContent = state === 'final_ready' ? 'Render final film again' : 'Render final film';
    if (result.error) productionStatus.textContent = `Render failed: ${result.error}`;
    else if (state === 'rough_ready') productionStatus.textContent = `Rough draft ready (${formatRuntime(result.runtime_seconds)}, ${result.aspect || 'format selected by runtime'}). Review it in the video edit room, then render the low-resolution rough film.`;
    else if (state === 'rough_film_ready') productionStatus.textContent = `Rough film ready (${formatRuntime(result.runtime_seconds)}, ${result.aspect || 'format selected by runtime'}). Review the animation, then render the final film at high resolution.`;
    else if (state === 'final_ready') productionStatus.textContent = `Final render ready (${formatRuntime(result.runtime_seconds)}, ${result.aspect || 'format selected by runtime'}).`;
    else productionStatus.textContent = state.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase()) + (isRendering(state) ? '…' : '.');
    if (result.rough_exists) ensureEditorLink();
    if (result.rough_version) root.dataset.roughVersion = result.rough_version;
    if (result.final_version) root.dataset.finalVersion = result.final_version;
    if (result.rough_exists) updateVideoPreview('Rough draft', root.dataset.roughUrl, root.dataset.roughVersion);
    const roughHeading = videoPreviews.querySelector('[data-video-kind="rough draft"] h3');
    if (roughHeading && ['rough_film_ready', 'final_ready'].includes(state)) roughHeading.textContent = 'Rough film';
    if (result.final_exists) updateVideoPreview('Final film', root.dataset.finalUrl, root.dataset.finalVersion);
  }

  function revealGeneratedImages(items, state) {
    const activeRough = ['rough_queued', 'rough_rendering'].includes(state) && root.dataset.renderStage === 'rough';
    if (!imageRevealInitialized) {
      imageRevealInitialized = true;
      imageRevealEnabled = activeRough;
      if (!activeRough) {
        for (const item of items || []) seenGeneratedImages.add(item.id);
        return;
      }
    }
    for (const item of items || []) {
      if (!item.id || seenGeneratedImages.has(item.id)) continue;
      seenGeneratedImages.add(item.id);
      if (imageRevealEnabled) imageLeaves.show(item);
    }
    if (!activeRough) imageRevealEnabled = false;
  }

  function formatRuntime(seconds) {
    if (!Number.isFinite(Number(seconds))) return 'runtime pending';
    const whole = Math.round(Number(seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  }

  function applyRenderSnapshot(result) {
    revealGeneratedImages(result.generated_images, result.status);
    reflectRenderState(result);
    const progress = result.progress || {};
    if (isRendering(result.status) && progress.message) {
      const percent = Number(progress.percent);
      productionStatus.textContent = Number.isFinite(percent)
        ? `${percent}% · ${progress.message}`
        : progress.message;
    }
  }

  function connectRenderSocket() {
    if (pageIsClosing) return;
    renderSocket = new WebSocket(root.dataset.renderWebsocket);
    renderSocket.addEventListener('open', () => {
      if (watchedRenderJobId) {
        renderSocket.send(JSON.stringify({action: 'watch', job_id: watchedRenderJobId}));
      }
    });
    renderSocket.addEventListener('message', ({data}) => {
      try {
        const message = JSON.parse(data);
        if (message.type === 'snapshot' || message.type === 'update') applyRenderSnapshot(message);
      } catch (_) {
        // Ignore non-JSON frames; this socket carries Theater render state only.
      }
    });
    renderSocket.addEventListener('close', () => {
      if (!pageIsClosing) renderReconnectTimer = setTimeout(connectRenderSocket, 1500);
    });
    renderSocket.addEventListener('error', () => renderSocket.close());
  }

  async function startRender(button, url, stage) {
    if (button.disabled) return;
    if (stage === 'rough draft') {
      imageRevealEnabled = true;
      imageRevealInitialized = true;
      seenGeneratedImages.clear();
      imageLeaves.reset();
    }
    button.disabled = true;
    productionStatus.textContent = `Queueing ${stage}…`;
    try {
      const response = await fetch(url, {method: 'POST'});
      const result = await readJsonResponse(response, `Could not queue the ${stage}.`);
      if (!response.ok) throw new Error(result.error || `Could not queue the ${stage}.`);
      if (result.job_id) {
        watchedRenderJobId = result.job_id;
        if (renderSocket?.readyState === WebSocket.OPEN) {
          renderSocket.send(JSON.stringify({action: 'watch', job_id: watchedRenderJobId}));
        }
      }
      root.dataset.renderStage = stage === 'rough draft' ? 'rough' : stage === 'rough film' ? 'rough_film' : 'final';
      if (stage === 'rough draft') root.dataset.timelineExists = 'true';
      reflectRenderState({status: result.status, timeline_exists: root.dataset.timelineExists === 'true'});
    } catch (error) {
      productionStatus.textContent = error.message;
      button.disabled = false;
    }
  }

  async function readJsonResponse(response, fallback) {
    const body = await response.text();
    try {
      return JSON.parse(body);
    } catch (_) {
      const detail = body.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180);
      throw new Error(`${fallback} (HTTP ${response.status})${detail ? `: ${detail}` : ''}`);
    }
  }

  if (roughButton) roughButton.addEventListener('click', () => startRender(roughButton, root.dataset.roughDraftUrl, 'rough draft'));
  if (roughFilmButton) roughFilmButton.addEventListener('click', () => startRender(roughFilmButton, root.dataset.roughFilmUrl, 'rough film'));
  finalButton.addEventListener('click', () => startRender(finalButton, root.dataset.finalRenderUrl, 'final render'));
  if (resetImagesButton) resetImagesButton.addEventListener('click', async () => {
    if (resetImagesButton.disabled) return;
    const confirmed = window.confirm(
      'Reset generated images? This clears the stills, derived animation and current video cuts so they can be generated again. Your story, director conversation, script, timeline and narration stay in place.'
    );
    if (!confirmed) return;
    resetImagesButton.disabled = true;
    productionStatus.textContent = 'Resetting generated images…';
    try {
      const response = await fetch(root.dataset.resetImagesUrl, {method: 'POST'});
      const result = await readJsonResponse(response, 'Could not reset generated images.');
      if (!response.ok) throw new Error(result.error || 'Could not reset generated images.');
      for (const video of videoPreviews.querySelectorAll('video')) {
        video.pause();
        video.removeAttribute('src');
        video.load();
      }
      videoPreviews.replaceChildren();
      seenGeneratedImages.clear();
      imageRevealInitialized = false;
      imageRevealEnabled = false;
      if (roughButton) {
        roughButton.hidden = false;
        roughButton.disabled = false;
        roughButton.textContent = 'Create rough draft';
      }
      if (roughFilmButton) roughFilmButton.hidden = true;
      finalButton.hidden = true;
      productionStatus.textContent = result.message;
      resetImagesButton.disabled = false;
    } catch (error) {
      productionStatus.textContent = error.message;
      resetImagesButton.disabled = false;
    }
  });
  if (resetButton) resetButton.addEventListener('click', async () => {
    if (resetButton.disabled) return;
    const confirmed = window.confirm(
      'Start over? This clears the film title, director reading and conversation, script, timeline, and all generated media. Your base story and selected director will be kept.'
    );
    if (!confirmed) return;
    resetButton.disabled = true;
    status.textContent = 'Resetting this film…';
    try {
      const response = await fetch(root.dataset.resetUrl, {method: 'POST'});
      const result = await readJsonResponse(response, 'Could not reset this film.');
      if (!response.ok) throw new Error(result.error || 'Could not reset this film.');
      window.location.assign(result.redirect_url);
    } catch (error) {
      status.textContent = error.message;
      resetButton.disabled = false;
    }
  });
  connectRenderSocket();
  window.addEventListener('pagehide', () => {
    pageIsClosing = true;
    clearTimeout(renderReconnectTimer);
    if (renderSocket && renderSocket.readyState < WebSocket.CLOSING) renderSocket.close();
  });

  const savedScript = JSON.parse(root.dataset.script || '{}');
  scriptEditor.value = JSON.stringify(savedScript || {}, null, 2);

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function labeledNotes(parent, label, value) {
    if (!value) return;
    const row = element('p', '', 'script-note');
    const heading = element('strong', `${label}: `);
    row.append(heading, document.createTextNode(Array.isArray(value) ? value.join(', ') : String(value)));
    parent.append(row);
  }

  function renderScript(script = {}) {
    scriptPreview.replaceChildren();
    const scenes = Array.isArray(script.scenes) ? script.scenes : [];
    if (!script.story_rewrite && !scenes.length) {
      scriptPreview.append(element('p', 'The director’s revised script will appear here when the story plan is ready.', 'script-empty'));
      return;
    }
    if (script.story_rewrite) {
      const rewrite = element('section', '', 'script-rewrite');
      rewrite.append(element('h3', 'Rewritten story'), element('p', script.story_rewrite));
      scriptPreview.append(rewrite);
    }

    const alignment = script.source_alignment;
    if (alignment && typeof alignment === 'object') {
      const review = element('section', '', 'script-source-alignment');
      review.append(element('h3', 'Director’s source check'));
      for (const [label, field] of [
        ['Source beats preserved', 'source_beats_preserved'],
        ['Unsupported material removed or corrected', 'unsupported_material_removed'],
        ['Intentional visual interpretation', 'intentional_stylization'],
        ['Ambiguities', 'ambiguities'],
      ]) labeledNotes(review, label, alignment[field]);
      scriptPreview.append(review);
    }

    const requirements = Array.isArray(script.story_requirements) ? script.story_requirements : [];
    if (requirements.length) {
      const section = element('section', '', 'script-requirements');
      section.append(element('h3', 'What must happen for the story to make sense'));
      const list = element('ol');
      requirements.forEach(item => {
        const row = element('li');
        row.append(element('strong', item.must_happen || 'Story-critical beat'));
        if (item.why_it_matters) row.append(element('p', item.why_it_matters));
        if (item.scene_id) row.append(element('small', `Scene: ${item.scene_id}`));
        list.append(row);
      });
      section.append(list);
      scriptPreview.append(section);
    }

    const characters = Array.isArray(script.characters) ? script.characters : [];
    const props = Array.isArray(script.props) ? script.props : [];
    if (characters.length || props.length) {
      const continuity = element('details', '', 'script-continuity');
      continuity.append(element('summary', 'Character and prop continuity'));
      for (const [label, entries, fields] of [
        ['Characters', characters, ['story_role', 'appearance', 'performance_notes', 'continuity_notes']],
        ['Props', props, ['appearance', 'story_function', 'continuity_notes']],
      ]) {
        if (!entries.length) continue;
        continuity.append(element('h4', label));
        for (const item of entries) {
          const card = element('article', '', 'continuity-card');
          card.append(element('strong', item.name || item.id || label.slice(0, -1)));
          for (const field of fields) labeledNotes(card, field.replaceAll('_', ' '), item[field]);
          continuity.append(card);
        }
      }
      scriptPreview.append(continuity);
    }

    const requirementByScene = new Map();
    for (const item of requirements) {
      if (!item.scene_id) continue;
      if (!requirementByScene.has(item.scene_id)) requirementByScene.set(item.scene_id, []);
      requirementByScene.get(item.scene_id).push(item);
    }
    const sceneList = element('div', '', 'director-scenes');
    scenes.forEach((scene, index) => {
      const sceneCard = element('article', '', 'director-scene');
      const sceneId = scene.id || `Scene ${index + 1}`;
      sceneCard.append(element('h3', scene.title || sceneId));
      labeledNotes(sceneCard, 'Dramatic purpose', scene.dramatic_purpose);
      labeledNotes(sceneCard, 'Visual note', scene.visual_note);
      for (const item of requirementByScene.get(scene.id) || []) labeledNotes(sceneCard, 'Must happen', item.must_happen);
      (Array.isArray(scene.shots) ? scene.shots : []).forEach((shot, shotIndex) => {
        const shotCard = element('section', '', 'director-shot');
        shotCard.append(element('h4', shot.title || `Shot ${shotIndex + 1}`));
        for (const [label, field] of [
          ['Cinematography', 'cinematic_description'], ['Framing', 'framing'], ['Camera', 'camera_position'],
          ['Movement', 'camera_movement'], ['Composition', 'composition'], ['Lens and focus', 'lens_focus'],
          ['Light and color', 'lighting'], ['Visual note', 'visual_note'], ['Action', 'action'],
          ['Entrances and exits', 'entrances_exits'], ['Characters and props', 'characters_props'],
          ['Dialogue or silence', 'dialogue'], ['Speaker', 'speaker'], ['Delivery', 'delivery'],
        ]) labeledNotes(shotCard, label, shot[field]);
        const prompts = element('details', '', 'shot-prompts');
        prompts.append(element('summary', 'Image and animation prompts'));
        labeledNotes(prompts, 'Image prompt', shot.image_prompt);
        labeledNotes(prompts, 'Animation prompt', shot.animation_prompt);
        if (shot.image_prompt || shot.animation_prompt) shotCard.append(prompts);
        sceneCard.append(shotCard);
      });
      sceneList.append(sceneCard);
    });
    scriptPreview.append(sceneList);
  }
  renderScript(savedScript);

  function appendLine(role, text) {
    const p = document.createElement('p');
    p.className = `dialogue-line ${role}`;
    const label = document.createElement('b');
    label.textContent = role === 'user' ? 'You: ' : 'Director: ';
    p.append(label, document.createTextNode(text));
    conversation.append(p);
    conversation.scrollTop = conversation.scrollHeight;
  }

  const messageField = form.querySelector('[name="message"]');
  const discussButton = form.querySelector('wa-button');
  function setChatEnabled(enabled) {
    messageField.disabled = !enabled;
    discussButton.disabled = !enabled;
    createScriptButton.disabled = !enabled;
  }

  async function readStory() {
    if (root.dataset.storyRead === 'true') {
      setChatEnabled(true);
      return;
    }
    setChatEnabled(false);
    status.textContent = 'The director is reading the base story…';
    try {
      const response = await fetch(root.dataset.readUrl, {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The director could not read the story.');
      if (!result.cached) appendLine('assistant', result.reply);
      if (result.film_title) {
        filmTitle.textContent = result.film_title;
        document.title = `${result.film_title} · Theater`;
      }
      root.dataset.storyRead = 'true';
      status.textContent = result.film_title
        ? `The director named the film “${result.film_title}” and has read the base story.`
        : 'The director has read the base story.';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      setChatEnabled(root.dataset.storyRead === 'true');
    }
  }
  readStory();

  createScriptButton.addEventListener('click', async () => {
    if (createScriptButton.disabled) return;
    setChatEnabled(false);
    scriptTaskStatus.textContent = 'The director is creating the revised script…';
    try {
      const response = await fetch(root.dataset.createScriptUrl, {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The director could not create the script.');
      scriptEditor.value = JSON.stringify(result.script, null, 2);
      renderScript(result.script);
      if (result.reply) appendLine('assistant', result.reply);
      scriptTaskStatus.textContent = 'The script is ready, shaped by the conversation above.';
      status.textContent = 'Director script refined.';
      if (result.previous_cut_archived) {
        videoPreviews.replaceChildren();
        production.querySelector('[data-editor-link]')?.remove();
        root.dataset.timelineExists = 'false';
        reflectRenderState({status: 'script_ready', rough_exists: false, final_exists: false});
        productionStatus.textContent = 'Previous cut archived. Create a new rough draft to review this script.';
      }
    } catch (error) {
      scriptTaskStatus.textContent = error.message;
    } finally {
      setChatEnabled(root.dataset.storyRead === 'true');
    }
  });

  function showOptions(options = []) {
    const target = root.querySelector('.director-options');
    target.replaceChildren();
    options.forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'director-option';
      const title = document.createElement('strong'); title.textContent = item.label || 'Option';
      const description = document.createElement('p'); description.textContent = item.description || '';
      button.append(title, description);
      button.addEventListener('click', () => {
        form.querySelector('[name="message"]').value = `Develop this option: ${item.label}. ${item.description || ''}`.trim();
        form.requestSubmit();
      });
      target.append(button);
    });
  }

  function showStillConcepts(concepts = []) {
    const target = root.querySelector('.sample-stills');
    target.replaceChildren();
    concepts.forEach(item => {
      const card = document.createElement('article'); card.className = 'sample-still';
      const title = document.createElement('strong'); title.textContent = item.label || 'Still concept';
      const prompt = document.createElement('p'); prompt.textContent = item.prompt || '';
      card.append(title, prompt);
      target.append(card);
    });
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const field = form.querySelector('[name="message"]');
    const message = field.value.trim();
    if (!message) return;
    setChatEnabled(false);
    status.textContent = 'The director is thinking…';
    try {
      await saveSettings(true);
      const response = await fetch(root.dataset.chatUrl, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Director conversation failed.');
      appendLine('user', message);
      appendLine('assistant', [result.reply, result.follow_up_question].filter(Boolean).join('\n'));
      showOptions(result.options);
      showStillConcepts(result.sample_still_prompts);
      status.textContent = result.follow_up_question ? 'The director has a question.' : 'Conversation saved. Refine the script when you’re ready.';
      field.value = '';
    } catch (error) { status.textContent = error.message; }
    finally { setChatEnabled(root.dataset.storyRead === 'true'); }
  });

  async function saveScriptOnChange() {
    try {
      const script = JSON.parse(scriptEditor.value);
      const response = await fetch(root.dataset.scriptUrl, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(script),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save script.');
      renderScript(script);
      status.textContent = 'Script changes saved.';
    } catch (error) { status.textContent = error instanceof SyntaxError ? 'Script must be valid JSON.' : error.message; }
  }
  scriptEditor.addEventListener('change', saveScriptOnChange);
})();
