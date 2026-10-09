// Scena "libro": un libro da sfogliare sopra il gioco, che si intravede dietro (niente cutscene).
//
// Il libro arriva come pacchetto (docs/libri/libro.py nel repo del modulo): palco.oggetto() =
// { tipo, titolo, autore?, sottotitolo?, colore?, stemma?, fede?, copertina?, testo (il .md), immagini {nome: dataURL} }
// fede = { nome, titolo, oro, cera, inchiostro }: il simbolo sacro della divinita' nelle sue finiture (vince sullo stemma)
// Quattro tipi, ognuno con la sua rilegatura, carta e scrittura: grimorio, trattato, diario, lettera.
// Le pagine si fanno qui, misurando il testo nella pagina vera (font e misure dello schermo);
// "---" nel testo forza una pagina nuova. Il server non decide niente: si legge e basta.

const $ = (s, el = document) => el.querySelector(s);
const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const TIPI = ['grimorio', 'trattato', 'diario', 'lettera'];
const COLORI = { nero: '#1d1714', rosso: '#5c1616', verde: '#1f3b27', blu: '#1b2b4d', viola: '#35214c',
  marrone: '#4d2f1a', grigio: '#3b3a38', avorio: '#d9ccae', oro: '#8c6b22', bianco: '#e7e0cf' };
const COLORE_TIPO = { grimorio: 'nero', trattato: 'verde', diario: 'marrone', lettera: 'rosso' };

// ---------------------------------------------------------------- stemmi disegnati (viewBox 100x100)
function raggi(n, r1, r2, cx = 50, cy = 50, rot = 0) {
  let d = '';
  for (let i = 0; i < n * 2; i++) {
    const r = i % 2 ? r2 : r1, a = rot + Math.PI * i / n;
    d += (i ? 'L' : 'M') + (cx + Math.sin(a) * r).toFixed(1) + ' ' + (cy - Math.cos(a) * r).toFixed(1);
  }
  return d + 'Z';
}
const STEMMI = {
  sole: `<path d="${raggi(12, 44, 26)}"/><circle cx="50" cy="50" r="20"/>`,
  luna: '<path d="M62 12a40 40 0 1 0 0 76a32 32 0 1 1 0-76z"/>',
  occhio: '<path fill-rule="evenodd" d="M4 50Q50 6 96 50Q50 94 4 50ZM50 32a18 18 0 1 0 .1 0ZM50 42a8 8 0 1 1-.1 0Z"/>',
  stella: `<path d="${raggi(8, 46, 18)}"/>`,
  teschio: '<path fill-rule="evenodd" d="M50 8C26 8 14 26 14 44c0 12 6 20 12 24v14h48V68c6-4 12-12 12-24C86 26 74 8 50 8ZM36 38a9 10 0 1 0 .1 0ZM64 38a9 10 0 1 0 .1 0ZM50 54l-6 10h12Z"/><path d="M30 84h8v8h-8zM46 84h8v8h-8zM62 84h8v8h-8z"/>',
  mano: '<path d="M28 56V30a5 5 0 0 1 10 0v20V18a5 5 0 0 1 10 0v32V14a5 5 0 0 1 10 0v36V20a5 5 0 0 1 10 0v40c0 18-8 32-24 32c-12 0-18-6-24-14L12 58a5 5 0 0 1 8-6z"/>',
  chiave: '<path fill-rule="evenodd" d="M30 10a20 20 0 1 0 .1 0ZM30 20a10 10 0 1 1-.1 0Z"/><path d="M26 48h8v44h-8zM34 70h14v7H34zM34 82h10v7H34z"/>',
  rosa: '<circle cx="50" cy="50" r="12"/><path d="' + raggi(5, 40, 22, 50, 50, .3) + '" fill-opacity=".55"/><path d="' + raggi(5, 30, 16) + '" fill-opacity=".8"/>',
  spada: '<path d="M47 4h6l3 62h-12z"/><path d="M28 64h44v7H28z"/><path d="M46 71h8v16h-8z"/><circle cx="50" cy="91" r="6"/>',
  fiamma: '<path d="M50 4C56 24 76 34 76 60a26 26 0 0 1-52 0c0-14 8-20 12-28c2 10 6 14 10 16C44 34 44 18 50 4Z"/>',
};
function stemma(nome, immagini, cls = '') {
  if (!nome) return '';
  if (immagini && immagini[nome]) return `<img class="lb-stemma ${cls}" src="${immagini[nome]}" alt="">`;
  const p = STEMMI[nome];
  return p ? `<svg class="lb-stemma ${cls}" viewBox="0 0 100 100" aria-hidden="true">${p}</svg>` : '';
}

// il simbolo del libro in una finitura: quello della divinita' se c'e', se no lo stemma
function stemmaDi(lk, fin, cls = '', riserva = lk.stemma) {
  if (lk.fede && lk.fede[fin]) return `<img class="lb-stemma fede ${fin} ${cls}" src="${lk.fede[fin]}" alt="">`;
  return stemma(riserva, lk.immagini, cls);
}

// ---------------------------------------------------------------- testo -> blocchi
function inline(s, img) {
  let t = esc(s);
  t = t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>');
  t = t.replace(/:([a-z0-9_-]+):/g, (m, n) => (img[n] ? `<img class=lb-simb src=${img[n]} alt>` : m));
  return t;
}
function blocchi(testo, img) {
  const out = [];
  const righe = String(testo || '').replace(/\r\n/g, '\n').split('\n');
  let par = [], cit = [];
  const chiudiPar = () => { if (par.length) out.push({ t: 'p', html: par.map(r => inline(r, img)).join('<br>') }); par = []; };
  const chiudiCit = () => { if (cit.length) out.push({ t: 'cit', html: cit.map(r => inline(r, img)).join('<br>') }); cit = []; };
  for (const r0 of righe) {
    const r = r0.trimEnd();
    const nuda = r.trim();
    if (nuda.startsWith('>')) { chiudiPar(); cit.push(nuda.replace(/^>\s?/, '')); continue; }
    chiudiCit();
    if (!nuda) { chiudiPar(); continue; }
    if (/^-{3,}$/.test(nuda)) { chiudiPar(); out.push({ t: 'pagina' }); continue; }
    if (/^(\*\s*){3}$/.test(nuda) || nuda === '~') { chiudiPar(); out.push({ t: 'fregio' }); continue; }
    let m = nuda.match(/^(#{1,2})\s+(.*)$/);
    if (m) { chiudiPar(); out.push({ t: m[1].length === 1 ? 'h1' : 'h2', html: inline(m[2], img) }); continue; }
    m = nuda.match(/^!\[([^\]]*)\]\(([A-Za-z0-9_-]+)\)$/);
    if (m) { chiudiPar(); if (img[m[2]]) out.push({ t: 'img', src: img[m[2]], dida: inline(m[1], img) }); continue; }
    par.push(nuda);
  }
  chiudiPar(); chiudiCit();
  // capolettera: il primo paragrafo dopo un titolo (e il primo del libro)
  let dopoTitolo = true;
  for (const b of out) {
    if (b.t === 'h1') dopoTitolo = true;
    else if (b.t === 'p') { if (dopoTitolo) b.capo = true; dopoTitolo = false; }
  }
  return out;
}
function elBlocco(b) {
  switch (b.t) {
    case 'h1': return h('h2', 'lb-cap', b.html);
    case 'h2': return h('h3', 'lb-tit', b.html);
    case 'cit': return h('blockquote', 'lb-cit', b.html);
    case 'fregio': return h('div', 'lb-fregio', '<span>❦</span>');
    case 'img': {
      const f = h('figure', 'lb-ill');
      f.append(Object.assign(h('img'), { src: b.src, alt: '' }));
      if (b.dida) f.append(h('figcaption', '', b.dida));
      return f;
    }
    default: return h('p', b.capo ? 'capo' : b.segue ? 'segue' : '', b.html);
  }
}

// un paragrafo spezzato a parole (i tag <b>/<i> aperti si richiudono e riaprono dall'altra parte)
function spezza(html, n) {
  const pezzi = html.split(/(\s+)/);
  const a = pezzi.slice(0, n * 2 - 1).join(''), b = pezzi.slice(n * 2).join('');
  const aperti = [];
  for (const m of a.matchAll(/<(\/?)(b|i)>/g)) { if (m[1]) aperti.pop(); else aperti.push(m[2]); }
  return [a + aperti.slice().reverse().map(t => `</${t}>`).join(''), aperti.map(t => `<${t}>`).join('') + b];
}

// ---------------------------------------------------------------- impaginazione (misurando)
function impagina(misura, lista) {
  const pagine = [];
  let pag = [];
  const box = misura;
  const sta = () => box.scrollHeight <= box.clientHeight + 1;
  const nuova = () => { if (pag.length) pagine.push(pag); pag = []; box.innerHTML = ''; };
  const coda = lista.slice();
  while (coda.length) {
    const b = coda.shift();
    if (b.t === 'pagina') { nuova(); continue; }
    if (b.t === 'h1' && pag.length) nuova();                // un capitolo comincia su una pagina sua
    const el = elBlocco(b);
    box.append(el);
    if (sta()) { pag.push(b); continue; }
    box.removeChild(el);
    if (b.t === 'p' || b.t === 'cit') {
      // quante parole ci stanno ancora su questa pagina
      const parole = b.html.split(/\s+/).length;
      let lo = 0, hi = parole - 1;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        const prova = elBlocco(Object.assign({}, b, { html: spezza(b.html, mid)[0] }));
        box.append(prova);
        const ok = sta();
        box.removeChild(prova);
        if (ok) lo = mid; else hi = mid - 1;
      }
      if (lo >= 3) {                                        // almeno qualche parola, se no a capo pagina
        const [a, r] = spezza(b.html, lo);
        pag.push(Object.assign({}, b, { html: a }));
        coda.unshift(Object.assign({}, b, { html: r, capo: false, segue: true }));
        nuova();
        continue;
      }
    }
    if (!pag.length) { pag.push(b); nuova(); continue; }    // non ci sta nemmeno da sola: va com'e'
    nuova();
    coda.unshift(b);
  }
  nuova();
  return pagine;
}

// ---------------------------------------------------------------- facce del libro
function copertina(lk, quarta = false) {
  if (!quarta && lk.copertina && lk.immagini[lk.copertina])
    return `<div class="lb-cop lb-cop-img"><img src="${lk.immagini[lk.copertina]}" alt=""></div>`;
  const t = lk.tipo;
  const borchie = t === 'grimorio' ? '<i class="lb-angolo a1"></i><i class="lb-angolo a2"></i><i class="lb-angolo a3"></i><i class="lb-angolo a4"></i>' : '';
  const cinghia = t === 'diario' ? '<i class="lb-cinghia"></i>' : '';
  const cornice = t === 'trattato' ? '<i class="lb-cornice"></i>' : '';
  if (quarta) return `<div class="lb-cop lb-quarta">${borchie}${cornice}${stemmaDi(lk, 'oro', 'piccolo')}</div>`;
  return `<div class="lb-cop">${borchie}${cornice}${cinghia}
    <div class="lb-cop-dentro">
      <div class="lb-cop-titolo">${esc(lk.titolo)}</div>
      ${stemmaDi(lk, 'oro')}
      ${lk.autore ? `<div class="lb-cop-autore">${esc(lk.autore)}</div>` : ''}
    </div></div>`;
}
function frontespizio(lk) {
  return `<div class="lb-front">
    ${stemmaDi(lk, 'inchiostro', 'front')}
    <div class="lb-front-titolo">${esc(lk.titolo)}</div>
    ${lk.sottotitolo ? `<div class="lb-front-sotto">${esc(lk.sottotitolo)}</div>` : ''}
    <div class="lb-fregio"><span>❦</span></div>
    ${lk.autore ? `<div class="lb-front-autore">${esc(lk.autore)}</div>` : ''}
  </div>`;
}

// ---------------------------------------------------------------- la scena
export default async function (palco) {
  const lk = await palco.oggetto();
  if (palco.finita) return;
  if (!lk || !lk.libro) throw new Error('il libro non e\' arrivato');
  lk.tipo = TIPI.includes(lk.tipo) ? lk.tipo : 'trattato';
  lk.immagini = lk.immagini || {};
  const tipo = lk.tipo, lettera = tipo === 'lettera';
  const col = COLORI[lk.colore] || (/^#[0-9a-f]{6}$/i.test(lk.colore || '') ? lk.colore : COLORI[COLORE_TIPO[tipo]]);

  document.body.classList.add('libro', 'libro-' + tipo);
  const scena = $('#scena');
  const tavolo = h('div', 'lb-tavolo');
  const libro = h('div', 'lb-libro');
  libro.style.setProperty('--cop', col);
  const barra = h('div', 'lb-barra');
  tavolo.append(libro, barra);
  scena.append(tavolo);

  // misure: il libro aperto (due pagine) entra nello schermo; la lettera e' un foglio solo
  const misure = () => {
    const vh = window.innerHeight, vw = window.innerWidth;
    let H = Math.min(vh * 0.84, 980);
    let W = H * (lettera ? 0.74 : 0.68);
    const quanti = lettera ? 1.1 : 2.1;
    if (W * quanti > vw * 0.94) { W = vw * 0.94 / quanti; H = W / (lettera ? 0.74 : 0.68); }
    return { W: Math.round(W), H: Math.round(H) };
  };
  // la finestra puo' non avere ancora una misura (overlay appena mostrato, scheda in background)
  for (let i = 0; i < 100 && (window.innerWidth < 50 || window.innerHeight < 50); i++) {
    await new Promise(r => setTimeout(r, 30));
    if (palco.finita) return;
  }
  const { W, H } = misure();
  libro.style.setProperty('--W', W + 'px');
  libro.style.setProperty('--H', H + 'px');
  libro.style.setProperty('--fs', (H / (lettera ? 40 : 42)).toFixed(1) + 'px');

  // immagini e caratteri pronti prima di misurare
  await Promise.all(Object.values(lk.immagini).map(src => new Promise(r => { const i = new Image(); i.onload = i.onerror = r; i.src = src; })));
  try { await document.fonts.ready; } catch (e) { /* niente */ }
  if (palco.finita) return;

  // si misura in una pagina vera (stesse classi, stesse misure), invisibile
  const misura = h('div', 'lb-faccia fronte carta' + (lettera ? ' lb-foglio-lettera' : ''));
  const misuraTesto = h('div', 'lb-testo');
  misura.append(misuraTesto);
  const sede = lettera ? misura : h('div', 'lb-foglio');
  if (!lettera) sede.append(misura);
  sede.style.visibility = 'hidden';
  libro.append(sede);
  const pagine = impagina(misuraTesto, blocchi(lk.testo, lk.immagini));
  sede.remove();

  const facce = [];          // html di ogni faccia, in ordine
  const pagina = (bl, n) => {
    const d = h('div', 'lb-testo');
    for (const b of bl) d.append(elBlocco(b));
    return d.outerHTML + (n ? `<div class="lb-num">${n}</div>` : '');
  };
  if (lettera) {
    pagine.forEach((bl, i) => facce.push({ cls: 'carta', html: pagina(bl, pagine.length > 1 ? i + 1 : 0) }));
  } else {
    const contenuto = [{ cls: 'carta', html: frontespizio(lk) }];
    pagine.forEach((bl, i) => contenuto.push({ cls: 'carta', html: pagina(bl, i + 1) }));
    if (contenuto.length % 2) contenuto.push({ cls: 'carta vuota', html: '' });
    facce.push({ cls: 'copertina', html: copertina(lk) }, { cls: 'risguardo', html: '' }, ...contenuto,
               { cls: 'risguardo', html: '' }, { cls: 'copertina quarta', html: copertina(lk, true) });
  }

  // ---- un foglio = due facce (fronte a destra, retro a sinistra quando e' girato)
  const fogli = [];
  if (!lettera) {
    for (let i = 0; i < facce.length; i += 2) {
      const f = h('div', 'lb-foglio');
      const a = facce[i], b = facce[i + 1];
      f.append(h('div', 'lb-faccia fronte ' + a.cls, a.html), h('div', 'lb-faccia retro ' + b.cls, b.html));
      f.append(h('i', 'lb-ombra'));
      libro.append(f);
      fogli.push(f);
    }
  } else {
    // lettera: una busta col sigillo; rotto il sigillo, il foglio si apre
    const busta = h('div', 'lb-busta', `<div class="lb-sigillo">${stemmaDi(lk, 'cera', '', lk.stemma || 'rosa')}</div>
      <div class="lb-busta-titolo">${esc(lk.titolo)}</div>`);
    libro.append(busta);
    facce.forEach((fc, i) => { const f = h('div', 'lb-faccia fronte lb-foglio-lettera ' + fc.cls, fc.html); f.dataset.i = i; libro.append(f); fogli.push(f); });
  }

  let aperti = 0;             // fogli girati (libro) / foglio mostrato (lettera, -1 = busta chiusa)
  if (lettera) aperti = -1;
  // il sigillo e' di quella lettera nel mondo: se qualcuno l'ha gia' rotto, lo si trova rotto
  let giaRotto = lettera && !!(palco.dati && palco.dati.sigillo_rotto);
  if (giaRotto) libro.classList.add('rotto-prima');
  const n = fogli.length;
  const info = h('span', 'lb-info');
  const bPrima = h('button', 'lb-freccia', '‹'), bDopo = h('button', 'lb-freccia', '›'), bChiudi = h('button', 'lb-chiudi', '✕');
  bPrima.title = 'Pagina prima (←)'; bDopo.title = 'Pagina dopo (→)'; bChiudi.title = 'Chiudi (Esc)';
  barra.append(bPrima, info, bDopo, bChiudi);

  function disponi(animato = true) {
    if (lettera) {
      libro.classList.toggle('aperta', aperti >= 0);
      fogli.forEach((f, i) => { f.classList.toggle('su', i === aperti); f.classList.toggle('via', i < aperti); });
      info.textContent = aperti < 0 ? (giaRotto ? 'Il sigillo è già rotto · apri' : 'Rompi il sigillo') : (n > 1 ? `Foglio ${aperti + 1} di ${n}` : '');
      bPrima.disabled = aperti < 0; bDopo.disabled = aperti >= n - 1;
      return;
    }
    fogli.forEach((f, i) => {
      const girato = i < aperti;
      f.classList.toggle('girato', girato);
      if (!f.classList.contains('vola')) f.style.zIndex = girato ? i + 1 : n - i;
    });
    libro.style.setProperty('--sposta', aperti === 0 ? '-0.5' : aperti === n ? '0.5' : '0');
    const pp = pagine.length;
    if (aperti === 0) info.textContent = lk.titolo || '';
    else if (aperti === n) info.textContent = 'Fine';
    else {
      // a sinistra la faccia 2*aperti-1, a destra 2*aperti; il contenuto parte dalla faccia 2
      // (frontespizio = 0, poi le pagine numerate 1..pp)
      const sx = 2 * aperti - 3, dx = sx + 1;
      const nn = [sx, dx].filter(x => x >= 1 && x <= pp);
      info.textContent = nn.length ? `Pagina ${nn.join('–')} di ${pp}` : '';
    }
    bPrima.disabled = aperti === 0; bDopo.disabled = aperti === n;
  }
  function gira(dir) {
    if (lettera) {
      const v = Math.max(-1, Math.min(n - 1, aperti + dir));
      if (v === aperti) return;
      if (aperti === -1 && v === 0) {
        libro.classList.add('rotto');
        if (!giaRotto && !palco.prova) { giaRotto = true; palco.invia('sigillo', {}); }   // ora e' rotto per tutti
      }
      aperti = v; disponi(); return;
    }
    const i = dir > 0 ? aperti : aperti - 1;
    if (i < 0 || i >= n) return;
    const f = fogli[i];
    f.classList.add('vola');
    f.style.zIndex = n + 5;
    clearTimeout(f._t);
    f._t = setTimeout(() => { f.classList.remove('vola'); disponi(); }, 900);
    aperti += dir;
    disponi();
  }

  bPrima.addEventListener('click', () => gira(-1));
  bDopo.addEventListener('click', () => gira(1));
  bChiudi.addEventListener('click', () => palco.chiudi('esc'));
  libro.addEventListener('click', e => {
    if (lettera && aperti < 0) return gira(1);
    const r = libro.getBoundingClientRect();
    gira(e.clientX > r.left + r.width / 2 ? 1 : -1);
  });
  const tasti = e => {
    if (['ArrowRight', 'PageDown', ' ', 'd', 'D'].includes(e.key)) { e.preventDefault(); gira(1); }
    else if (['ArrowLeft', 'PageUp', 'a', 'A'].includes(e.key)) { e.preventDefault(); gira(-1); }
    else if (e.key === 'Home') { while (aperti > (lettera ? 0 : 0)) gira(-1); }
  };
  let rotella = 0;
  const ruota = e => { const t = Date.now(); if (t - rotella < 350) return; rotella = t; gira(e.deltaY > 0 ? 1 : -1); };
  document.addEventListener('keydown', tasti);
  window.addEventListener('wheel', ruota, { passive: true });
  palco.allaFine(() => {
    document.removeEventListener('keydown', tasti);
    window.removeEventListener('wheel', ruota);
    document.body.classList.remove('libro', 'libro-' + tipo);
  });

  // in prova (anteprima del redattore del Cartografo) si riapre alla pagina di prima, senza animazioni
  const chiave = palco.prova ? 'lb_aperti:' + (lk.titolo || '') : '';
  let ricorda = null;
  try { ricorda = chiave ? sessionStorage.getItem(chiave) : null; } catch (e) { /* niente */ }
  if (ricorda !== null) {
    const v = +ricorda;
    libro.classList.add('subito');
    if (lettera) { aperti = Math.max(-1, Math.min(n - 1, v)); if (aperti >= 0) libro.classList.add('rotto'); }
    else aperti = Math.max(0, Math.min(n, v));
    disponi(false);
    tavolo.classList.add('dentro', 'subito');
    requestAnimationFrame(() => requestAnimationFrame(() => { libro.classList.remove('subito'); tavolo.classList.remove('subito'); }));
  } else {
    disponi(false);
    requestAnimationFrame(() => tavolo.classList.add('dentro'));
  }
  if (chiave) {
    const salva = () => { try { sessionStorage.setItem(chiave, String(aperti)); } catch (e) { /* niente */ } };
    bPrima.addEventListener('click', salva); bDopo.addEventListener('click', salva);
    libro.addEventListener('click', salva); document.addEventListener('keyup', salva);
    window.addEventListener('wheel', () => setTimeout(salva, 0), { passive: true });
    palco.allaFine(() => document.removeEventListener('keyup', salva));
  }
}

// per il redattore dei libri del Cartografo (stessi stemmi e colori nelle scelte)
export { STEMMI, COLORI, COLORE_TIPO, TIPI };
