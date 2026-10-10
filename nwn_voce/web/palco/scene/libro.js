// Scena "libro": un testo da leggere sopra il gioco, che si intravede dietro (niente cutscene).
// Non solo libri: lettere, pergamene, avvisi, e scritte su MONUMENTI (lapidi, statue, piastre di metallo).
//
// Il libro arriva come pacchetto (docs/libri/libro.py nel repo del modulo): palco.oggetto() =
// { tipo, titolo, autore?, sottotitolo?, colore?, stemma?, fede?, copertina?, testo (il .md), immagini {nome: dataURL},
//   + le scelte di stile (tutte facoltative, se mancano vale il PRESET del tipo):
//   rilegatura, carta, scrittura, inchiostro, capolettera, decori, formato, busta, cera, simbolo }
// fede = { nome, titolo, oro, cera, inchiostro }: il simbolo sacro della divinita' nelle sue finiture (vince sullo stemma)
// Ogni tipo e' un preset completo; ogni scelta si puo' cambiare a parte (Cartografo, "Personalizza").
// Tre forme: libro (si sfoglia), foglio (lettera, pergamena, avviso: un foglio alla volta, la lettera nella busta
// col sigillo), superficie (pietra, marmo, metallo, legno: niente copertina, le facce si susseguono).
// Tipi nuovi (stele, cartello, segnale) arrivano come "variante" di un tipo vecchio (intarsio, piastra):
// un Companion che non li conosce mostra il tipo vecchio con le scelte scritte, non un libro.
// Le pagine si fanno qui, misurando il testo nella pagina vera; "---" nel testo forza una pagina nuova.

const $ = (s, el = document) => el.querySelector(s);
const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const COLORI = { nero: '#1d1714', rosso: '#5c1616', verde: '#1f3b27', blu: '#1b2b4d', viola: '#35214c',
  marrone: '#4d2f1a', grigio: '#3b3a38', avorio: '#d9ccae', oro: '#8c6b22', bianco: '#e7e0cf' };
const CERE = { rosso: '#a3221b', nero: '#2a2524', verde: '#2f5a33', blu: '#26406e', oro: '#b08a2e', viola: '#5a2f72', bianco: '#d8d0bf' };

// ---------------------------------------------------------------- i tipi: un preset completo ciascuno
const PRESET = {
  grimorio: { forma: 'libro', rilegatura: 'cuoio', carta: 'macchiata', scrittura: 'gotica', inchiostro: 'nero', capolettera: 'gotico', decori: 'borchie,bruciato', formato: 'normale', colore: 'nero' },
  trattato: { forma: 'libro', rilegatura: 'tela', carta: 'vecchia', scrittura: 'elegante', inchiostro: 'nero', capolettera: 'miniato', decori: 'cornice', formato: 'normale', colore: 'verde' },
  diario: { forma: 'libro', rilegatura: 'consunta', carta: 'righe', scrittura: 'mano', inchiostro: 'blu', capolettera: 'no', decori: 'cinghia', formato: 'normale', colore: 'marrone' },
  lettera: { forma: 'foglio', busta: 'si', carta: 'lettera', scrittura: 'calligrafia', inchiostro: 'seppia', capolettera: 'no', decori: '', formato: 'normale', cera: 'rosso', colore: 'rosso' },
  pergamena: { forma: 'foglio', busta: 'no', carta: 'pergamena', scrittura: 'stampa', inchiostro: 'seppia', capolettera: 'semplice', decori: 'rotolo', formato: 'normale', cera: 'rosso', colore: 'marrone' },
  avviso: { forma: 'foglio', busta: 'no', carta: 'vecchia', scrittura: 'stampa', inchiostro: 'nero', capolettera: 'no', decori: 'chiodo,macchie', formato: 'piccolo', cera: 'rosso', colore: 'marrone' },
  incisione: { forma: 'superficie', carta: 'pietra', scrittura: 'lapidaria', inchiostro: 'incisa', capolettera: 'no', decori: 'crepe,muschio', formato: 'normale', colore: 'grigio' },
  intarsio: { forma: 'superficie', carta: 'marmo', scrittura: 'lapidaria', inchiostro: 'dorata', capolettera: 'no', decori: 'cornice', formato: 'normale', colore: 'avorio' },
  piastra: { forma: 'superficie', carta: 'bronzo', scrittura: 'lapidaria', inchiostro: 'incisa', capolettera: 'no', decori: 'chiodi,patina', formato: 'largo', colore: 'oro' },
  // stele: una lastra alta e centinata, il testo lungo come in un libro ma intarsiato d'oro nell'ardesia
  stele: { forma: 'superficie', carta: 'ardesia', scrittura: 'elegante', inchiostro: 'dorata', capolettera: 'semplice', decori: 'cornice', formato: 'alto', colore: 'grigio' },
  // cartello e segnale: assi di legno inchiodate, poche parole dipinte in grande (il segnale e' a freccia)
  cartello: { forma: 'superficie', carta: 'legno', scrittura: 'insegna', inchiostro: 'bianco', capolettera: 'no', decori: 'assi,chiodi', formato: 'largo', colore: 'marrone' },
  segnale: { forma: 'superficie', carta: 'legno', scrittura: 'insegna', inchiostro: 'bianco', capolettera: 'no', decori: 'assi,freccia', formato: 'largo', colore: 'marrone' },
};
const TIPI = Object.keys(PRESET);
const COLORE_TIPO = Object.fromEntries(TIPI.map(t => [t, PRESET[t].colore]));
// le scelte possibili (il Cartografo le mostra in "Personalizza"): valore -> nome
const OPZIONI = {
  rilegatura: { cuoio: 'Cuoio', tela: 'Tela', consunta: 'Cuoio consunto', legno: 'Legno', metallo: 'Metallo' },
  carta: { vecchia: 'Carta vecchia', bianca: 'Carta bianca', pergamena: 'Pergamena', macchiata: 'Carta macchiata', righe: 'Carta a righe',
    lettera: 'Carta da lettere', nera: 'Carta nera', pelle: 'Pelle conciata',
    pietra: 'Pietra', marmo: 'Marmo', ardesia: 'Ardesia', ossidiana: 'Ossidiana', bronzo: 'Bronzo', ferro: 'Ferro', oro: 'Oro', argento: 'Argento', legno: 'Legno' },
  scrittura: { stampa: 'A stampa antica', elegante: 'Elegante', gotica: 'Gotica', mano: 'A mano', calligrafia: 'Calligrafia', lapidaria: 'Lapidaria (maiuscole romane)', insegna: 'Insegna (grande, dipinta)', rune: 'Rune (illeggibile)' },
  inchiostro: { nero: 'Nero', seppia: 'Seppia', blu: 'Blu', rosso: 'Rosso sangue', verde: 'Verde', viola: 'Viola', bianco: 'Bianco', oro: 'Oro', argento: 'Argento',
    incisa: 'Incisa', rilievo: 'In rilievo', dorata: 'Intarsio d\'oro', argentata: 'Intarsio d\'argento' },
  capolettera: { no: 'Nessuno', semplice: 'Semplice', miniato: 'Miniato', gotico: 'Gotico rosso' },
  decori: { borchie: 'Borchie agli angoli', cornice: 'Cornice', cinghia: 'Cinghia', bruciato: 'Bordi bruciati', macchie: 'Macchie', strappi: 'Bordi strappati',
    rotolo: 'Arrotolata', chiodo: 'Chiodo (avviso)', chiodi: 'Chiodi agli angoli', crepe: 'Crepe', muschio: 'Muschio', patina: 'Patina verde', ruggine: 'Ruggine',
    assi: 'Assi di legno', freccia: 'A freccia verso destra', frecciasx: 'A freccia verso sinistra' },
  formato: { piccolo: 'Piccolo', normale: 'Normale', grande: 'Grande', largo: 'Largo (piastra)', alto: 'Alto (stele)' },
  busta: { si: 'Nella busta col sigillo', no: 'Senza busta' },
};
const SUPERFICI = ['pietra', 'marmo', 'ardesia', 'ossidiana', 'bronzo', 'ferro', 'oro', 'argento', 'legno'];
const CARTE_SCURE = ['nera', 'ardesia', 'ossidiana', 'ferro'];

// il libro con le sue scelte: quelle scritte vincono, le altre vengono dal preset del tipo
function stile(lk) {
  const p = PRESET[lk.tipo] || PRESET.trattato;
  const s = {};
  for (const k of ['forma', 'rilegatura', 'carta', 'scrittura', 'inchiostro', 'capolettera', 'decori', 'formato', 'busta', 'cera']) {
    const v = String(lk[k] ?? '').trim().toLowerCase();
    s[k] = v && (k === 'forma' || k === 'decori' || k === 'cera' || !OPZIONI[k] || OPZIONI[k][v]) ? v : (p[k] ?? '');
  }
  if (!['libro', 'foglio', 'superficie'].includes(s.forma)) s.forma = p.forma;
  s.decori = s.decori === 'nessuno' ? [] : s.decori.split(/[,\s]+/).filter(d => OPZIONI.decori[d]);
  return s;
}

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
// un'immagine colorata a tinta unita (simbolo ricolorato): l'immagine fa da maschera
const tinta = (src, col, cls) => `<span class="lb-stemma tinta ${cls}" style="--tinta:${col};-webkit-mask-image:url('${src}');mask-image:url('${src}')"></span>`;
function stemma(nome, lk, cls = '') {
  if (!nome) return '';
  const im = lk.immagini || {};
  if (im[nome]) return lk.simbolo ? tinta(im[nome], lk.simbolo, cls) : `<img class="lb-stemma ${cls}" src="${im[nome]}" alt="">`;
  const p = STEMMI[nome];
  return p ? `<svg class="lb-stemma ${cls}" viewBox="0 0 100 100" aria-hidden="true"${lk.simbolo ? ` style="fill:${lk.simbolo}"` : ''}>${p}</svg>` : '';
}
// il simbolo del libro in una finitura: quello della divinita' se c'e', se no lo stemma
function stemmaDi(lk, fin, cls = '', riserva = lk.stemma) {
  if (lk.fede && lk.fede[fin]) return lk.simbolo ? tinta(lk.fede[fin], lk.simbolo, `fede ${fin} ${cls}`) : `<img class="lb-stemma fede ${fin} ${cls}" src="${lk.fede[fin]}" alt="">`;
  return stemma(riserva, lk, cls);
}

// ---------------------------------------------------------------- rune: il testo traslitterato nel futhark
const RUNE = { th: 'ᚦ', ng: 'ᛜ', a: 'ᚨ', b: 'ᛒ', c: 'ᚲ', d: 'ᛞ', e: 'ᛖ', f: 'ᚠ', g: 'ᚷ', h: 'ᚺ', i: 'ᛁ', j: 'ᛃ', k: 'ᚲ', l: 'ᛚ', m: 'ᛗ', n: 'ᚾ', o: 'ᛟ',
  p: 'ᛈ', q: 'ᚲ', r: 'ᚱ', s: 'ᛊ', t: 'ᛏ', u: 'ᚢ', v: 'ᚹ', w: 'ᚹ', x: 'ᚲᛊ', y: 'ᛃ', z: 'ᛉ' };
const runa = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/th|ng|[a-z]/g, m => RUNE[m] || m);

// ---------------------------------------------------------------- testo -> blocchi
function inline(s, img, rune) {
  // i simboli :nome: restano simboli anche in runico
  const pezzi = String(s).split(/(:[a-z0-9_-]+:)/);
  let t = pezzi.map(p => (/^:[a-z0-9_-]+:$/.test(p) ? p : esc(rune ? runa(p) : p))).join('');
  t = t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>');
  t = t.replace(/:([a-z0-9_-]+):/g, (m, n) => (img[n] ? `<img class="lb-simb" src="${img[n]}" alt="">` : m));
  return t;
}
function blocchi(testo, img, rune) {
  const out = [];
  const righe = String(testo || '').replace(/\r\n/g, '\n').split('\n');
  let par = [], cit = [];
  const il = r => inline(r, img, rune);
  const chiudiPar = () => { if (par.length) out.push({ t: 'p', html: par.map(il).join('<br>') }); par = []; };
  const chiudiCit = () => { if (cit.length) out.push({ t: 'cit', html: cit.map(il).join('<br>') }); cit = []; };
  for (const r0 of righe) {
    const r = r0.trimEnd();
    const nuda = r.trim();
    if (nuda.startsWith('>')) { chiudiPar(); cit.push(nuda.replace(/^>\s?/, '')); continue; }
    chiudiCit();
    if (!nuda) { chiudiPar(); continue; }
    if (/^-{3,}$/.test(nuda)) { chiudiPar(); out.push({ t: 'pagina' }); continue; }
    if (/^(\*\s*){3}$/.test(nuda) || nuda === '~') { chiudiPar(); out.push({ t: 'fregio' }); continue; }
    let m = nuda.match(/^(#{1,2})\s+(.*)$/);
    if (m) { chiudiPar(); out.push({ t: m[1].length === 1 ? 'h1' : 'h2', html: il(m[2]) }); continue; }
    m = nuda.match(/^!\[([^\]]*)\]\(([A-Za-z0-9_-]+)\)$/);
    if (m) { chiudiPar(); if (img[m[2]]) out.push({ t: 'img', src: img[m[2]], dida: il(m[1]) }); continue; }
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
    default: return h('p', [b.capo ? 'capo' : b.segue ? 'segue' : '', b.taglia ? 'taglia' : ''].join(' ').trim(), b.html);
  }
}

// un paragrafo spezzato a parole (i tag <b>/<i> aperti si richiudono e riaprono dall'altra parte).
// Gli spazi DENTRO un tag (<img class=.. src=..> dei simboli) non sono confini di parola: spezzato li', il
// simbolo si rompeva e sulla pagina dopo usciva il resto del tag come testo
const SPAZI = /(\s+)(?![^<]*>)/;
const parole = html => html.split(SPAZI).filter((_, i) => i % 2 === 0).length;
function spezza(html, n) {
  const pezzi = html.split(SPAZI);
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
      let lo = 0, hi = parole(b.html) - 1;
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
        pag.push(Object.assign({}, b, { html: a, taglia: true }));     // continua alla pagina dopo
        coda.unshift(Object.assign({}, b, { html: r, capo: false, segue: true, taglia: false }));
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
function copertina(lk, st, quarta = false) {
  if (!quarta && lk.copertina && lk.immagini[lk.copertina])
    return `<div class="lb-cop lb-cop-img"><img src="${lk.immagini[lk.copertina]}" alt=""></div>`;
  const d = st.decori;
  const borchie = d.includes('borchie') ? '<i class="lb-angolo a1"></i><i class="lb-angolo a2"></i><i class="lb-angolo a3"></i><i class="lb-angolo a4"></i>' : '';
  const cinghia = d.includes('cinghia') ? '<i class="lb-cinghia"></i>' : '';
  const cornice = d.includes('cornice') ? '<i class="lb-cornice"></i>' : '';
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
// le decorazioni sopra una pagina (foglio e superficie)
function sopra(st) {
  const d = st.decori;
  let s = '';
  if (d.includes('chiodi')) s += '<i class="lb-chiodo c1"></i><i class="lb-chiodo c2"></i><i class="lb-chiodo c3"></i><i class="lb-chiodo c4"></i>';
  if (d.includes('chiodo')) s += '<i class="lb-chiodo c0"></i>';
  if (d.includes('rotolo')) s += '<i class="lb-rullo su"></i><i class="lb-rullo giu"></i>';
  if (st.forma === 'superficie' && d.includes('cornice')) s += '<i class="lb-incorniciata"></i>';
  if (st.forma === 'superficie' && d.includes('muschio')) s += '<i class="lb-muschio"></i>';
  return s;
}

// ---------------------------------------------------------------- la scena
export default async function (palco) {
  const lk = await palco.oggetto();
  if (palco.finita) return;
  if (!lk || !lk.libro) throw new Error('il libro non e\' arrivato');
  lk.tipo = TIPI.includes(lk.variante) ? lk.variante : TIPI.includes(lk.tipo) ? lk.tipo : 'trattato';
  // solo immagini incorporate (data:image/...;base64): finiscono dentro l'HTML delle pagine
  const IMG_OK = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/;
  lk.immagini = Object.fromEntries(Object.entries(lk.immagini || {}).filter(([k, v]) => /^[A-Za-z0-9_-]+$/.test(k) && IMG_OK.test(v)));
  if (lk.fede && typeof lk.fede !== 'object') lk.fede = null;
  if (lk.fede) for (const f of ['oro', 'cera', 'inchiostro']) if (!IMG_OK.test(lk.fede[f] || '')) delete lk.fede[f];
  if (lk.simbolo && !/^#[0-9a-f]{6}$/i.test(lk.simbolo)) lk.simbolo = '';
  const st = stile(lk);
  const tipo = lk.tipo, forma = st.forma, foglio = forma === 'foglio', sup = forma === 'superficie';
  const busta = foglio && st.busta !== 'no';
  const col = COLORI[lk.colore] || (/^#[0-9a-f]{6}$/i.test(lk.colore || '') ? lk.colore : COLORI[PRESET[tipo].colore]);
  const cera = CERE[st.cera] || (/^#[0-9a-f]{6}$/i.test(st.cera || '') ? st.cera : CERE.rosso);
  const rune = st.scrittura === 'rune';

  const classi = ['libro', 'libro-' + tipo, 'lb-forma-' + forma, 'lb-ril-' + st.rilegatura, 'lb-carta-' + st.carta, 'lb-scr-' + st.scrittura,
    'lb-ink-' + st.inchiostro, 'lb-capo-' + st.capolettera, 'lb-fmt-' + st.formato, ...st.decori.map(d => 'lb-dec-' + d)];
  if (CARTE_SCURE.includes(st.carta)) classi.push('lb-scura');
  if (busta) classi.push('lb-busta-si');
  if (SUPERFICI.includes(st.carta)) classi.push('lb-materiale');
  document.body.classList.add(...classi);
  const scena = $('#scena');
  const tavolo = h('div', 'lb-tavolo');
  const libro = h('div', 'lb-libro');
  libro.style.setProperty('--cop', col);
  libro.style.setProperty('--cera', cera);
  if (lk.simbolo) libro.style.setProperty('--simbolo', lk.simbolo);
  const barra = h('div', 'lb-barra');
  tavolo.append(libro, barra);
  scena.append(tavolo);

  // misure: il libro aperto (due pagine) entra nello schermo; foglio e superficie sono una faccia sola
  const prop = sup ? ({ largo: 1.45, alto: 0.62, grande: 0.8, piccolo: 0.8 }[st.formato] || 0.78) : foglio ? 0.74 : 0.68;
  const scala = { piccolo: 0.8, grande: 1.08 }[st.formato] || 1;
  const misure = () => {
    const vh = window.innerHeight, vw = window.innerWidth;
    let H = Math.min(vh * 0.84, 980) * scala;
    let W = H * prop;
    const quanti = forma === 'libro' ? 2.1 : 1.1;
    if (W * quanti > vw * 0.94) { W = vw * 0.94 / quanti; H = W / prop; }
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
  // il carattere si misura sul lato piu' corto (una piastra larga non ha lettere enormi)
  libro.style.setProperty('--fs', ((sup ? Math.min(H, W / 0.78) * 1.12 : H) / (forma === 'libro' ? 42 : 40)).toFixed(1) + 'px');

  // immagini e caratteri pronti prima di misurare
  await Promise.all(Object.values(lk.immagini).map(src => new Promise(r => { const i = new Image(); i.onload = i.onerror = r; i.src = src; })));
  try { await document.fonts.ready; } catch (e) { /* niente */ }
  if (palco.finita) return;

  // si misura in una pagina vera (stesse classi, stesse misure), invisibile
  const unaFaccia = foglio || sup;
  const clsFaccia = sup ? 'lb-faccia fronte lb-sup' : foglio ? 'lb-faccia fronte carta lb-foglio-lettera' : 'lb-faccia fronte carta';
  const misura = h('div', clsFaccia);
  const misuraTesto = h('div', 'lb-testo');
  misura.append(misuraTesto);
  const sede = unaFaccia ? misura : h('div', 'lb-foglio');
  if (!unaFaccia) sede.append(misura);
  sede.style.visibility = 'hidden';
  libro.append(sede);
  const pagine = impagina(misuraTesto, blocchi(lk.testo, lk.immagini, rune));
  sede.remove();

  const facce = [];          // html di ogni faccia, in ordine
  const pagina = (bl, n) => {
    const d = h('div', 'lb-testo');
    for (const b of bl) d.append(elBlocco(b));
    return d.outerHTML + (n ? `<div class="lb-num">${n}</div>` : '');
  };
  if (unaFaccia) {
    pagine.forEach((bl, i) => facce.push({ cls: sup ? 'lb-sup' : 'carta', html: sopra(st) + pagina(bl, !sup && pagine.length > 1 ? i + 1 : 0) }));
    if (!facce.length) facce.push({ cls: sup ? 'lb-sup' : 'carta', html: sopra(st) });
  } else {
    const contenuto = [{ cls: 'carta', html: frontespizio(lk) }];
    pagine.forEach((bl, i) => contenuto.push({ cls: 'carta', html: pagina(bl, i + 1) }));
    if (contenuto.length % 2) contenuto.push({ cls: 'carta vuota', html: '' });
    facce.push({ cls: 'copertina', html: copertina(lk, st) }, { cls: 'risguardo', html: '' }, ...contenuto,
               { cls: 'risguardo', html: '' }, { cls: 'copertina quarta', html: copertina(lk, st, true) });
  }

  // ---- libro: un foglio = due facce (fronte a destra, retro a sinistra quando e' girato)
  const fogli = [];
  if (!unaFaccia) {
    for (let i = 0; i < facce.length; i += 2) {
      const f = h('div', 'lb-foglio');
      const a = facce[i], b = facce[i + 1];
      f.append(h('div', 'lb-faccia fronte ' + a.cls, a.html), h('div', 'lb-faccia retro ' + b.cls, b.html));
      f.append(h('i', 'lb-ombra'));
      libro.append(f);
      fogli.push(f);
    }
  } else {
    // lettera: una busta col sigillo; rotto il sigillo, il foglio si apre. Senza busta il foglio c'e' subito.
    if (busta) {
      libro.append(h('div', 'lb-busta', `<div class="lb-sigillo">${stemmaDi(lk, 'cera', '', lk.stemma || 'rosa')}</div>
        <div class="lb-busta-titolo">${esc(lk.titolo)}</div>`));
    }
    facce.forEach((fc, i) => { const f = h('div', clsFaccia.replace(' carta', '') + ' ' + fc.cls, fc.html); f.dataset.i = i; libro.append(f); fogli.push(f); });
  }

  let aperti = busta ? -1 : 0;   // fogli girati (libro) / faccia mostrata (foglio e superficie; -1 = busta chiusa)
  // il sigillo e' di quella lettera nel mondo: se qualcuno l'ha gia' rotto, lo si trova rotto
  let giaRotto = busta && !!(palco.dati && palco.dati.sigillo_rotto);
  if (giaRotto) libro.classList.add('rotto-prima');
  const n = fogli.length;
  const info = h('span', 'lb-info');
  const bPrima = h('button', 'lb-freccia', '‹'), bDopo = h('button', 'lb-freccia', '›'), bChiudi = h('button', 'lb-chiudi', '✕');
  bPrima.title = 'Pagina prima (←)'; bDopo.title = 'Pagina dopo (→)'; bChiudi.title = 'Chiudi (Esc)';
  barra.append(bPrima, info, bDopo, bChiudi);
  const minimo = busta ? -1 : 0;

  function disponi() {
    if (unaFaccia) {
      libro.classList.toggle('aperta', aperti >= 0);
      fogli.forEach((f, i) => { f.classList.toggle('su', i === aperti); f.classList.toggle('via', i < aperti); });
      info.textContent = aperti < 0 ? (giaRotto ? 'Il sigillo è già rotto · apri' : 'Rompi il sigillo')
        : n > 1 ? `${sup ? 'Faccia' : 'Foglio'} ${aperti + 1} di ${n}` : (sup ? lk.titolo || '' : '');
      bPrima.disabled = aperti <= minimo; bDopo.disabled = aperti >= n - 1;
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
    if (unaFaccia) {
      const v = Math.max(minimo, Math.min(n - 1, aperti + dir));
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
    if (busta && aperti < 0) return gira(1);
    const r = libro.getBoundingClientRect();
    gira(e.clientX > r.left + r.width / 2 ? 1 : -1);
  });
  const tasti = e => {
    if (['ArrowRight', 'PageDown', ' ', 'd', 'D'].includes(e.key)) { e.preventDefault(); gira(1); }
    else if (['ArrowLeft', 'PageUp', 'a', 'A'].includes(e.key)) { e.preventDefault(); gira(-1); }
    else if (e.key === 'Home') { while (aperti > Math.max(0, minimo)) gira(-1); }
  };
  let rotella = 0;
  const ruota = e => { const t = Date.now(); if (t - rotella < 350) return; rotella = t; gira(e.deltaY > 0 ? 1 : -1); };
  document.addEventListener('keydown', tasti);
  window.addEventListener('wheel', ruota, { passive: true });
  palco.allaFine(() => {
    document.removeEventListener('keydown', tasti);
    window.removeEventListener('wheel', ruota);
    document.body.classList.remove(...classi);
  });

  // in prova (anteprima del redattore del Cartografo) si riapre alla pagina di prima, senza animazioni
  const chiave = palco.prova ? 'lb_aperti:' + (lk.titolo || '') : '';
  let ricorda = null;
  try { ricorda = chiave ? sessionStorage.getItem(chiave) : null; } catch (e) { /* niente */ }
  if (ricorda !== null) {
    const v = +ricorda;
    libro.classList.add('subito');
    if (unaFaccia) { aperti = Math.max(minimo, Math.min(n - 1, v)); if (busta && aperti >= 0) libro.classList.add('rotto'); }
    else aperti = Math.max(0, Math.min(n, v));
    disponi();
    tavolo.classList.add('dentro', 'subito');
    requestAnimationFrame(() => requestAnimationFrame(() => { libro.classList.remove('subito'); tavolo.classList.remove('subito'); }));
  } else {
    disponi();
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

// per il redattore dei libri del Cartografo (stessi stemmi, colori, tipi e scelte)
export { STEMMI, COLORI, CERE, COLORE_TIPO, TIPI, PRESET, OPZIONI, SUPERFICI, stile };
