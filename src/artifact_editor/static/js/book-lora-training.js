(() => {
  let socket;
  let reconnectTimer;
  let reconnectDelay = 1000;
  let closed = false;

  const update = html => {
    const documentRoot = new DOMParser().parseFromString(html, 'text/html')
      .getElementById('book-lora-training');
    const current = document.getElementById('book-lora-training');
    if (documentRoot && current) current.innerHTML = documentRoot.innerHTML;
  };

  const connect = () => {
    const root = document.getElementById('book-lora-training');
    if (closed || !root?.dataset.loraWebsocket) return;
    socket = new WebSocket(root.dataset.loraWebsocket);
    socket.addEventListener('open', () => { reconnectDelay = 1000; });
    socket.addEventListener('message', event => update(event.data));
    socket.addEventListener('close', () => {
      if (closed) return;
      reconnectTimer = window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(10000, reconnectDelay * 2);
    });
  };

  connect();
  window.addEventListener('pagehide', () => {
    closed = true;
    window.clearTimeout(reconnectTimer);
    socket?.close();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { closed = false; connect(); }
  });
})();
