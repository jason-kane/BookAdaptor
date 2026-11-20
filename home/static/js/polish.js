function renderModelScores(scores) {
  const body = document.querySelector('[data-model-scores]');
  if (!body) return;
  const rows = scores.map(score => {
    const row = document.createElement('tr');
    [score.model_label, score.rated_count, score.points].forEach(value => {
      const cell = document.createElement('td');
      cell.textContent = String(value);
      row.append(cell);
    });
    return row;
  });
  body.replaceChildren(...rows);
}

const goalsToggle = document.querySelector('[data-goals-toggle]');
const goalsPanel = document.querySelector('#polish-goals');
goalsToggle?.addEventListener('click', () => {
  const expanded = goalsToggle.getAttribute('aria-expanded') !== 'true';
  goalsToggle.setAttribute('aria-expanded', String(expanded));
  goalsToggle.textContent = expanded ? 'Hide Goals' : 'Show Goals';
  goalsPanel.hidden = !expanded;
  if (expanded && document.querySelector('[data-model-refresh]')) {
    loadGoalModels(!goalModelsLoaded);
    pollGoalJobs();
  }
});

function renderGoals(goals) {
  const list = document.querySelector('[data-goals-list]');
  if (!list || !goals) return;
  list.replaceChildren(...goals.map(goal => {
    const item = document.createElement('li');
    item.dataset.goalId = goal.id;
    const title = document.createElement('h3');
    title.textContent = `${goal.id} · ${goal.title}`;
    item.append(title);
    if (goal.evidence) {
      const evidence = document.createElement('p');
      evidence.textContent = goal.evidence;
      item.append(evidence);
    }
    addGoalActions(item, goal.id);
    return item;
  }));
  document.querySelector('[data-goal-count]').textContent = `(${goals.length})`;
}

let goalModels = [];
let goalModelsLoaded = false;
let modelPollTimer;
let jobPollTimer;
let modelPollAttempts = 0;
const goalSelections = new Map();
const goalJobs = new Map();
const goalRequestKeys = new Map();
const rubModel = document.querySelector('[data-rub-model]');
const rubButton = document.querySelector('[data-rub-start]');
const sixButton = document.querySelector('[data-six-start]');
let rubSelection = '';
let rubJob;
let rubRequestKey;
let sixPack;
let sixRequestKey;

function setModelMessage(message) {
  document.querySelectorAll('[data-model-message]').forEach(item => { item.textContent = message; });
}

function updateRubControl() {
  if (!rubModel || !rubButton || !sixButton) return;
  const rubActive = rubJob && ['queued', 'running'].includes(rubJob.status);
  const sixActive = sixPack && sixPack.status === 'running';
  const active = rubActive || sixActive;
  const options = [new Option('Choose a model', '')];
  goalModels.forEach(model => options.push(new Option(model.label, model.id)));
  rubModel.replaceChildren(...options);
  const activeModel = rubActive ? rubJob.model_id : sixActive ? sixPack.jobs[0]?.model_id : rubSelection;
  rubModel.value = activeModel || '';
  rubModel.disabled = !!active || !goalModels.length;
  rubButton.disabled = !!active || !rubModel.value;
  sixButton.disabled = !!active || !rubModel.value;
  rubButton.loading = !!rubActive;
  sixButton.loading = !!sixActive;
  rubButton.setAttribute('aria-busy', String(!!rubActive));
  sixButton.setAttribute('aria-busy', String(!!sixActive));
  if (rubJob) {
    const status = document.querySelector('[data-rub-status]');
    status.textContent = `${rubJob.goal_id} · ${rubJob.goal_title} (score ${rubJob.selection.score}) · ${rubJob.model}: ${rubJob.message}`;
    status.dataset.jobState = rubJob.status;
    if (rubJob.status === 'completed' && rubJob.entry_id) {
      const link = document.createElement('a');
      link.href = `?new_entry=${rubJob.entry_id}#entry-${rubJob.entry_id}`;
      link.textContent = ' Review new entry';
      status.append(link);
    }
  }
  renderSixPackStatus();
}

function renderSixPackStatus() {
  const status = document.querySelector('[data-six-status]');
  if (!status || !sixPack) return;
  const heading = document.createElement('p');
  heading.textContent = `Six pack: ${sixPack.completed} of ${sixPack.requested} entries generated (${sixPack.status}).`;
  status.replaceChildren(heading);
  const list = document.createElement('ul');
  sixPack.jobs.forEach(job => {
    const item = document.createElement('li');
    item.textContent = `${job.goal_id} · ${job.goal_title}: ${job.message}`;
    if (job.status === 'completed' && job.entry_id) {
      const link = document.createElement('a');
      link.href = `?new_entry=${job.entry_id}#entry-${job.entry_id}`;
      link.textContent = ' Review entry';
      item.append(link);
    }
    list.append(item);
  });
  status.append(list);
}

async function goalRequest(url, body) {
  const response = await fetch(url, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || 'Unable to contact the Polish worker.');
    error.status = response.status;
    throw error;
  }
  return data;
}

function updateGoalControl(item) {
  const goalId = item.dataset.goalId;
  const select = item.querySelector('[data-goal-model]');
  const button = item.querySelector('[data-goal-seek]');
  const job = goalJobs.get(goalId);
  const active = job && ['queued', 'running'].includes(job.status);
  const selected = active ? job.model_id : goalSelections.get(goalId);
  const options = [new Option('Choose a model', '')];
  goalModels.forEach(model => options.push(new Option(model.label, model.id)));
  select.replaceChildren(...options);
  select.value = selected || '';
  select.disabled = !!active || !goalModels.length;
  button.disabled = !!active || !select.value;
  button.textContent = active ? 'Seeking…' : 'Seek improvements';
  const status = item.querySelector('[data-goal-job-status]');
  status.replaceChildren();
  if (job) {
    status.textContent = `${job.model}: ${job.message}`;
    if (job.status === 'completed' && job.entry_id) {
      const link = document.createElement('a');
      link.href = `?new_entry=${job.entry_id}#entry-${job.entry_id}`;
      link.textContent = ' Review new entry';
      status.append(link);
    }
    status.dataset.jobState = job.status;
  }
}

function addGoalActions(item, goalId) {
  // Test/preview lists without provider controls stay read-only.
  if (!document.querySelector('[data-model-refresh]')) return;
  const controls = document.createElement('div');
  controls.className = 'goal-actions';
  const label = document.createElement('label');
  label.textContent = 'Model ';
  const select = document.createElement('select');
  select.dataset.goalModel = goalId;
  select.setAttribute('aria-label', `Model for ${goalId}`);
  label.append(select);
  const button = document.createElement('wa-button');
  button.type = 'button';
  button.setAttribute('size', 's');
  button.setAttribute('appearance', 'outlined');
  button.dataset.goalSeek = goalId;
  controls.append(label, button);
  const status = document.createElement('p');
  status.dataset.goalJobStatus = goalId;
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  item.append(controls, status);
  updateGoalControl(item);
}

function updateAllGoalControls() {
  document.querySelectorAll('[data-goal-id]').forEach(item => {
    if (item.querySelector('[data-goal-model]')) updateGoalControl(item);
  });
  updateRubControl();
}

async function loadGoalModels(refresh = false) {
  clearTimeout(modelPollTimer);
  const message = document.querySelector('[data-model-message]');
  if (!message) return;
  try {
    if (refresh) {
      modelPollAttempts = 0;
      setModelMessage('Discovering available models…');
      await goalRequest('/polish/models/refresh', {});
    }
    const data = await goalRequest('/polish/models');
    if (!data.updated_at && !data.refreshing && !refresh && !(data.models || []).length) {
      await loadGoalModels(true);
      return;
    }
    goalModels = data.models || [];
    goalModelsLoaded = true;
    updateAllGoalControls();
    setModelMessage(`${goalModels.length} models available. ${(data.errors || []).join(' ')}`);
    if (data.refreshing && modelPollAttempts++ < 30) {
      setModelMessage('Discovering available models…');
      modelPollTimer = setTimeout(() => loadGoalModels(), 1000);
    } else if (data.refreshing) {
      setModelMessage('Discovery is taking longer than expected. Check Polish worker status or refresh models.');
    }
  } catch (error) {
    setModelMessage(error.message);
  }
}

async function pollGoalJobs() {
  clearTimeout(jobPollTimer);
  try {
    const data = await goalRequest('/polish/jobs');
    (data.jobs || []).forEach(job => {
      goalJobs.set(job.goal_id, job);
      if (!goalSelections.has(job.goal_id)) goalSelections.set(job.goal_id, job.model_id);
    });
    if (data.automatic_job && (!rubJob || data.automatic_job.created_at >= rubJob.created_at)) {
      rubJob = data.automatic_job;
      if (!rubSelection) rubSelection = rubJob.model_id;
    }
    if (data.six_pack) sixPack = data.six_pack;
    updateAllGoalControls();
    if ([...goalJobs.values(), rubJob].some(job => job && ['queued', 'running'].includes(job.status))
        || (sixPack && sixPack.status === 'running')) {
      jobPollTimer = setTimeout(pollGoalJobs, 2000);
    }
  } catch (error) {
    const message = document.querySelector('[data-model-message]');
    if (message) setModelMessage(`Unable to refresh job progress: ${error.message}`);
    // Saved jobs keep running; another refresh can recover their status.
  }
}

rubModel?.addEventListener('change', () => {
  rubSelection = rubModel.value;
  updateRubControl();
});
sixButton?.addEventListener('click', async () => {
  if (sixButton.disabled) return;
  sixButton.disabled = true;
  rubButton.disabled = true;
  rubModel.disabled = true;
  const status = document.querySelector('[data-six-status]');
  status.textContent = 'Selecting six distinct available goals…';
  try {
    if (!sixRequestKey) sixRequestKey = crypto.randomUUID();
    const data = await goalRequest('/polish/six-pack', {model: rubModel.value, request_key: sixRequestKey});
    sixRequestKey = undefined;
    sixPack = data.six_pack;
    updateRubControl();
    pollGoalJobs();
  } catch (error) {
    if (error.status) sixRequestKey = undefined;
    status.textContent = error.message;
    rubModel.disabled = false;
    sixButton.disabled = !rubModel.value;
    rubButton.disabled = !rubModel.value;
  }
});
rubButton?.addEventListener('click', async () => {
  if (rubButton.disabled) return;
  rubButton.disabled = true;
  sixButton.disabled = true;
  rubModel.disabled = true;
  const status = document.querySelector('[data-rub-status]');
  status.textContent = 'Choosing a lowest-scoring goal…';
  try {
    if (!rubRequestKey) rubRequestKey = crypto.randomUUID();
    const data = await goalRequest('/polish/rub-one-out', {model: rubModel.value, request_key: rubRequestKey});
    rubRequestKey = undefined;
    rubJob = data.job;
    goalJobs.set(rubJob.goal_id, rubJob);
    updateAllGoalControls();
    pollGoalJobs();
  } catch (error) {
    if (error.status) rubRequestKey = undefined;
    status.textContent = error.message;
    rubModel.disabled = false;
    rubButton.disabled = !rubModel.value;
    sixButton.disabled = !rubModel.value;
  }
});
if (rubModel) {
  loadGoalModels();
  pollGoalJobs();
}

document.querySelector('[data-model-refresh]')?.addEventListener('click', () => loadGoalModels(true));
document.querySelectorAll('[data-goal-id]').forEach(item => addGoalActions(item, item.dataset.goalId));
document.querySelector('[data-goals-list]')?.addEventListener('change', event => {
  const select = event.target.closest('[data-goal-model]');
  if (!select) return;
  goalSelections.set(select.dataset.goalModel, select.value);
  const item = select.closest('[data-goal-id]');
  item.querySelector('[data-goal-seek]').disabled = !select.value;
});
document.querySelector('[data-goals-list]')?.addEventListener('click', async event => {
  const button = event.target.closest('[data-goal-seek]');
  if (!button || button.disabled) return;
  const item = button.closest('[data-goal-id]');
  const goalId = item.dataset.goalId;
  const select = item.querySelector('[data-goal-model]');
  const status = item.querySelector('[data-goal-job-status]');
  button.disabled = true;
  select.disabled = true;
  status.textContent = 'Submitting search…';
  try {
    if (!goalRequestKeys.has(goalId)) goalRequestKeys.set(goalId, crypto.randomUUID());
    const data = await goalRequest(`/polish/goals/${encodeURIComponent(goalId)}/improvements`, {
      model: select.value, request_key: goalRequestKeys.get(goalId)
    });
    goalRequestKeys.delete(goalId);
    goalJobs.set(goalId, data.job);
    updateGoalControl(item);
    pollGoalJobs();
  } catch (error) {
    if (error.status) goalRequestKeys.delete(goalId);
    status.textContent = error.message;
    button.disabled = false;
    select.disabled = false;
  }
});

function renderAttention(entry, result) {
  const list = document.querySelector('[data-warning-list]');
  if (!list) return;
  const existing = [...list.children].find(item => item.dataset.warningEntry === entry.dataset.entryId);
  existing?.remove();
  if (['conflict', 'blocked'].includes(result.application_status)) {
    const warning = document.createElement('p');
    warning.dataset.warningEntry = entry.dataset.entryId;
    const link = document.createElement('a');
    link.href = `?view=graded#entry-${entry.dataset.entryId}`;
    link.textContent = entry.querySelector('h2')?.textContent || 'Graded change';
    warning.append(link, ` · ${result.application_message}`);
    if (result.correction_entry_id) {
      const correction = document.createElement('a');
      correction.href = `?new_entry=${result.correction_entry_id}#entry-${result.correction_entry_id}`;
      correction.textContent = ' Review second attempt';
      warning.append(correction);
    }
    list.append(warning);
  }
  list.closest('.application-warnings').hidden = list.children.length === 0;
}

function dismissGradedEntry(entry) {
  const list = entry.closest('.review-list');
  if (!list || list.dataset.reviewView === 'graded') return;
  entry.dataset.dismissing = 'true';
  const finish = () => {
    if (entry.contains(document.activeElement)) {
      const adjacent = entry.nextElementSibling?.querySelector('[data-reaction]') ||
        entry.previousElementSibling?.querySelector('[data-reaction]') || goalsToggle;
      adjacent?.focus();
    }
    entry.remove();
    const remaining = list.querySelectorAll('[data-entry-id]').length;
    const count = document.querySelector('[data-entry-count]');
    if (count) count.textContent = `${remaining} ${list.dataset.reviewView === 'preview' ? 'preview entries' : 'entries to review'}`;
    const empty = list.querySelector('[data-review-empty]');
    if (empty) empty.hidden = remaining !== 0;
  };
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) {
    finish();
  } else if (entry.animate) {
    entry.animate([
      {height: `${entry.getBoundingClientRect().height}px`, opacity: 1,
        marginBottom: getComputedStyle(entry).marginBottom},
      {height: '0px', opacity: 0, marginBottom: '0px', borderWidth: '0px'}
    ], {duration: 420, easing: 'ease', fill: 'forwards'}).finished.then(finish, finish);
  } else {
    entry.classList.add('grading-out');
    setTimeout(finish, 420);
  }
}

document.querySelectorAll('[data-entry-id]').forEach(entry => {
  const buttons = [...entry.querySelectorAll('[data-reaction]')];
  const status = entry.querySelector('.reaction-status');
  const application = entry.querySelector('[data-application-status]');
  let saving = false;
  buttons.forEach(button => button.addEventListener('click', async () => {
    if (saving) return;
    saving = true;
    buttons.forEach(item => { item.disabled = true; });
    status.textContent = 'Saving…';
    try {
      let reaction = button.dataset.reaction;
      let result;
      if (entry.dataset.preview !== 'true') {
        const response = await fetch(entry.dataset.reactionUrl, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({reaction})
        });
        if (!response.ok) throw new Error('Unable to save. Please try again.');
        result = await response.json();
        reaction = result.reaction;
      }
      buttons.forEach(item => {
        const selected = item.dataset.reaction === reaction;
        item.setAttribute('aria-pressed', String(selected));
        item.setAttribute('appearance', selected ? 'accent' : 'outlined');
      });
      status.textContent = `${entry.dataset.preview === 'true' ? 'Preview' : 'Saved'}: ${reaction}`;
      if (result) {
        application.dataset.applicationStatus = result.application_status;
        application.textContent = result.application_message;
        renderModelScores(result.scores);
        renderGoals(result.goals);
        renderAttention(entry, result);
      }
      const feedback = document.querySelector('[data-review-feedback]');
      if (feedback) {
        feedback.hidden = false;
        feedback.dataset.attention = String(['conflict', 'blocked'].includes(result?.application_status));
        feedback.textContent = `${result ? 'Saved' : 'Preview'}: ${reaction}. ${result?.application_message || 'Temporary preview rating.'}`;
        if (result && ['conflict', 'blocked'].includes(result.application_status)) {
          const link = document.createElement('a');
          link.href = `?view=graded#entry-${entry.dataset.entryId}`;
          link.textContent = ' View graded change';
          feedback.append(link);
        }
      }
      dismissGradedEntry(entry);
    } catch (error) {
      status.textContent = 'Unable to save. Please try again.';
    } finally {
      saving = false;
      buttons.forEach(item => { item.disabled = entry.dataset.dismissing === 'true'; });
    }
  }));
});
