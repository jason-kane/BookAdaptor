(() => {
  const root = document.getElementById('library-page');
  const toggle = document.getElementById('show-unprocessed-books');
  const location = document.getElementById('library-location');
  if (!root || !toggle) return;
  const updateVisibility = () => {
    for (const series of root.querySelectorAll('.library-series')) {
      let visible = false;
      for (const book of series.querySelectorAll('.library-book')) {
        book.hidden = (!toggle.checked && book.dataset.processed !== 'true') ||
          (location && location.value !== 'all' && book.dataset.location !== location.value);
        visible = visible || !book.hidden;
      }
      series.querySelector('.library-series-empty').hidden = visible;
    }
    for (const author of root.querySelectorAll('.library-author')) {
      author.hidden = !Array.from(author.querySelectorAll('.library-book')).some(book => !book.hidden);
    }
    const empty = document.getElementById('library-filter-empty');
    if (empty) empty.hidden = Array.from(root.querySelectorAll('.library-book')).some(book => !book.hidden);
  };
  toggle.addEventListener('change', updateVisibility);
  location?.addEventListener('change', updateVisibility);
  updateVisibility();
})();
