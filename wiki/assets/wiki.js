(() => {
  const key = 'rag-engineering-book';
  let state = {};
  try { state = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch {}
  const save = () => { try { localStorage.setItem(key, JSON.stringify(state)); } catch {} };
  const root = document.documentElement;
  const size = Math.min(28, Math.max(17, Number(state.size) || 20));
  root.style.setProperty('--body-size', size + 'px');
  document.querySelectorAll('[data-size]').forEach(button => button.addEventListener('click', () => {
    state.size = Math.min(28, Math.max(17, (Number(state.size) || 20) + Number(button.dataset.size)));
    root.style.setProperty('--body-size', state.size + 'px'); save();
  }));
  const chapter = document.querySelector('[data-chapter]');
  if (chapter) {
    let timer;
    const remember = () => {
      state.chapter = chapter.dataset.chapter;
      state.y = scrollY / Math.max(1, document.documentElement.scrollHeight - innerHeight);
      state.title = document.querySelector('h1')?.textContent || state.chapter;
      save();
    };
    addEventListener('scroll', () => { clearTimeout(timer); timer = setTimeout(remember, 120); }, {passive:true});
    addEventListener('pagehide', remember);
    if (location.hash === '#resume' && state.chapter === chapter.dataset.chapter) {
      requestAnimationFrame(() => scrollTo(0, (Number(state.y) || 0) * Math.max(1, document.documentElement.scrollHeight - innerHeight)));
    }
  }
  const resume = document.querySelector('[data-resume]');
  if (resume && state.chapter && /^[a-z0-9-]+$/.test(state.chapter)) {
    resume.hidden = false;
    const link = resume.querySelector('a');
    link.href = `chapters/${state.chapter}.html#resume`;
    link.textContent = `继续阅读：${state.title || state.chapter}`;
  }
  document.querySelectorAll('table').forEach(table => {
    const wrap = document.createElement('div');
    wrap.className = 'table-wrap'; wrap.tabIndex = 0;
    table.parentNode.insertBefore(wrap,table); wrap.appendChild(table);
  });
})();

/* Progressive reading controls; no network and no dependency on writable storage. */
(() => {
  const root = document.documentElement;
  const themeKey = 'ebook-reader:appearance';
  const get = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const put = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  let theme = get(themeKey) || root.dataset.theme || 'auto';
  if (!['auto', 'light', 'dark'].includes(theme)) theme = 'auto';
  const old = document.querySelector('button[data-theme]');
  const button = old ? old.cloneNode(true) : document.createElement('button');
  button.type = 'button';
  button.dataset.readerTheme = '';
  button.classList.add('control');
  if (old) old.replaceWith(button);
  else {
    const tools = document.querySelector('.reader-tools,.topbar .tools,.topbar,.bar-inner nav,.tools');
    if (tools) tools.append(button);
  }
  function applyTheme() {
    root.dataset.theme = theme;
    button.textContent = {auto:'自动',light:'浅色',dark:'深色'}[theme];
    button.setAttribute('aria-label', '外观：' + button.textContent + '，点击切换');
    button.title = '依次切换自动、浅色、深色';
  }
  applyTheme();
  button.addEventListener('click', () => {
    theme = {auto:'light',light:'dark',dark:'auto'}[theme];
    applyTheme(); put(themeKey, theme);
  });

  const bookNav = document.querySelector('.layout>nav.toc');
  if (bookNav) {
    const wrapper = document.createElement('details');
    wrapper.className = 'reader-book-toc';
    const wide = matchMedia('(min-width:1001px)');
    wrapper.open = wide.matches;
    wide.addEventListener('change', event => { wrapper.open = event.matches; });
    const summary = document.createElement('summary'); summary.textContent = '全书目录';
    bookNav.before(wrapper); wrapper.append(summary, bookNav);
  }
  const rail = document.querySelector('.chapter-rail');
  if (rail && !document.querySelector('.reader-mobile-toc')) {
    const list = rail.querySelector('ol');
    const header = document.querySelector('.chapter-head');
    if (list && header) {
      const toc = document.createElement('details');
      toc.className = 'reader-mobile-toc';
      const label = document.createElement('summary');
      label.textContent = '本章目录';
      toc.append(label, list.cloneNode(true));
      header.after(toc);
    }
  }
  document.querySelectorAll('table').forEach(table => {
    if (table.closest('.table-wrap')) return;
    const wrap = document.createElement('div');
    wrap.className = 'table-wrap';
    table.before(wrap); wrap.append(table);
  });
  document.querySelectorAll('.table-wrap').forEach(wrap => {
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    if (!wrap.hasAttribute('aria-label')) wrap.setAttribute('aria-label', '数据表，可横向滚动');
  });
  document.querySelectorAll('pre').forEach(pre => {
    pre.tabIndex = 0;
    pre.setAttribute('aria-label', '代码示例，可横向滚动');
  });
  document.querySelectorAll('.prose pre,.content pre').forEach(pre => {
    if (pre.querySelector('button')) return;
    const copy = document.createElement('button');
    copy.className = 'copy'; copy.type = 'button'; copy.textContent = '复制';
    pre.append(copy);
  });
  document.querySelectorAll('button.copy').forEach(oldButton => {
    // Replace the original listener so clipboard rejection always has a recovery.
    const copy = oldButton.cloneNode(true);
    oldButton.replaceWith(copy);
    copy.setAttribute('aria-label', '复制这段代码');
    copy.setAttribute('aria-live', 'polite');
    copy.addEventListener('click', async () => {
      const code = copy.closest('pre')?.querySelector('code');
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code.textContent);
        copy.textContent = '已复制';
      } catch {
        const range = document.createRange(); range.selectNodeContents(code);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
        copy.textContent = '已选中';
        copy.title = '浏览器限制复制，请按 Ctrl+C 或长按复制';
      }
      setTimeout(() => { copy.textContent = '复制'; }, 1600);
    });
  });
  // Print folded examples too, then restore the reader's disclosure state.
  let printDetails = [];
  addEventListener('beforeprint', () => {
    printDetails = [...new Set([...printDetails, ...document.querySelectorAll('main details:not([open])')])];
    printDetails.forEach(details => { details.open = true; });
  });
  addEventListener('afterprint', () => {
    printDetails.forEach(details => { details.open = false; });
    printDetails = [];
  });
  const links = [...document.querySelectorAll('.chapter-rail a[href^="#"],.reader-mobile-toc a[href^="#"],nav.toc>a[href^="#"]')];
  const headings = [...new Set(links.map(link => document.getElementById(decodeURIComponent(link.hash.slice(1)))).filter(Boolean))];
  if (headings.length && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        links.forEach(link => {
          if (decodeURIComponent(link.hash.slice(1)) === entry.target.id) link.setAttribute('aria-current','location');
          else link.removeAttribute('aria-current');
        });
      }
    }, {rootMargin:'-10% 0px -65% 0px'});
    headings.forEach(heading => observer.observe(heading));
  }
})();
