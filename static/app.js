/* ═══════════════════════════ VQM Screener — app.js ════════════════════════ */

// ── State ────────────────────────────────────────────────────────────────────
let allData      = [];
let activeFilter = 'ALL';
let sortBy       = 'score';
let historyChart = null;
// portfolio: Map<ticker, shares>
let portfolio    = new Map(Object.entries(JSON.parse(localStorage.getItem('vqm_portfolio_v2') || '{}')));
// Migrazione da vecchio storage Set-based (eseguita una volta sola)
(function() {
  if (!localStorage.getItem('vqm_portfolio_v2')) {
    try {
      const old = JSON.parse(localStorage.getItem('vqm_portfolio') || '[]');
      if (Array.isArray(old)) old.forEach(t => portfolio.set(t, 1));
      if (portfolio.size) localStorage.setItem('vqm_portfolio_v2', JSON.stringify(Object.fromEntries(portfolio)));
    } catch {}
  }
})();
let portfolioChart      = null;
let _sharesModalTicker  = null;
let thresholds   = {};   // {settore: {metrica: {good, bad, lower_is_better}}}
let fxRates      = { EUR: 1.0 };  // tassi → EUR, aggiornati da /api/fx-rates

// Colour helpers (shared)
const CLS_COLOR  = { BUY:'#00d084', HOLD:'#fbbf24', SELL:'#f87171' };
const clsColor   = cls => CLS_COLOR[cls] ?? '#52525e';
const pillClass  = cls => cls === 'BUY' ? 'pill-buy' : cls === 'HOLD' ? 'pill-hold' : cls === 'SELL' ? 'pill-sell' : 'pill-nd';

// ── Boot ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initFilterBtns();
  initSort();
  initSearch();
  loadLatest();
  loadLastRunLabel();
  loadThresholds();
  loadFxRates();
  updatePortfolioBadge();
  document.getElementById('shares-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter')  confirmSharesModal();
    if (e.key === 'Escape') cancelSharesModal();
  });
  // Se lo screener era già in corso prima del refresh, riattacca il polling
  fetch('/api/run-screener/status').then(r => r.json()).then(({ running }) => {
    if (running) {
      const btn   = document.getElementById('run-btn');
      const icon  = document.getElementById('run-icon');
      const label = document.getElementById('run-label');
      _pollScreener(btn, icon, label);
    }
  }).catch(() => {});
});

// ── API ──────────────────────────────────────────────────────────────────────
async function loadLatest() {
  try {
    const res = await fetch('/api/latest');
    if (!res.ok) throw new Error(res.statusText);
    allData = await res.json();
    document.getElementById('loading').classList.add('hidden');
    if (!allData.length) {
      document.getElementById('empty-state').classList.remove('hidden');
      return;
    }
    updateNavStats();
    renderCards();
  } catch {
    document.getElementById('loading').innerHTML =
      '<p class="text-red-400/70 text-sm text-center py-32 font-medium">Errore nel caricamento dati.</p>';
  }
}

async function loadLastRunLabel() {
  try {
    const res  = await fetch('/api/runs');
    const runs = await res.json();
    if (runs.length) {
      const d = new Date(runs[0].run_at);
      document.getElementById('last-run-label').textContent =
        d.toLocaleString('it-IT', { dateStyle: 'short', timeStyle: 'short' });
    }
  } catch { /* silent */ }
}

async function loadTickerHistory(ticker) {
  const res = await fetch(`/api/ticker/${encodeURIComponent(ticker)}`);
  return await res.json();
}

async function loadThresholds() {
  try {
    const res = await fetch('/api/thresholds');
    if (res.ok) thresholds = await res.json();
  } catch { /* silent */ }
}

async function loadFxRates() {
  try {
    const res = await fetch('/api/fx-rates');
    if (res.ok) fxRates = await res.json();
  } catch { /* silent */ }
}

// ── Refresh (ricarica dati dal DB senza rieseguire lo screener) ───────────────
async function refreshData() {
  const icon = document.getElementById('refresh-icon');
  icon.classList.add('spin');
  await loadLatest();
  await loadLastRunLabel();
  icon.classList.remove('spin');
}

// ── Run Screener (avvia esecuzione + polling) ────────────────────────────────
async function runScreener() {
  const btn   = document.getElementById('run-btn');
  const icon  = document.getElementById('run-icon');
  const label = document.getElementById('run-label');

  // Avvia run
  const res = await fetch('/api/run-screener', { method: 'POST' });
  if (res.status === 409) {
    // Già in esecuzione — aggancia comunque il polling
    _pollScreener(btn, icon, label);
    return;
  }
  if (!res.ok) return;

  _pollScreener(btn, icon, label);
}

function _pollScreener(btn, icon, label) {
  btn.disabled = true;
  // Sostituisce icona play con spinner
  icon.outerHTML = `<svg id="run-icon" class="spin" width="11" height="11" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
  </svg>`;
  label.textContent = 'In corso…';

  const interval = setInterval(async () => {
    try {
      const s = await fetch('/api/run-screener/status');
      const { running, error } = await s.json();
      if (!running) {
        clearInterval(interval);
        // Ripristina pulsante
        btn.disabled = false;
        document.getElementById('run-icon').outerHTML = `<svg id="run-icon" width="11" height="11"
          viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
        document.getElementById('run-label').textContent = 'Screener';
        // Ricarica i dati
        await loadLatest();
        await loadLastRunLabel();
      }
    } catch { /* rete momentaneamente irraggiungibile */ }
  }, 2000);
}

function updateNavStats() {
  const d = allData;
  document.getElementById('nav-total').textContent = `${d.length} titoli`;
  document.getElementById('nav-buy').textContent   = `${d.filter(r => r.classificazione === 'BUY').length} BUY`;
  document.getElementById('nav-hold').textContent  = `${d.filter(r => r.classificazione === 'HOLD').length} HOLD`;
  document.getElementById('nav-sell').textContent  = `${d.filter(r => r.classificazione === 'SELL').length} SELL`;
}

// ── Cards ─────────────────────────────────────────────────────────────────────
function renderCards() {
  const grid = document.getElementById('cards-grid');
  const q    = document.getElementById('search-input').value.toLowerCase();

  let data = allData.filter(r => {
    const matchF = activeFilter === 'ALL' || r.classificazione === activeFilter;
    const matchS = !q || (r.ticker || '').toLowerCase().includes(q) || (r.nome || '').toLowerCase().includes(q);
    return matchF && matchS;
  });

  data.sort((a, b) => {
    if (sortBy === 'score')     return (b.score_finale ?? -1) - (a.score_finale ?? -1);
    if (sortBy === 'score-asc') return (a.score_finale ?? -1) - (b.score_finale ?? -1);
    if (sortBy === 'ticker')    return (a.ticker || '').localeCompare(b.ticker || '');
    return 0;
  });

  grid.innerHTML = data.map((r, i) => cardHTML(r, i)).join('');
  data.forEach(r => {
    const el = document.getElementById(`card-${r.ticker}`);
    if (el) el.addEventListener('click', () => openDrawer(r));
  });
}

function cardHTML(r, idx) {
  const inPort = portfolio.has(r.ticker);
  const score  = r.score_finale ?? null;
  const cls    = r.classificazione ?? 'N/D';
  const pct    = score !== null ? Math.round((score / 10) * 100) : 0;
  const offset = 251.2 - (251.2 * pct / 100);
  const col    = clsColor(cls);
  const scoreTxt = score !== null ? score.toFixed(1) : '—';
  const prezzo   = r.prezzo != null ? fmtNum(r.prezzo, 2) + ' ' + (r.valuta ?? '') : '—';
  const delay    = Math.min(idx * 30, 300);

  return `
<div id="card-${r.ticker}" class="stock-card p-4 card-enter${inPort ? ' in-portfolio' : ''}"
     style="animation-delay:${delay}ms">

  <!-- header row -->
  <div class="flex items-start justify-between mb-3.5">
    <div class="flex-1 min-w-0 pr-2">
      <div class="flex items-center gap-1.5 mb-0.5">
        <span class="font-extrabold text-[15px] tracking-tight">${esc(r.ticker)}</span>
        <button id="port-btn-${r.ticker}" data-ticker="${esc(r.ticker)}"
          onclick="togglePortfolio(this,event)"
          class="text-[15px] leading-none transition-all duration-150 hover:scale-125 ${inPort ? 'text-yellow-400' : 'text-gray-700 hover:text-gray-500'}">
          ${inPort ? '★' : '☆'}
        </button>
      </div>
      <div class="text-[12px] text-gray-500 font-medium truncate">${esc(r.nome ?? '—')}</div>
      <div class="text-[11px] text-gray-700 truncate mt-0.5">${esc(r.settore ?? '—')}</div>
    </div>

    <!-- Score ring -->
    <div class="relative w-[52px] h-[52px] shrink-0">
      <svg class="w-full h-full -rotate-90" viewBox="0 0 88 88">
        <circle cx="44" cy="44" r="40" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="9"/>
        <circle cx="44" cy="44" r="40" fill="none" stroke="${col}" stroke-width="9"
          class="score-ring" style="stroke-dashoffset:${offset};filter:drop-shadow(0 0 4px ${col}66)"/>
      </svg>
      <span class="absolute inset-0 flex items-center justify-center text-[13px] font-black"
            style="color:${col}">${scoreTxt}</span>
    </div>
  </div>

  <!-- Pillar bars -->
  <div class="space-y-1.5 mb-2.5">
    ${miniPillar('V', r.score_value,    '#3b82f6')}
    ${miniPillar('Q', r.score_quality,  '#a855f7')}
    ${miniPillar('M', r.score_momentum, '#f97316')}
  </div>

  <!-- Quick metrics strip -->
  <div class="qm-strip flex items-center justify-between px-0.5 py-2 mb-1">
    ${_qm('ROE', r.roe, '%')}
    ${_qm(r.settore === 'Financial Services' ? 'P/Book' : 'FCF Yld',
          r.settore === 'Financial Services' ? r.p_book : r.fcf_yield,
          r.settore === 'Financial Services' ? 'x' : '%')}
    ${_qm(r.settore === 'Financial Services' ? 'EPS 4Y' : 'ROIC',
          r.settore === 'Financial Services' ? r.eps_cagr_4y : r.roic, '%')}
  </div>

  <!-- footer -->
  <div class="flex items-center justify-between pt-1.5">
    <span class="text-[11px] text-gray-600 font-medium tabular-nums">${prezzo}</span>
    <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold ${pillClass(cls)}">${cls}</span>
  </div>
</div>`;
}

function miniPillar(label, score, color) {
  const pct = score != null ? Math.round((score / 10) * 100) : 0;
  const txt = score != null ? score.toFixed(1) : '—';
  return `
  <div class="flex items-center gap-2">
    <span class="text-[10px] font-bold text-gray-700 w-3 shrink-0">${label}</span>
    <div class="pillar-track flex-1">
      <div class="pillar-fill" style="width:${pct}%;background:${color}"></div>
    </div>
    <span class="text-[11px] text-gray-600 w-5 text-right tabular-nums">${txt}</span>
  </div>`;
}

// ── Drawer ───────────────────────────────────────────────────────────────────
function openDrawer(r) {
  document.getElementById('drawer-title').textContent   = r.ticker;
  document.getElementById('drawer-subtitle').textContent = r.nome ?? '';
  const cls  = r.classificazione ?? 'N/D';
  const pill = document.getElementById('drawer-pill');
  pill.textContent = cls;
  pill.className   = `px-2.5 py-1 rounded-full text-[11px] font-bold shrink-0 ${pillClass(cls)}`;

  document.getElementById('drawer-body').innerHTML = buildDrawerBody(r);
  document.getElementById('drawer-overlay').classList.remove('hidden');
  document.getElementById('drawer').classList.remove('translate-x-full');

  loadTickerHistory(r.ticker).then(hist => {
    if (hist.length >= 2) renderHistoryChart(hist);
  });
}

function closeDrawer() {
  document.getElementById('drawer').classList.add('translate-x-full');
  document.getElementById('drawer-overlay').classList.add('hidden');
  if (historyChart) { historyChart.destroy(); historyChart = null; }
}

function buildDrawerBody(r) {
  const cls   = r.classificazione ?? 'N/D';
  const col   = clsColor(cls);
  const score = r.score_finale;
  const offset = score != null ? 251.2 - (251.2 * score / 10) : 251.2;
  const mktcap = r.mktcap ? fmtMktCap(r.mktcap) : '—';
  const prezzo = r.prezzo != null ? fmtNum(r.prezzo, 2) + ' ' + (r.valuta ?? '') : '—';

  return `
  <!-- Hero score -->
  <div class="rounded-2xl p-4 flex items-center gap-5"
       style="background:linear-gradient(160deg,rgba(255,255,255,.03) 0%,rgba(255,255,255,.01) 100%);border:1px solid rgba(255,255,255,.06)">
    <div class="relative w-[72px] h-[72px] shrink-0">
      <svg class="w-full h-full -rotate-90" viewBox="0 0 88 88">
        <circle cx="44" cy="44" r="40" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="9"/>
        <circle cx="44" cy="44" r="40" fill="none" stroke="${col}" stroke-width="9"
          class="score-ring" style="stroke-dashoffset:${offset};filter:drop-shadow(0 0 6px ${col}66)"/>
      </svg>
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <span class="text-xl font-black tabular-nums" style="color:${col}">${score != null ? score.toFixed(1) : '—'}</span>
        <span class="text-[9px] font-bold text-gray-600 tracking-widest uppercase">/ 10</span>
      </div>
    </div>
    <div class="flex-1 space-y-2">
      ${pillarBar('Value',    r.score_value,    '#3b82f6')}
      ${pillarBar('Quality',  r.score_quality,  '#a855f7')}
      ${pillarBar('Momentum', r.score_momentum, '#f97316')}
    </div>
  </div>

  <!-- Info strip -->
  <div class="grid grid-cols-2 gap-2">
    ${mRow('Settore',   r.settore)}
    ${mRow('Industria', r.industria)}
    ${mRow('Prezzo',    prezzo)}
    ${mRow('Mkt Cap',   mktcap)}
    ${mRow('Benchmark', r.benchmark)}
    ${mRow('Rank',      r.rank ? '#' + r.rank : '—')}
    ${mRow('Data run',  r.run_date ?? '—')}
    ${mRow('Valuta',    r.valuta)}
  </div>

  <!-- History chart -->
  <div>
    <div class="s-head text-gray-600 mb-3">Andamento Score</div>
    <div class="rounded-2xl p-3" style="background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05)">
      <canvas id="history-chart" height="155"></canvas>
    </div>
  </div>

  <!-- Value -->
  <div>
    <div class="s-head mb-3" style="color:#3b82f6">
      <span>Value</span>
      <span class="text-[11px] font-semibold text-gray-600 normal-case tracking-normal ml-1">${fmt1(r.score_value)} / 10</span>
    </div>
    <div class="grid grid-cols-2 gap-2">
      ${mValThr('EV/EBITDA', r.ev_ebitda, 'x',  'ev_ebitda', r.settore)}
      ${mValThr('P/FCF',     r.p_fcf,     'x',  'p_fcf',     r.settore)}
      ${mValThr('P/E',       r.pe,        'x',  'pe',        r.settore)}
      ${r.settore === 'Financial Services'
        ? mValThr('P/Book',   r.p_book,   'x',  'p_book',    r.settore)
        : mValThr('FCF Yield',r.fcf_yield,'%',  'fcf_yield', r.settore)}
    </div>
  </div>

  <!-- Quality -->
  <div>
    <div class="s-head mb-3" style="color:#a855f7">
      <span>Quality</span>
      <span class="text-[11px] font-semibold text-gray-600 normal-case tracking-normal ml-1">${fmt1(r.score_quality)} / 10</span>
    </div>
    <div class="grid grid-cols-2 gap-2">
      ${mValThr('ROE',           r.roe,          '%', 'roe',          r.settore)}
      ${mValThr('EBITDA Margin', r.ebitda_margin,'%', 'ebitda_margin',r.settore)}
      ${mValThr('ROIC',          r.roic,         '%', 'roic',         r.settore)}
      ${mValThr('D/E Ratio',     r.de_ratio,     'x', 'de_ratio',     r.settore)}
      ${mValThr('EPS CAGR ~4Y',  r.eps_cagr_4y ?? r.eps_cagr_5y,  '%', 'eps_cagr_4y',  r.settore)}
    </div>
  </div>

  <!-- Momentum -->
  <div>
    <div class="s-head mb-3" style="color:#f97316">
      <span>Momentum</span>
      <span class="text-[11px] font-semibold text-gray-600 normal-case tracking-normal ml-1">${fmt1(r.score_momentum)} / 10</span>
    </div>
    <div class="grid grid-cols-2 gap-2">
      ${mValThr('Mom 12M-1M',  r.mom_12m1m,  '%', 'mom_12m1m',  r.settore)}
      ${mValThr('EPS Revision',r.eps_rev,    '%', 'eps_rev',    r.settore)}
      ${mValThr('FCF Growth',  r.fcf_growth, '%', 'fcf_growth', r.settore)}
    </div>
  </div>

  <!-- Extra -->
  <div>
    <div class="s-head text-gray-600 mb-3">Extra</div>
    <div class="grid grid-cols-2 gap-2">
      ${mVal('Gross Margin',  r.gross_margin,     '%')}
      ${r.settore !== 'Financial Services' ? mVal('P/Book', r.p_book, 'x') : ''}
      ${mVal('Op. Margin',    r.operating_margin, '%')}
      ${mVal('Profit Margin', r.profit_margin,    '%')}
      ${mVal('Rev Growth',    r.rev_growth,       '%')}
      ${mVal('ROA',           r.roa,              '%')}
      ${mVal('Current Ratio', r.current_ratio,    'x')}
      ${mVal('Div. Yield',    r.dividend_yield,   '%')}
      ${mVal('PEG',           r.peg,              'x')}
      ${mVal('52W Change',    r.week52_change,    '%')}
      ${mVal('Rel. Strength', r.rel_strength,     '%')}
    </div>
  </div>

  ${r.commento_ai ? `
  <div class="rounded-2xl p-4" style="background:rgba(0,208,132,.05);border:1px solid rgba(0,208,132,.14)">
    <div class="s-head mb-3" style="color:#00d084">Analisi AI</div>
    <p class="text-[13px] text-gray-400 leading-relaxed">${esc(r.commento_ai)}</p>
  </div>` : ''}
  `;
}

function pillarBar(label, score, color) {
  const pct = score != null ? Math.round((score / 10) * 100) : 0;
  const txt = score != null ? score.toFixed(1) : '—';
  return `
  <div class="flex items-center gap-2.5">
    <span class="text-[11px] font-semibold text-gray-600 w-[58px] shrink-0">${label}</span>
    <div class="pillar-track flex-1"><div class="pillar-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="text-[12px] font-bold tabular-nums w-7 text-right" style="color:${color}">${txt}</span>
  </div>`;
}

// Quick-metric chip used in cards
function _qm(lbl, val, sym) {
  const txt = val != null ? fmtNum(val, 1) + '\u00a0' + sym : '—';
  const dim = val == null;
  return `<div class="text-center">
    <div class="qm-lbl">${lbl}</div>
    <div class="qm-val${dim ? ' dim' : ''}">${txt}</div>
  </div>`;
}

// ── History Chart ─────────────────────────────────────────────────────────────
function renderHistoryChart(hist) {
  const canvas = document.getElementById('history-chart');
  if (!canvas) return;
  if (historyChart) historyChart.destroy();

  const labels   = hist.map(h => h.run_date);
  const gradient = canvas.getContext('2d').createLinearGradient(0, 0, 0, 155);
  gradient.addColorStop(0,   'rgba(0,208,132,.18)');
  gradient.addColorStop(1,   'rgba(0,208,132,.0)');

  historyChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label:'Score',    data: hist.map(h => h.score_finale),   borderColor:'#00d084', backgroundColor: gradient,         tension:.35, fill:true,  borderWidth:2,   pointRadius:3, pointHoverRadius:5, pointBackgroundColor:'#00d084' },
        { label:'Value',    data: hist.map(h => h.score_value),    borderColor:'#3b82f6', backgroundColor:'transparent',     tension:.35, fill:false, borderWidth:1.5, pointRadius:2, borderDash:[4,4] },
        { label:'Quality',  data: hist.map(h => h.score_quality),  borderColor:'#a855f7', backgroundColor:'transparent',     tension:.35, fill:false, borderWidth:1.5, pointRadius:2, borderDash:[4,4] },
        { label:'Momentum', data: hist.map(h => h.score_momentum), borderColor:'#f97316', backgroundColor:'transparent',     tension:.35, fill:false, borderWidth:1.5, pointRadius:2, borderDash:[4,4] },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode:'index', intersect:false },
      scales: {
        x: { ticks:{ color:'#3f3f52', maxTicksLimit:6, font:{size:10} }, grid:{ color:'rgba(255,255,255,.035)' }, border:{ display:false } },
        y: { min:0, max:10, ticks:{ color:'#3f3f52', stepSize:2, font:{size:10} }, grid:{ color:'rgba(255,255,255,.035)' }, border:{ display:false } }
      },
      plugins: {
        legend: { labels:{ color:'#52525e', boxWidth:10, font:{size:10}, padding:12 } },
        tooltip: { backgroundColor:'#17171f', titleColor:'#f9fafb', bodyColor:'#9ca3af', borderColor:'rgba(255,255,255,.08)', borderWidth:1, cornerRadius:10, padding:10 }
      }
    }
  });
}

// ── Filter / sort / search ────────────────────────────────────────────────────
function initFilterBtns() {
  const btns = document.querySelectorAll('.filter-btn');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.className = 'filter-btn');
      const f = btn.dataset.filter;
      btn.classList.add(`f-active-${f}`);
      activeFilter = f;
      renderCards();
    });
  });
  const allBtn = document.querySelector('[data-filter="ALL"]');
  if (allBtn) allBtn.classList.add('f-active-ALL');
}

function initSort() {
  document.getElementById('sort-select').addEventListener('change', e => {
    sortBy = e.target.value;
    renderCards();
  });
}

function initSearch() {
  document.getElementById('search-input').addEventListener('input', renderCards);
}

// ── Portfolio ─────────────────────────────────────────────────────────────────
function savePortfolio() {
  localStorage.setItem('vqm_portfolio_v2', JSON.stringify(Object.fromEntries(portfolio)));
}

function togglePortfolio(btnEl, event) {
  event.stopPropagation();
  const ticker = btnEl.dataset.ticker;
  if (portfolio.has(ticker)) {
    portfolio.delete(ticker);
    _updatePortfolioBtnState(ticker, false);
    savePortfolio();
    updatePortfolioBadge();
  } else {
    _openSharesModal(ticker);
  }
}

function _updatePortfolioBtnState(ticker, inPort) {
  const btn  = document.getElementById(`port-btn-${ticker}`);
  const card = document.getElementById(`card-${ticker}`);
  if (btn) {
    btn.textContent = inPort ? '★' : '☆';
    btn.classList.toggle('text-yellow-400', inPort);
    btn.classList.toggle('text-gray-700', !inPort);
  }
  card?.classList.toggle('in-portfolio', inPort);
}

function _openSharesModal(ticker) {
  _sharesModalTicker = ticker;
  const r = allData.find(x => x.ticker === ticker);
  document.getElementById('shares-modal-title').textContent =
    ticker + (r?.nome ? ' \u2014 ' + r.nome : '');
  document.getElementById('shares-input').value = portfolio.get(ticker) ?? 1;
  document.getElementById('shares-modal').classList.remove('hidden');
  setTimeout(() => {
    const inp = document.getElementById('shares-input');
    inp.focus(); inp.select();
  }, 50);
}

function cancelSharesModal() {
  _sharesModalTicker = null;
  document.getElementById('shares-modal').classList.add('hidden');
}

function confirmSharesModal() {
  if (!_sharesModalTicker) return;
  const shares = Math.max(1, parseInt(document.getElementById('shares-input').value, 10) || 1);
  portfolio.set(_sharesModalTicker, shares);
  _updatePortfolioBtnState(_sharesModalTicker, true);
  savePortfolio();
  updatePortfolioBadge();
  document.getElementById('shares-modal').classList.add('hidden');
  _sharesModalTicker = null;
  // Aggiorna il panel se già aperto
  const panel = document.getElementById('portfolio-panel');
  if (panel && !panel.classList.contains('translate-y-full')) renderPortfolioPanel();
}

function editShares(ticker, event) {
  event.stopPropagation();
  _openSharesModal(ticker);
}

function updatePortfolioBadge() {
  const n     = portfolio.size;
  const badge = document.getElementById('portfolio-badge');
  const btn   = document.getElementById('portfolio-nav-btn');
  if (!badge || !btn) return;
  badge.textContent = n;
  if (n > 0) {
    badge.classList.remove('hidden');
    btn.classList.add('text-yellow-400', 'border-yellow-400/30');
    btn.classList.remove('text-gray-500');
  } else {
    badge.classList.add('hidden');
    btn.classList.remove('text-yellow-400', 'border-yellow-400/30');
    btn.classList.add('text-gray-500');
  }
}

function clearPortfolio() {
  if (!confirm(`Rimuovere tutti i ${portfolio.size} titoli dal portafoglio?`)) return;
  portfolio.clear();
  savePortfolio();
  updatePortfolioBadge();
  renderCards();
  closePortfolio();
}

function openPortfolio() {
  if (!portfolio.size) return;
  renderPortfolioPanel();
  document.getElementById('portfolio-overlay').classList.remove('hidden');
  document.getElementById('portfolio-panel').classList.remove('translate-y-full');
}

function closePortfolio() {
  document.getElementById('portfolio-panel').classList.add('translate-y-full');
  document.getElementById('portfolio-overlay').classList.add('hidden');
  if (portfolioChart) { portfolioChart.destroy(); portfolioChart = null; }
}

function portTickerClick(el) {
  const ticker = el.dataset.ticker;
  closePortfolio();
  const r = allData.find(x => x.ticker === ticker);
  if (r) openDrawer(r);
}

function removeFromPortfolio(ticker, event) {
  event.stopPropagation();
  portfolio.delete(ticker);
  savePortfolio();
  updatePortfolioBadge();
  _updatePortfolioBtnState(ticker, false);
  if (!portfolio.size) { closePortfolio(); return; }
  renderPortfolioPanel();
}

function renderPortfolioPanel() {
  const items = allData.filter(r => portfolio.has(r.ticker));
  const n     = items.length;
  document.getElementById('port-count').textContent = `${n} titol${n === 1 ? 'o' : 'i'} selezionati`;

  if (!n) {
    document.getElementById('portfolio-body').innerHTML =
      '<p class="text-gray-600 text-center text-sm py-12">Nessun titolo nel portafoglio.</p>';
    return;
  }

  // Arricchisci ogni posizione con shares e valore in EUR
  const toEur = (amount, currency) => {
    if (amount == null) return null;
    const rate = fxRates[(currency || 'EUR').toUpperCase()] ?? null;
    return rate ? amount * rate : amount;  // se tasso sconosciuto usa valore nominale
  };

  const positions = items.map(r => {
    const shares     = portfolio.get(r.ticker) ?? 1;
    const posValNom  = r.prezzo != null ? shares * r.prezzo : null;          // valuta originale
    const posVal     = posValNom != null ? toEur(posValNom, r.valuta) : null; // in EUR
    const rateUsed   = (fxRates[(r.valuta || 'EUR').toUpperCase()] ?? null);
    return { ...r, shares, posVal, posValNom, rateUsed };
  });

  const hasValues  = positions.some(p => p.posVal != null);
  const totalValue = hasValues ? positions.reduce((s, p) => s + (p.posVal ?? 0), 0) : null;

  // Peso: per valore € se disponibile, altrimenti equi-pesato
  const getW = p => {
    if (hasValues && totalValue > 0) return p.posVal != null ? p.posVal / totalValue : 0;
    return 1 / n;
  };
  const ws = positions.map(getW);

  // Media ponderata (solo valori non-null)
  const wavg = key => {
    let sum = 0, wsum = 0;
    positions.forEach((p, i) => { const v = p[key]; if (v != null) { sum += v * ws[i]; wsum += ws[i]; } });
    return wsum > 0 ? sum / wsum : null;
  };

  const buyC  = positions.filter(r => r.classificazione === 'BUY').length;
  const holdC = positions.filter(r => r.classificazione === 'HOLD').length;
  const sellC = positions.filter(r => r.classificazione === 'SELL').length;

  // Ordina per valore decrescente (o score se manca il prezzo)
  const sorted = [...positions].sort((a, b) =>
    (b.posVal ?? b.score_finale ?? -1) - (a.posVal ?? a.score_finale ?? -1)
  );

  const avgScore  = wavg('score_finale');
  const avgOffset = avgScore != null ? 251.2 - (251.2 * avgScore / 10) : 251.2;
  const avgCol    = avgScore != null ? (avgScore >= 7.5 ? '#00d084' : avgScore >= 5 ? '#fbbf24' : '#f87171') : '#52525e';

  document.getElementById('portfolio-body').innerHTML = `
    <!-- Signal sheet + avg score -->
    <div class="grid grid-cols-4 gap-2.5">
      <div class="rounded-2xl p-3.5 text-center" style="background:rgba(0,208,132,.07);border:1px solid rgba(0,208,132,.18)">
        <div class="text-2xl font-black" style="color:#00d084">${buyC}</div>
        <div class="text-[10px] font-bold text-gray-600 uppercase tracking-widest mt-0.5">BUY</div>
      </div>
      <div class="rounded-2xl p-3.5 text-center" style="background:rgba(251,191,36,.07);border:1px solid rgba(251,191,36,.18)">
        <div class="text-2xl font-black text-yellow-400">${holdC}</div>
        <div class="text-[10px] font-bold text-gray-600 uppercase tracking-widest mt-0.5">HOLD</div>
      </div>
      <div class="rounded-2xl p-3.5 text-center" style="background:rgba(239,68,68,.07);border:1px solid rgba(239,68,68,.18)">
        <div class="text-2xl font-black text-red-400">${sellC}</div>
        <div class="text-[10px] font-bold text-gray-600 uppercase tracking-widest mt-0.5">SELL</div>
      </div>
      <div class="rounded-2xl p-3.5 flex flex-col items-center justify-center gap-0.5"
           style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06)">
        <div class="relative w-10 h-10">
          <svg class="w-full h-full -rotate-90" viewBox="0 0 88 88">
            <circle cx="44" cy="44" r="40" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="9"/>
            <circle cx="44" cy="44" r="40" fill="none" stroke="${avgCol}" stroke-width="9"
              class="score-ring" style="stroke-dashoffset:${avgOffset}"/>
          </svg>
          <span class="absolute inset-0 flex items-center justify-center text-[11px] font-black"
                style="color:${avgCol}">${fmt1(avgScore)}</span>
        </div>
        <div class="text-[10px] font-bold text-gray-600 uppercase tracking-widest">Score</div>
      </div>
    </div>

    <!-- Grafico a torta composizione -->
    <div>
      <div class="s-head text-gray-600 mb-3">
        Composizione
        ${totalValue ? `<span class="text-gray-700 normal-case tracking-normal font-medium">· ${fmtMktCap(totalValue)}\u00a0EUR</span>` : ''}
      </div>
      <div class="rounded-2xl p-3" style="background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05)">
        <canvas id="portfolio-pie" height="200"></canvas>
      </div>
    </div>

    <!-- Score pesati per valore -->
    <div class="rounded-2xl p-4" style="background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.055)">
      <div class="s-head text-gray-600 mb-3">
        Score Pesati
        ${hasValues ? '<span class="text-[10px] font-medium text-gray-700 normal-case tracking-normal">(per valore &euro;)</span>' : ''}
      </div>
      <div class="space-y-2.5">
        ${pillarBar('Value',    wavg('score_value'),    '#3b82f6')}
        ${pillarBar('Quality',  wavg('score_quality'),  '#a855f7')}
        ${pillarBar('Momentum', wavg('score_momentum'), '#f97316')}
      </div>
    </div>

    <!-- Ticker list con shares, valore e peso -->
    <div>
      <div class="s-head text-gray-600 mb-3">Titoli (${n})</div>
      <div class="space-y-1.5">
        ${sorted.map(p => {
          const col    = clsColor(p.classificazione ?? 'N/D');
          const wPct   = hasValues && totalValue > 0 && p.posVal != null
                         ? (p.posVal / totalValue * 100).toFixed(1) + '%'
                         : (100 / n).toFixed(1) + '%';
          const valStr = (() => {
            if (p.posValNom == null) return '—';
            const origCur = (p.valuta || 'EUR').toUpperCase();
            const orig    = fmtNum(p.posValNom, 0) + '\u00a0' + origCur;
            if (origCur === 'EUR' || p.posVal == null || p.rateUsed == null) return orig;
            return orig + ' ≈ ' + fmtNum(p.posVal, 0) + '\u00a0EUR';
          })();
          const barW   = Math.max(3, Math.min(60, parseFloat(wPct) * 1.5));
          return `
          <div class="rounded-xl overflow-hidden cursor-pointer transition-all"
               style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05)"
               onclick="portTickerClick(this)" data-ticker="${esc(p.ticker)}"
               onmouseenter="this.style.background='rgba(255,255,255,.04)'"
               onmouseleave="this.style.background='rgba(255,255,255,.025)'">
            <div class="flex items-center gap-2.5 px-3.5 pt-2.5 pb-1">
              <div class="w-2 h-2 rounded-full shrink-0" style="background:${col};box-shadow:0 0 5px ${col}88"></div>
              <div class="flex-1 min-w-0">
                <span class="font-bold text-[13px]">${esc(p.ticker)}</span>
                <span class="ml-1.5 text-[11px] text-gray-600">${esc(p.nome ?? '')}</span>
              </div>
              <span class="text-[13px] font-black tabular-nums" style="color:${col}">${fmt1(p.score_finale)}</span>
              <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${pillClass(p.classificazione ?? 'N/D')}">${p.classificazione ?? 'N/D'}</span>
              <button data-ticker="${esc(p.ticker)}" onclick="editShares(this.dataset.ticker,event)"
                class="text-gray-600 hover:text-accent transition text-[13px] leading-none shrink-0" title="Modifica quantit\u00e0">&#x270E;</button>
              <button data-ticker="${esc(p.ticker)}" onclick="removeFromPortfolio(this.dataset.ticker,event)"
                class="text-gray-700 hover:text-red-400 transition text-[16px] leading-none shrink-0">&times;</button>
            </div>
            <div class="flex items-center gap-2.5 px-3.5 pb-2" style="padding-left:2.3rem">
              <span class="text-[11px] font-semibold text-gray-600">${p.shares}&#x202F;&times;</span>
              ${p.prezzo != null ? `<span class="text-[11px] text-gray-700">@ ${fmtNum(p.prezzo, 2)}</span>` : ''}
              <span class="text-[11px] font-semibold text-gray-400">${valStr}</span>
              <div class="ml-auto flex items-center gap-1.5">
                <div class="h-1.5 rounded-full" style="width:${barW}px;background:${col}77"></div>
                <span class="text-[11px] font-bold tabular-nums" style="color:${col}cc">${wPct}</span>
              </div>
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>

    <!-- Metriche pesate per valore -->
    <div>
      <div class="s-head text-gray-600 mb-3">Metriche Pesate</div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
        ${mVal('P/E',          wavg('pe'),            'x')}
        ${mVal('EV/EBITDA',    wavg('ev_ebitda'),     'x')}
        ${mVal('FCF Yield',    wavg('fcf_yield'),     '%')}
        ${mVal('ROE',          wavg('roe'),           '%')}
        ${mVal('ROIC',         wavg('roic'),          '%')}
        ${mVal('EBITDA Margin',wavg('ebitda_margin'), '%')}
        ${mVal('EPS CAGR ~4Y', wavg('eps_cagr_4y'),  '%')}
        ${mVal('FCF Growth',   wavg('fcf_growth'),    '%')}
        ${mVal('Rev. Growth',  wavg('rev_growth'),    '%')}
        ${mVal('Div. Yield',   wavg('dividend_yield'),'%')}
      </div>
    </div>
  `;

  _renderPortfolioPie(sorted, hasValues, totalValue);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtNum(v, dec) {
  if (v == null) return '—';
  return Number(v).toLocaleString('it-IT', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}
function fmt1(v) { return v != null ? Number(v).toFixed(1) : '—'; }

function fmtMktCap(v) {
  if (!v) return '—';
  if (v >= 1e12) return (v / 1e12).toFixed(2) + ' T';
  if (v >= 1e9)  return (v / 1e9).toFixed(1)  + ' B';
  if (v >= 1e6)  return (v / 1e6).toFixed(0)  + ' M';
  return String(v);
}

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

function mRow(label, value) {
  return `
  <div class="m-tile">
    <div class="lbl">${label}</div>
    <div class="val">${esc(String(value ?? '—'))}</div>
  </div>`;
}

function mVal(label, value, unit) {
  const isN = value == null;
  const txt  = isN ? '—' : fmtNum(value, 2) + '\u00a0' + unit;
  return `
  <div class="m-tile">
    <div class="lbl">${label}</div>
    <div class="val${isN ? ' dim' : ''}">${txt}</div>
  </div>`;
}

// Restituisce la soglia per la metrica del settore dato (fallback su _default)
function getMetricThr(sector, metricKey) {
  return (thresholds[sector]  && thresholds[sector][metricKey])
      || (thresholds['_default'] && thresholds['_default'][metricKey])
      || null;
}

// Colore del valore rispetto alle soglie: verde/giallo/rosso
function metricColor(value, thr) {
  if (value == null || !thr || thr.good == null || thr.bad == null) return null;
  const { good, bad, lower_is_better } = thr;
  if (lower_is_better) {
    if (value <= good) return '#00d084';
    if (value >= bad)  return '#f87171';
    return '#fbbf24';
  } else {
    if (value >= good) return '#00d084';
    if (value <= bad)  return '#f87171';
    return '#fbbf24';
  }
}

// Tile metrica con soglie (usato nel drawer per Value/Quality/Momentum)
function mValThr(label, value, unit, metricKey, sector) {
  const isN  = value == null;
  const vTxt = isN ? '—' : fmtNum(value, 2) + '\u00a0' + unit;
  const thr  = getMetricThr(sector, metricKey);
  const col  = isN ? null : metricColor(value, thr);

  let thrLine = '';
  if (thr && thr.good != null && thr.bad != null) {
    const fv  = v => Number(v).toLocaleString('it-IT', { maximumFractionDigits: 2 });
    const sym = unit === '%' ? '%' : '\u00a0' + unit;
    if (thr.lower_is_better) {
      thrLine = `<span style="color:#00d084">&#x2713;&#xA0;&#x2264;${fv(thr.good)}${sym}</span>`
              + `<span class="mx-1" style="color:#3f3f52">·</span>`
              + `<span style="color:#f87171">&#x2715;&#xA0;&#x2265;${fv(thr.bad)}${sym}</span>`;
    } else {
      thrLine = `<span style="color:#00d084">&#x2713;&#xA0;&#x2265;${fv(thr.good)}${sym}</span>`
              + `<span class="mx-1" style="color:#3f3f52">·</span>`
              + `<span style="color:#f87171">&#x2715;&#xA0;&#x2264;${fv(thr.bad)}${sym}</span>`;
    }
  } else if (thr && (thr.good == null || thr.bad == null)) {
    thrLine = `<span style="color:#3f3f52">N/A settore</span>`;
  }

  const borderLeft = col ? `border-left:2px solid ${col};padding-left:.65rem;` : '';
  return `
  <div class="m-tile" style="${borderLeft}">
    <div class="lbl">${label}</div>
    <div class="val${isN ? ' dim' : ''}" style="${col ? 'color:'+col : ''}">${vTxt}</div>
    ${thrLine ? `<div class="thr">${thrLine}</div>` : ''}
  </div>`;
}

// ── Portfolio Pie Chart ──────────────────────────────────────────────────────
function _renderPortfolioPie(sorted, hasValues, totalValue) {
  const canvas = document.getElementById('portfolio-pie');
  if (!canvas) return;
  if (portfolioChart) { portfolioChart.destroy(); portfolioChart = null; }

  const labels = sorted.map(p => p.ticker);
  const data   = sorted.map(p => {
    if (hasValues && totalValue > 0)
      return p.posVal != null ? +((p.posVal / totalValue) * 100).toFixed(2) : 0;
    return +(100 / sorted.length).toFixed(2);
  });
  const colors = _pieColors(sorted.length);

  portfolioChart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors.map(c => c + 'cc'),
        borderColor:     colors.map(c => c + '55'),
        borderWidth: 1,
        hoverBorderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: {
          position: 'right',
          labels: { color:'#6b7280', boxWidth:10, font:{ size:10 }, padding:8 }
        },
        tooltip: {
          backgroundColor:'#17171f', titleColor:'#f9fafb', bodyColor:'#9ca3af',
          borderColor:'rgba(255,255,255,.08)', borderWidth:1, cornerRadius:10, padding:10,
          callbacks: { label: ctx => ` ${ctx.label}: ${ctx.parsed.toFixed(1)}%` }
        }
      }
    }
  });
}

function _pieColors(n) {
  const palette = ['#00d084','#3b82f6','#a855f7','#f97316','#fbbf24',
                   '#ec4899','#14b8a6','#6366f1','#84cc16','#0ea5e9','#f43f5e','#8b5cf6'];
  if (n <= palette.length) return palette.slice(0, n);
  const colors = [...palette];
  for (let i = palette.length; i < n; i++) {
    colors.push(`hsl(${(i * 137.5) % 360},70%,55%)`);
  }
  return colors;
}

// ── PWA Install ───────────────────────────────────────────────────────────────
let _pwaPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  _pwaPrompt = e;
  const btn = document.getElementById('pwa-install-btn');
  if (btn) btn.classList.replace('hidden', 'flex');
});

window.addEventListener('appinstalled', () => {
  _pwaPrompt = null;
  const btn = document.getElementById('pwa-install-btn');
  if (btn) btn.classList.replace('flex', 'hidden');
});

function pwaInstall() {
  if (!_pwaPrompt) return;
  _pwaPrompt.prompt();
  _pwaPrompt.userChoice.then(() => { _pwaPrompt = null; });
}

// ── Service Worker registration ────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch((err) => console.warn('SW registration failed:', err));
  });
}
