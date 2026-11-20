const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('home/static/js/polish.js', 'utf8');

function page(fetch) {
  const dom = new JSDOM(`<table><tbody data-model-scores></tbody></table>
    <article data-entry-id="1" data-preview="false" data-reaction-url="/polish/entries/1/reaction">
      <button data-reaction="Good" aria-pressed="false">Good</button>
      <button data-reaction="Awesome" aria-pressed="false">Awesome</button>
      <span class="reaction-status"></span><p data-application-status="pending"></p>
    </article>`, {runScripts:'outside-only'});
  dom.window.fetch = fetch;
  dom.window.eval(source);
  return dom.window.document;
}
const settled = () => new Promise(resolve => setImmediate(resolve));

test('A saved conflicting rating updates score and warning, and re-enables controls', async () => {
  const document = page(async (url, options) => {
    assert.equal(url, '/polish/entries/1/reaction');
    assert.deepEqual(JSON.parse(options.body), {reaction:'Good'});
    return {ok:true, json:async () => ({reaction:'Good', points:1,
      application_status:'conflict', application_message:'Merge conflict: target changed.',
      scores:[{model_label:'<img onerror=bad()>', rated_count:1, points:1}]})};
  });
  const button = document.querySelector('[data-reaction="Good"]');
  button.click();
  await settled();
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.equal(button.disabled, false);
  assert.equal(document.querySelector('[data-application-status]').dataset.applicationStatus, 'conflict');
  assert.match(document.querySelector('[data-application-status]').textContent, /Merge conflict/);
  assert.equal(document.querySelector('[data-model-scores]').textContent, '<img onerror=bad()>11');
  assert.equal(document.querySelector('img'), null);
});

test('A failed request preserves selection and scores and allows retry', async () => {
  let fail = true;
  const document = page(async () => {
    if (fail) throw Error('offline');
    return {ok:true, json:async () => ({reaction:'Awesome', application_status:'applied',
      application_message:'Applied.', scores:[{model_label:'Model', rated_count:1, points:5}]})};
  });
  const button = document.querySelector('[data-reaction="Awesome"]');
  button.click();
  await settled();
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.match(document.querySelector('.reaction-status').textContent, /Unable to save/);
  assert.equal(button.disabled, false);
  fail = false;
  button.click();
  await settled();
  assert.equal(document.querySelector('[data-application-status]').textContent, 'Applied.');
  assert.equal(button.getAttribute('aria-pressed'), 'true');
});

function queuePage(fetch) {
  const dom = new JSDOM(`<button data-goals-toggle aria-expanded="false">Show Goals</button>
    <section id="polish-goals" hidden><span data-goal-count>(1)</span><ol data-goals-list></ol></section>
    <div data-review-feedback hidden></div><aside class="application-warnings" hidden><div data-warning-list></div></aside>
    <span data-entry-count>1 entries to review</span>
    <div class="review-list" data-review-view="ungraded">
      <article data-entry-id="1" data-preview="false" data-reaction-url="/polish/entries/1/reaction">
        <h2>Test suggestion</h2><button data-reaction="Good">Good</button>
        <span class="reaction-status"></span><p data-application-status="pending"></p>
      </article><section data-review-empty hidden>All caught up.</section>
    </div>`, {runScripts:'outside-only'});
  dom.window.fetch = fetch;
  let finish;
  const article = dom.window.document.querySelector('article');
  article.animate = () => ({finished: new Promise(resolve => { finish = resolve; })});
  dom.window.eval(source);
  return {document:dom.window.document, finish: () => finish()};
}

test('A saved grade animates away, shrinks count and refreshes accepted goals', async () => {
  const queue = queuePage(async () => ({ok:true,json:async () => ({reaction:'Good',application_status:'applied',
    application_message:'Applied.',scores:[],goals:[{id:'G100',title:'A new goal',evidence:'A result'}]})}));
  const document = queue.document;
  document.querySelector('[data-goals-toggle]').click();
  assert.equal(document.querySelector('#polish-goals').hidden, false);
  assert.equal(document.querySelector('[data-goals-toggle]').getAttribute('aria-expanded'), 'true');
  document.querySelector('[data-reaction]').click();
  await settled();
  assert.equal(document.querySelector('article').dataset.dismissing, 'true');
  assert.equal(document.querySelector('[data-reaction]').disabled, true);
  assert.match(document.querySelector('[data-goals-list]').textContent, /G100 · A new goal/);
  queue.finish();
  await settled();
  assert.equal(document.querySelector('article'), null);
  assert.equal(document.querySelector('[data-entry-count]').textContent, '0 entries to review');
  assert.equal(document.querySelector('[data-review-empty]').hidden, false);
  document.querySelector('[data-goals-toggle]').click();
  assert.equal(document.querySelector('#polish-goals').hidden, true);
});

test('Conflict warnings survive removing their graded card', async () => {
  const queue = queuePage(async () => ({ok:true,json:async () => ({reaction:'Good',application_status:'conflict',
    application_message:'Merge conflict: overlap.',scores:[],goals:[]})}));
  queue.document.querySelector('[data-reaction]').click();
  await settled();
  queue.finish();
  await settled();
  assert.equal(queue.document.querySelector('article'), null);
  assert.equal(queue.document.querySelector('.application-warnings').hidden, false);
  assert.match(queue.document.querySelector('[data-warning-list]').textContent, /Merge conflict/);
  assert.equal(queue.document.querySelector('[data-warning-list] a').getAttribute('href'), '?view=graded#entry-1');
});

test('Failed grading leaves the item and count in place', async () => {
  const queue = queuePage(async () => ({ok:false}));
  queue.document.querySelector('[data-reaction]').click();
  await settled();
  assert.equal(queue.document.querySelector('article').dataset.dismissing, undefined);
  assert.equal(queue.document.querySelector('[data-entry-count]').textContent, '1 entries to review');
  assert.match(queue.document.querySelector('.reaction-status').textContent, /Unable to save/);
});
