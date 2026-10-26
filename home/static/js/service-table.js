(() => {
  const element = document.querySelector('.service-table');
  const detailRows = new Map();
  const infoButtons = [...document.querySelectorAll('[data-table-info]')];
  let table;

  infoButtons.forEach(button => {
    const row = document.getElementById(button.dataset.tableInfo);
    detailRows.set(button.dataset.tableInfo, row);
    // DataTables manages detail rows as children, not sortable data records.
    if (window.DataTable) row.remove();
    button.addEventListener('click', () => {
      const detail = detailRows.get(button.dataset.tableInfo);
      const open = button.getAttribute('aria-expanded') !== 'true';
      if (table) {
        const parent = table.row(button.closest('tr'));
        if (open) parent.child(detail).show();
        else parent.child.hide();
      }
      detail.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      button.textContent = open ? 'Close info' : 'Info';
      detail.querySelector('[data-service-info]').dispatchEvent(new CustomEvent('service-info-toggle', {
        detail: {open}
      }));
    });
  });

  document.querySelectorAll('[data-service-port]').forEach(link => {
    const destination = new URL(link.getAttribute('href'), window.location.href);
    destination.port = link.dataset.servicePort;
    link.href = destination.href;
  });

  if (!window.DataTable) return;
  table = new DataTable(element, {
    autoWidth: false,
    colReorder: {columns: ':not(.table-actions-heading)'},
    order: [],
    pageLength: 10,
    lengthMenu: [10, 25, 50, {label: 'All', value: -1}],
    stateSave: true,
    stateDuration: -1,
    columns: ['name', 'compose-service', 'version', 'state', 'image', 'ports', 'status', 'actions'].map((name, index) => ({
      name, width: `${[15, 13, 10, 9, 20, 17, 7, 9][index]}%`
    })),
    columnDefs: [{targets: -1, orderable: false, searchable: false}],
    language: {search: 'Search services:', lengthMenu: 'Show _MENU_ services',
      info: '_START_–_END_ of _TOTAL_ services', infoEmpty: 'No services',
      emptyTable: 'No services available', zeroRecords: 'No matching services'},
    initComplete() {
      const api = this.api();
      api.columns().every(function () {
        const column = this;
        const footer = column.footer();
        if (!footer || footer.dataset.filter === 'false') return;
        const input = document.createElement('input');
        input.type = 'search';
        input.placeholder = `Filter ${footer.dataset.label}`;
        input.setAttribute('aria-label', input.placeholder);
        input.value = column.search();
        footer.replaceChildren(input);
        input.addEventListener('input', () => {
          api.column(`${footer.dataset.column}:name`).search(input.value).draw();
        });
      });
    }
  });
  element.addEventListener('service-table-data', () => {
    if (!globalThis.getSelection?.()?.toString()) table.rows().invalidate('dom').draw(false);
  });
  document.querySelector('[data-table-reset]').addEventListener('click', () => {
    table.colReorder.reset();
    table.search('').columns().search('');
    element.querySelectorAll('tfoot input').forEach(input => { input.value = ''; });
    table.order([]).page.len(10).draw();
  });

  function exportRows() {
    const headers = table.columns().header().toArray().filter(header => !header.classList.contains('table-actions-heading'));
    const clean = cell => cell.textContent.trim().replace(/\s+/g, ' ');
    return [headers.map(clean), ...table.rows({search: 'applied', order: 'applied'}).nodes().toArray().map(row =>
      [...row.cells].filter(cell => !cell.classList.contains('table-actions')).map(clean)
    )];
  }
  const status = document.querySelector('[data-table-export-status]');
  document.querySelector('[data-table-copy]').addEventListener('click', async () => {
    try {
      const rows = exportRows();
      await navigator.clipboard.writeText(rows.map(row => row.join('\t')).join('\n'));
      status.textContent = `Copied ${rows.length - 1} filtered services as tab-separated text.`;
    } catch (_) {
      status.textContent = 'Clipboard unavailable. Select cells to copy, or download CSV.';
    }
  });
  document.querySelector('[data-table-csv]').addEventListener('click', () => {
    const rows = exportRows();
    const csv = rows.map(row => row.map(value => `"${value.replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff', csv], {type: 'text/csv;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'bookadaptor-services.csv';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `Downloaded ${rows.length - 1} filtered services.`;
  });
  document.querySelector('[data-table-tools]').hidden = false;
})();
