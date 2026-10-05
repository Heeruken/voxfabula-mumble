// Il dado del palco. Il risultato lo decide il SERVER (tiro gia' fatto nello script); qui si
// anima soltanto. Stesso seme e stessi tempi = la stessa animazione su ogni schermo.

function mulberry32(a) {
  return () => {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

const PASSI = 14;           // facce mostrate prima di fermarsi
const DURATA = 1500;        // ms del rotolare
const POSA = 1700;          // ms in cui il risultato resta in mezzo allo schermo

let coda = Promise.resolve();

// m = {chi, abilita, d20, mod, tot, cd, ok, seme}. Torna quando il dado e' sparito.
// dove() = {x, y} in pixel (accanto a un pallino) o null = in mezzo; chiesto quando tocca a lui.
export function tira(el, m, dove = null) {
  coda = coda.then(() => rotola(el, m, dove ? dove() : null));
  return coda;
}

// Quando i dadi in corso (e in coda) hanno finito: quello che lo script manda DOPO un tiro
// (es. il segreto scoperto) si mostra dopo il dado, non sopra.
export function dopoDadi() {
  return coda;
}

function rotola(el, m, pos) {
  return new Promise(fine => {
    const caso = mulberry32(Number(m.seme) || 1);
    el.innerHTML = '';
    el.classList.toggle('vicino', !!pos);
    if (pos) {
      // accanto al pallino, dentro lo schermo
      const x = Math.min(window.innerWidth - 110, Math.max(110, pos.x - 110));
      const y = Math.min(window.innerHeight - 110, Math.max(110, pos.y));
      el.style.left = x + 'px';
      el.style.top = y + 'px';
    } else {
      el.style.left = el.style.top = '';
    }
    const chi = document.createElement('div');
    chi.className = 'chi';
    chi.textContent = `${m.chi || ''} - ${m.abilita || ''}`;
    const faccia = document.createElement('div');
    faccia.className = 'faccia';
    const conto = document.createElement('div');
    conto.className = 'conto';
    el.append(chi, faccia, conto);
    el.hidden = false;

    // tempi che rallentano: somma dei pesi = DURATA
    const pesi = Array.from({ length: PASSI }, (_, i) => 1 + (i / PASSI) ** 2 * 4);
    const tot = pesi.reduce((a, b) => a + b, 0);
    let t = 0;
    pesi.forEach((p, i) => {
      t += p / tot * DURATA;
      const ultimo = i === PASSI - 1;
      const n = ultimo ? m.d20 : 1 + Math.floor(caso() * 20);
      setTimeout(() => {
        faccia.textContent = n;
        faccia.classList.toggle('gira', !ultimo && i % 2 === 0);
        if (ultimo) {
          faccia.classList.add(n === 20 ? 'venti' : n === 1 ? 'uno' : 'fermo');
          const segno = m.mod >= 0 ? '+' : '-';
          conto.textContent = `${m.d20} ${segno} ${Math.abs(m.mod)} = ${m.tot}` +
            (m.cd ? `  (CD ${m.cd}) ${m.ok ? 'riuscito' : 'fallito'}` : '');
          conto.classList.add('su');
          setTimeout(() => { el.hidden = true; fine(); }, POSA);
        }
      }, t);
    });
  });
}
