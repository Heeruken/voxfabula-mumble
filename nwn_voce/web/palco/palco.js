// Il ponte del palco: parla col Companion (window.pywebview.api) e carica la scena giusta.
//
// Una scena e' un modulo scene/<tipo>.js con  export default function (palco) {...}
// palco = { sid, tipo, dati,            // cosa ha mandato lo script del server
//           oggetto(),                  // il modello (Promise di oggetto JSON, o null)
//           invia(ev, dati),            // al server (lo script decide)
//           su(fn),                     // fn(msg) per ogni messaggio dello script (o "vista" di un altro)
//           chiudi(esito),
//           azioni(lista), diario(testo, classe, piccolo), tiro(msg) }   // pezzi comuni
// Tutto quello che conta (tiri, segreti) arriva dal server: la pagina mostra e riferisce.
//
// Aperta in un browser normale (senza Companion) parte in PROVA: dati finti e tiri locali,
// solo per lavorare alla grafica.

import { tira, dopoDadi } from './dado.js';

const $ = id => document.getElementById(id);
const ascoltatori = [];
let chiusa = false;

function apiProva() {
  const q = new URLSearchParams(location.search);
  const tipo = q.get('tipo') || 'esamina';
  // scena=<json>: la scena preparata nell'editor del Cartografo (con CD e segreti: qui si tira in locale)
  let scena = null;
  try { scena = q.get('scena') ? JSON.parse(q.get('scena')) : null; } catch (e) { scena = null; }
  const dati = scena || {
    titolo: 'Coppa annerita (prova)', oggetto: q.get('oggetto') || '',
    testo: 'Una coppa di metallo scuro, fredda al tatto.',
    azioni: [{ id: 'indaga', etichetta: 'Indaga (Cercare)' }, { id: 'ricorda', etichetta: 'Ricorda (Sapienza)' }],
    punti: [{ id: 'cima', etichetta: 'La sommità consumata', lato: 'alto', cd: 13, segreto: 'Consumata dalle dita.' },
            { id: 'base', etichetta: 'Un graffio sulla base', lato: 'fronte', cd: 13, segreto: 'Il graffio forma una M.' },
            { id: 'fianco', etichetta: 'Il fianco annerito', lato: 'destra', cd: 13, segreto: 'Fuoco di drago.' }],
  };
  if (!dati.oggetto) dati.oggetto = q.get('oggetto') || '';
  const mod = Number(q.get('mod') || 4);           // il modificatore del personaggio di prova
  const tiraProva = (abilita, cd, extra) => {
    const d20 = 1 + Math.floor(Math.random() * 20), ok = !cd || d20 + mod >= cd;
    window.palcoRicevi(Object.assign({ tipo: 'tiro', chi: 'Tu (prova)', abilita, d20, mod, tot: d20 + mod,
      cd: cd || undefined, ok: cd ? ok : undefined, seme: Math.floor(Math.random() * 1e9) }, extra));
    return ok;
  };
  const fatti = new Set();
  return {
    async inizio() {
      // alla pagina non vanno CD e segreti, come in gioco
      const pagina = Object.assign({}, dati, { punti: (dati.punti || []).map(p => ({ id: p.id, etichetta: p.etichetta, lato: p.lato, pos: p.pos })) });
      return { sid: 'prova', tipo, dati: pagina };
    },
    async oggetto() {
      const f = q.get('modello') || q.get('oggetto');
      if (!f) return '';
      try { return await (await fetch('prova/' + f + '.json')).text(); } catch (e) { return ''; }
    },
    async invia(ev, d) {
      console.log('[prova] invia', ev, d);
      if (ev === 'punto') {
        const p = (dati.punti || []).find(x => x.id === d.id);
        if (!p || fatti.has(p.id)) return true;
        fatti.add(p.id);
        const ok = tiraProva(p.abilitaNome || 'Cercare', p.cd || 15, { punto: p.id });
        window.palcoRicevi(ok && p.segreto ? { tipo: 'rivela', id: p.id, testo: `${p.etichetta}. ${p.segreto}` }
          : { tipo: 'testo', testo: `${p.etichetta}: niente di strano.` });
      }
      if (ev === 'azione' && !fatti.has('_indaga')) {
        fatti.add('_indaga');
        const ok = tiraProva(dati.abilitaNome || 'Cercare', dati.cd || 15, {});
        window.palcoRicevi(ok && dati.segreto ? { tipo: 'rivela', id: '_s', testo: dati.segreto }
          : { tipo: 'testo', testo: 'Niente di strano.' });
      }
      return true;
    },
    async chiudi(esito) { console.log('[prova] chiudi', esito); document.body.innerHTML = ''; },
  };
}

const PROVA = new URLSearchParams(location.search).has('prova');

function api() {
  return PROVA ? (window._apiProva ||= apiProva()) : window.pywebview.api;
}

function consegna(msg) {
  for (const fn of ascoltatori) {
    try { fn(msg); } catch (e) { console.error(e); }
  }
}

// In ordine d'arrivo; tutto quello che segue un tiro aspetta che il dado si fermi (stessa
// sequenza su ogni schermo). La "vista" (l'oggetto girato da un altro) passa subito.
let filo = Promise.resolve();
window.palcoRicevi = msg => {
  if (!msg || typeof msg !== 'object') return;
  if (msg.tipo === 'vista') return consegna(msg);
  filo = filo.then(() => (msg.tipo === 'tiro' ? consegna(msg) : dopoDadi().then(() => consegna(msg))));
};

function chiudi(esito = 'chiusa') {
  if (chiusa) return;
  chiusa = true;
  api().chiudi(esito);
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); chiudi('esc'); } });
document.addEventListener('contextmenu', e => e.preventDefault());
document.addEventListener('dragstart', e => e.preventDefault());

// ---- pezzi comuni a tutte le scene
function azioni(lista) {
  const box = $('azioni');
  box.innerHTML = '';
  for (const a of lista || []) {
    const b = document.createElement('button');
    b.textContent = a.etichetta || a.id;
    b.disabled = !!a.spenta;
    b.addEventListener('click', () => api().invia('azione', { id: a.id }));
    box.appendChild(b);
  }
}

function diario(testo, classe = '', piccolo = '') {
  const v = document.createElement('div');
  v.className = 'voce ' + classe;
  v.textContent = testo;
  if (piccolo) {
    const s = document.createElement('small');
    s.textContent = piccolo;
    v.appendChild(s);
  }
  $('diario').appendChild(v);
  v.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

let palcoAttivo = null;

async function tiro(m) {
  await tira($('dado'), m, () => (palcoAttivo && palcoAttivo.posizioneDado ? palcoAttivo.posizioneDado(m) : null));
  diario(`${m.chi}: ${m.abilita} ${m.d20} ${m.mod >= 0 ? '+' : '-'} ${Math.abs(m.mod)} = ${m.tot}` +
         (m.cd ? ` contro ${m.cd}` : ''), m.ok === undefined ? '' : m.ok ? 'ok' : 'ko',
         m.ok === undefined ? '' : m.ok ? 'riuscito' : 'fallito');
}

async function avvia() {
  const ini = await api().inizio();          // nell'overlay aspetta (nascosto) che arrivi una scena
  if (!ini) return;                          // l'overlay si sta chiudendo
  const dati = ini.dati || {};
  document.body.classList.toggle('oscura', !!dati.oscura);
  document.body.classList.add('in-scena');
  $('titolo').textContent = dati.titolo || '';
  if (dati.testo) diario(dati.testo);
  azioni(dati.azioni);
  const palco = {
    sid: ini.sid, tipo: ini.tipo, dati,
    async oggetto() {
      const s = await api().oggetto();
      return s ? JSON.parse(s) : null;
    },
    invia: (ev, d) => api().invia(ev, d || {}),
    su: fn => ascoltatori.push(fn),
    chiudi, azioni, diario, tiro,
    prova: PROVA,
    posizioneDado: null,        // la scena puo' dire dove far rotolare un tiro (es. accanto a un pallino)
  };
  palcoAttivo = palco;
  // messaggi comuni: le scene ascoltano gli stessi e aggiungono i loro
  palco.su(m => {
    if (m.tipo === 'testo') diario(m.testo, m.classe || '');
    else if (m.tipo === 'tiro') tiro(m);
    else if (m.tipo === 'azioni') azioni(m.azioni);
    else if (m.tipo === 'titolo') $('titolo').textContent = m.titolo || '';
  });
  const carica = $('carica');
  const tCarica = setTimeout(() => { carica.hidden = false; }, 250);   // solo se ci mette un po'
  try {
    const mod = await import('./scene/' + ini.tipo + '.js');
    await mod.default(palco);
    clearTimeout(tCarica);
    carica.hidden = true;
  } catch (e) {
    clearTimeout(tCarica);
    carica.hidden = true;
    console.error(e);
    diario('Questa scena non si apre: ' + e.message, 'ko');
  }
}

// la scena piu' usata si carica SUBITO (three.js compreso), mentre l'overlay aspetta nascosto:
// quando arriva la scena e' gia' tutto in memoria
import('./scene/esamina.js').catch(() => {});

// PROVA solo se chiesta nell'indirizzo (index.html?prova&oggetto=...): mai per sbaglio nel Companion
let avviato = false;
function parti() { if (!avviato) { avviato = true; avvia(); } }
if (new URLSearchParams(location.search).has('prova')) parti();
else if (window.pywebview && window.pywebview.api && window.pywebview.api.inizio) parti();
else window.addEventListener('pywebviewready', parti, { once: true });
