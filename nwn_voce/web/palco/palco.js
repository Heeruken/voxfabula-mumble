// Il ponte del palco: parla col Companion (window.pywebview.api) e carica la scena giusta.
//
// Una scena e' un modulo scene/<tipo>.js con  export default function (palco) {...}
// palco = { sid, tipo, dati,            // cosa ha mandato lo script del server
//           oggetto(),                  // il modello (Promise di oggetto JSON, o null)
//           invia(ev, dati),            // al server (lo script decide)
//           su(fn),                     // fn(msg) per ogni messaggio dello script (o "vista" di un altro)
//           chiudi(esito),
//           allaFine(fn),               // fn() quando la scena finisce (via WebGL, timer, ...)
//           finita,                     // true dopo la fine: chi aspettava qualcosa si ferma
//           azioni(lista), diario(testo, classe, piccolo), tiro(msg) }   // pezzi comuni
// Tutto quello che conta (tiri, segreti) arriva dal server: la pagina mostra e riferisce.
//
// La pagina NON si ricarica tra una scena e l'altra (con pywebview 6 ricaricare = la finestra
// ricompare da sola): finita una scena si ripulisce e torna ad aspettare la prossima.
// I messaggi dello script arrivati mentre la scena si prepara (modello, texture) aspettano e
// passano appena e' pronta; poi la pagina manda "pronta" allo script.
//
// Aperta in un browser normale (senza Companion) parte in PROVA: dati finti e tiri locali,
// solo per lavorare alla grafica.

import { tira, dopoDadi, ferma } from './dado.js';

const $ = id => document.getElementById(id);
let ascoltatori = [];
let chiusa = false;
let scenaPronta = false;      // la scena ha registrato i suoi ascoltatori
let inAttesa = [];            // messaggi arrivati prima
let giro = 0;                 // quale scena (una vecchia che finisce di caricare se ne accorge)

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
  let data = false;
  return {
    async inizio() {
      if (data) return new Promise(() => {});      // chiusa: in prova non ne arrivano altre
      data = true;
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
    async chiudi(esito) { console.log('[prova] chiudi', esito); },
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
function smista(msg) {
  if (msg.tipo === 'vista') return consegna(msg);
  filo = filo.then(() => (msg.tipo === 'tiro' ? consegna(msg) : dopoDadi().then(() => consegna(msg))));
}
window.palcoRicevi = msg => {
  if (!msg || typeof msg !== 'object' || chiusa) return;
  if (!scenaPronta) inAttesa.push(msg);
  else smista(msg);
};

let palcoAttivo = null;

// via tutto quello che la scena ha messo in pagina; poi si aspetta la prossima
function ripulisci() {
  giro++;
  if (palcoAttivo) {
    palcoAttivo.finita = true;
    for (const fn of palcoAttivo._allaFine.splice(0)) {
      try { fn(); } catch (e) { console.error(e); }
    }
  }
  palcoAttivo = null;
  ascoltatori = [];
  inAttesa = [];
  scenaPronta = false;
  filo = Promise.resolve();
  ferma($('dado'));
  document.body.classList.remove('in-scena', 'oscura');
  $('titolo').textContent = '';
  $('scena').innerHTML = '';
  $('azioni').innerHTML = '';
  $('diario').innerHTML = '';
  $('carica').hidden = true;
}

const USCITA = 350;           // ms di dissolvenza (come USCITA in palco.py e palco.css)
let finendo = null;
function fine() {
  if (finendo) return finendo;
  // la scena sfuma (il gioco e' gia' tornato attivo sotto), poi via tutto e di nuovo in
  // attesa (inizio() manda "pronto" all'app)
  document.body.classList.remove('in-scena');
  finendo = new Promise(r => setTimeout(r, USCITA)).then(() => {
    ripulisci();
    finendo = null; chiusa = false; avvia();
  });
  return finendo;
}

async function chiudi(esito = 'chiusa') {
  if (chiusa) return;
  chiusa = true;
  try { await api().chiudi(esito); } catch (e) { console.error(e); }
  fine();
}

// chiusa dall'app (lo script, la rete, l'app che si chiude): l'esito l'ha gia' mandato lei.
// Puo' arrivare anche prima che la pagina abbia cominciato la scena: allora la salta.
const chiuseDallApp = new Set();
window.palcoFine = sid => {
  chiuseDallApp.add(sid);
  if (!palcoAttivo || palcoAttivo.sid !== sid || (chiusa && finendo)) return;
  chiusa = true;
  fine();
};

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

async function tiro(m) {
  const di = palcoAttivo;
  await tira($('dado'), m, () => (palcoAttivo && palcoAttivo.posizioneDado ? palcoAttivo.posizioneDado(m) : null));
  if (!di || di.finita) return;              // la scena e' finita mentre il dado rotolava
  diario(`${m.chi}: ${m.abilita} ${m.d20} ${m.mod >= 0 ? '+' : '-'} ${Math.abs(m.mod)} = ${m.tot}` +
         (m.cd ? ` contro ${m.cd}` : ''), m.ok === undefined ? '' : m.ok ? 'ok' : 'ko',
         m.ok === undefined ? '' : m.ok ? 'riuscito' : 'fallito');
}

let aspetto = false;
async function avvia() {
  if (aspetto) return;                       // c'e' gia' chi aspetta la prossima scena
  aspetto = true;
  let ini;
  try { ini = await api().inizio(); } finally { aspetto = false; }   // nell'overlay: nascosto, in attesa
  if (!ini) return;                          // l'overlay si sta chiudendo
  if (chiuseDallApp.has(ini.sid)) return avvia();   // chiusa prima ancora di cominciare
  const mio = ++giro;
  chiusa = false;
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
    finita: false,
    _allaFine: [],
    allaFine(fn) {              // se e' gia' finita (caricava ancora): subito
      if (palco.finita) { try { fn(); } catch (e) { console.error(e); } } else palco._allaFine.push(fn);
    },
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
  palco.allaFine(() => clearTimeout(tCarica));
  let ok = false;
  try {
    const mod = await import('./scene/' + ini.tipo + '.js');
    await mod.default(palco);
    ok = true;
  } catch (e) {
    console.error(e);
    if (mio === giro) diario('Questa scena non si apre: ' + e.message, 'ko');
  }
  if (mio !== giro) return;                  // finita mentre si preparava
  clearTimeout(tCarica);
  carica.hidden = true;
  // la scena ascolta: passano i messaggi arrivati nel frattempo, in ordine
  scenaPronta = true;
  for (const m of inAttesa.splice(0)) smista(m);
  if (ok) palco.invia('pronta', {});
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
