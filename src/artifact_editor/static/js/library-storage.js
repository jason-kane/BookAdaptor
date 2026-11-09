(() => {
  const root = document.getElementById('library-page') || document.getElementById('book-storage');
  if (!root) return;
  const busy = state => ['queued', 'running'].includes(state);
  const cards = Array.from(root.querySelectorAll('.library-book'));
  const storageAvailable = root.dataset.storageAvailable !== 'false';
  const shelfAvailable = root.dataset.shelfAvailable !== 'false';
  const available = target => target === 'shelf' ? shelfAvailable : target === 'storage' ? storageAvailable : true;
  const buttons = card => Array.from(card.querySelectorAll('[data-move-target]'));
  let submitting = false;
  const refreshButtons = () => {
    const transferActive = submitting || cards.some(card => busy(card.dataset.transferState));
    for (const card of cards) {
      for (const button of buttons(card)) {
        button.disabled = transferActive || !available(button.dataset.moveTarget);
      }
    }
  };
  const byBook = new Map();
  let socket;
  let reconnectTimer;
  let reconnectDelay = 1000;
  let closed = false;
  let reloading = false;
  for (const card of cards) {
    const key = JSON.stringify([card.dataset.author, card.dataset.title]);
    if (!byBook.has(key)) byBook.set(key, []);
    byBook.get(key).push(card);
    const message = card.querySelector('.library-transfer-status');
    for (const button of buttons(card)) button.addEventListener('click', async () => {
      if (button.disabled) return;
      submitting = true;
      card.dataset.transferState = 'queued';
      refreshButtons();
      message.textContent = 'Queuing move…';
      try {
        const response = await fetch(card.dataset.locationUrl, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({target: button.dataset.moveTarget, source: button.dataset.moveSource || card.dataset.location})
        });
        const status = await response.json();
        if (!response.ok) throw new Error(status.error || 'Unable to move this book.');
        submitting = false;
        refreshButtons();
        // WebSocket events are authoritative and may arrive before this reply.
        if (message.textContent === 'Queuing move…') message.textContent = 'Waiting for a CPU worker…';
      } catch (error) {
        submitting = false;
        message.textContent = error.message;
        card.dataset.transferState = 'failed';
        refreshButtons();
      }
    });
  }
  const update = status => {
    if (status.type !== 'library_transfer') return;
    for (const card of byBook.get(JSON.stringify([status.author, status.title])) || []) {
      card.dataset.transferState = status.state || '';
      let text = status.message || '';
      if (Number(status.bytes_total) && busy(status.state)) {
        const percent = Math.min(100, Math.floor(100 * Number(status.bytes_done) / Number(status.bytes_total)));
        text += ` ${percent}%`;
      }
      const diskActivity = role => {
        const disk = status[`${role}_disk`];
        const read = Number(status[`${role}_read_iops`]);
        const write = Number(status[`${role}_write_iops`]);
        const busyPercent = Number(status[`${role}_busy_percent`]);
        if (!disk || !Number.isFinite(read) || !Number.isFinite(write)
                || !Number.isFinite(busyPercent)) return '';
        const location = role === 'source' ? status.source : status.target;
        const iops = value => Number.isInteger(value) ? value : value.toFixed(1);
        return `${location || role} ${disk}: read ${iops(read)} IOPS, write ${iops(write)} IOPS, ${Math.round(busyPercent)}% busy`;
      };
      const ioDetails = [diskActivity('source'), diskActivity('destination')].filter(Boolean);
      if (ioDetails.length) text += ` · ${ioDetails.join(' → ')}`;
      const transferStatus = card.querySelector('.library-transfer-status');
      transferStatus.textContent = text;
      transferStatus.title = ioDetails.length
        ? 'Read/write IOPS and busy time cover the entire physical disk, including other activity.'
        : '';
      refreshButtons();
      if (status.state === 'complete' && status.target !== card.dataset.location && !reloading) {
        reloading = true;
        if (root.dataset.transferCompleteUrl) window.location.href = root.dataset.transferCompleteUrl;
        else window.location.reload();
      }
    }
  };
  const connect = () => {
    if (closed || !root.dataset.transferWebsocket) return;
    socket = new WebSocket(root.dataset.transferWebsocket);
    socket.addEventListener('open', () => { reconnectDelay = 1000; });
    socket.addEventListener('message', event => {
      try { update(JSON.parse(event.data)); } catch (_) { /* Ignore unrelated/malformed messages. */ }
    });
    socket.addEventListener('close', () => {
      if (closed) return;
      for (const card of cards) {
        if (busy(card.dataset.transferState)) {
          card.querySelector('.library-transfer-status').textContent = 'Reconnecting to live transfer updates…';
        }
      }
      reconnectTimer = setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(10000, reconnectDelay * 2);
    });
  };
  window.addEventListener('pagehide', () => {
    closed = true;
    clearTimeout(reconnectTimer);
    socket?.close();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { closed = false; connect(); }
  });
  connect();
})();
