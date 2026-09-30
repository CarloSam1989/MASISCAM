(() => {
  const table = document.getElementById('usuarios-tabla');
  if (!table) return;
  const body = table.tBodies[0];
  const rows = Array.from(body.rows);
  const search = document.getElementById('usuarios-buscar');
  const previous = document.getElementById('usuarios-anterior');
  const next = document.getElementById('usuarios-siguiente');
  const headers = Array.from(table.tHead.rows[0].cells).slice(0, 4);
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
  const collator = new Intl.Collator('es', {numeric: true, sensitivity: 'base'});
  let page = 0;
  let column = 0;
  let direction = 1;
  const perPage = 10;

  function render() {
    const query = normalize(search.value);
    const filtered = rows.filter(row => Array.from(row.cells).slice(0, 4)
      .some(cell => normalize(cell.textContent).includes(query)));
    filtered.sort((a, b) => direction * collator.compare(
      a.cells[column].textContent.trim(), b.cells[column].textContent.trim()));
    const pages = Math.max(1, Math.ceil(filtered.length / perPage));
    page = Math.min(page, pages - 1);
    // Move existing nodes to preserve action forms, CSRF fields and event handlers.
    body.replaceChildren(...filtered.slice(page * perPage, (page + 1) * perPage));
    headers.forEach((header, index) => header.setAttribute('aria-sort',
      index === column ? (direction === 1 ? 'ascending' : 'descending') : 'none'));
    previous.disabled = page === 0;
    next.disabled = page >= pages - 1;
    document.getElementById('usuarios-vacio').hidden = filtered.length > 0;
    const start = filtered.length ? page * perPage + 1 : 0;
    const end = Math.min((page + 1) * perPage, filtered.length);
    document.getElementById('usuarios-info').textContent =
      `Mostrando ${start} a ${end} de ${filtered.length} usuarios · Página ${page + 1} de ${pages}`;
  }

  headers.forEach((header, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-link p-0 text-reset fw-bold text-decoration-none';
    button.textContent = `${header.textContent} ↕`;
    button.setAttribute('aria-label', `Ordenar por ${header.textContent}`);
    button.addEventListener('click', () => {
      direction = column === index ? -direction : 1;
      column = index;
      page = 0;
      render();
    });
    header.replaceChildren(button);
  });
  search.addEventListener('input', () => { page = 0; render(); });
  previous.addEventListener('click', () => { page -= 1; render(); });
  next.addEventListener('click', () => { page += 1; render(); });
  document.getElementById('usuarios-controles').hidden = false;
  document.getElementById('usuarios-paginacion').hidden = false;
  render();
})();
