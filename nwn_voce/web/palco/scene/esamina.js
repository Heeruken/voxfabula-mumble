// Scena "esamina": un oggetto in 3D da girare e guardare da vicino.
// dati dallo script: {titolo, testo, oggetto: "<nome pacchetto>", azioni: [{id, etichetta}]}
// Dallo script arrivano: tiro (dado), testo, rivela {id, pos: [x, y, z] in coordinate NWN del
// modello, testo}, azioni. Il relay gira la "vista" di chi tocca l'oggetto agli altri: chi
// guarda insieme vede l'oggetto girare come lo gira l'altro (finche' non lo tocca lui).

import * as THREE from '../vendor/three.module.js';
import { dopoDadi } from '../dado.js';

const VISTA_OGNI = 100;        // ms tra due "vista" mandate mentre si gira
const MIA_PER = 1200;          // ms dopo l'ultimo tocco in cui la vista degli altri non ci sposta

function b64f32(s) {
  const bin = atob(s);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Float32Array(u8.buffer);
}

function caricaTex(src) {
  return new Promise(res => {
    if (!src) return res(null);
    new THREE.TextureLoader().load(src, t => {
      t.colorSpace = THREE.SRGBColorSpace;
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      t.anisotropy = 4;
      res(t);
    }, undefined, () => res(null));
  });
}

// il modello (formato del Cartografo: mesh a triangoli, posizioni/uv in base64) -> gruppo three.js
async function costruisci(d) {
  const g = new THREE.Group();
  const tex = d.tex || {};
  const mappe = await Promise.all(d.m.map(m => caricaTex(tex[m.t])));
  d.m.forEach((m, i) => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(b64f32(m.p), 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(b64f32(m.uv), 2));
    geo.computeVertexNormals();
    const map = mappe[i];
    const mat = new THREE.MeshStandardMaterial({
      map, color: map ? 0xffffff : 0x8a8f99, roughness: .58, metalness: .12, envMapIntensity: .9,
      side: THREE.DoubleSide, alphaTest: .35, transparent: true, opacity: 0 });   // entra in dissolvenza
    g.add(new THREE.Mesh(geo, mat));
  });
  return g;
}

// Luce d'ambiente per i materiali: una stanza scura con una finestra calda e una fredda, filtrata
// (PMREM) in una mappa di riflessi. Da' volume a metalli, pietra e legno senza file in piu'.
function ambiente(renderer) {
  const stanza = new THREE.Scene();
  stanza.background = new THREE.Color(0x0d0a1c);
  const pannello = (colore, forza, pos, scala) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(colore).multiplyScalar(forza), side: THREE.DoubleSide }));
    m.position.set(...pos); m.scale.set(...scala); m.lookAt(0, 0, 0);
    stanza.add(m);
  };
  pannello(0xffe2b0, 6, [4, 5, 4], [5, 3, 1]);      // luce calda, in alto davanti
  pannello(0x8a7cff, 3, [-6, 1, -3], [4, 6, 1]);    // contorno freddo, dietro
  pannello(0xfff6e8, 1.2, [0, -6, 0], [8, 8, 1]);   // un po' di luce da sotto
  const pm = new THREE.PMREMGenerator(renderer);
  const env = pm.fromScene(stanza, 0.04).texture;
  pm.dispose();
  return env;
}

// alone dietro l'oggetto: un disco di luce morbida, sempre di fronte alla camera
function alone(raggio) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
  g.addColorStop(0, 'rgba(160,130,255,0.55)');
  g.addColorStop(0.35, 'rgba(110,80,200,0.22)');
  g.addColorStop(1, 'rgba(60,40,120,0)');
  x.fillStyle = g;
  x.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthWrite: false, transparent: true }));
  s.scale.setScalar(raggio * 4.2);
  s.position.set(0, 0, -raggio * 1.2);
  s.renderOrder = -1;
  return s;
}

// pulviscolo dorato che fluttua piano attorno all'oggetto
function pulviscolo(raggio, n = 140) {
  const pos = new Float32Array(n * 3), fase = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    pos[i * 3] = (Math.random() - .5) * raggio * 6;
    pos[i * 3 + 1] = (Math.random() - .5) * raggio * 4;
    pos[i * 3 + 2] = (Math.random() - .5) * raggio * 4;
    fase[i] = Math.random() * Math.PI * 2;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const p = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xf2d98a, size: raggio * .018,
    transparent: true, opacity: .55, depthWrite: false, blending: THREE.AdditiveBlending }));
  p.userData = { fase, base: pos.slice(), raggio };
  return p;
}

function muoviPulviscolo(p, t) {
  const { fase, base, raggio } = p.userData;
  const a = p.geometry.attributes.position.array;
  for (let i = 0; i < fase.length; i++) {
    a[i * 3] = base[i * 3] + Math.sin(t * .23 + fase[i]) * raggio * .08;
    a[i * 3 + 1] = base[i * 3 + 1] + ((t * raggio * .03 + fase[i]) % (raggio * 4)) - raggio * 2;
    a[i * 3 + 2] = base[i * 3 + 2] + Math.cos(t * .19 + fase[i]) * raggio * .08;
  }
  p.geometry.attributes.position.needsUpdate = true;
}

// geometrie, materiali e texture di un oggetto three.js (WebGL non li libera da solo)
function libera(obj) {
  obj.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    for (const m of [].concat(o.material || [])) { if (m.map) m.map.dispose(); m.dispose(); }
  });
}

function segnaposto() {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.IcosahedronGeometry(.5, 0),
    new THREE.MeshStandardMaterial({ color: 0x8f74e8, roughness: .4, flatShading: true })));
  return { g, lo: [-.5, -.5, -.5], hi: [.5, .5, .5] };
}

export default async function (palco) {
  const box = document.getElementById('scena');
  const tela = document.createElement('canvas');
  box.appendChild(tela);
  const chi = document.createElement('div');
  chi.id = 'chi';
  document.body.appendChild(chi);

  // fine della scena: la pagina non si ricarica, quindi si libera tutto (WebGL compreso: i
  // contesti sono pochi e una scena dopo l'altra finirebbero)
  let raf = 0, chiTimer = 0, renderer = null, scene = null;
  palco.allaFine(() => {
    cancelAnimationFrame(raf);
    clearTimeout(chiTimer);
    chi.remove();
    if (scene) {
      libera(scene);
      if (scene.environment) scene.environment.dispose();
    }
    if (renderer) { renderer.dispose(); renderer.forceContextLoss(); }
  });

  renderer = new THREE.WebGLRenderer({ canvas: tela, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  scene = new THREE.Scene();
  scene.environment = ambiente(renderer);
  scene.add(new THREE.HemisphereLight(0xfff4e0, 0x1c1638, .55));
  const sole = new THREE.DirectionalLight(0xffe9c8, 2.4);      // luce principale, calda
  sole.position.set(3, 5, 4);
  scene.add(sole);
  const contorno = new THREE.DirectionalLight(0x9d8cff, 1.3);  // luce di contorno, dietro: stacca la sagoma
  contorno.position.set(-3, 2, -5);
  scene.add(contorno);
  const camera = new THREE.PerspectiveCamera(35, 1, .01, 100);

  // perno (la rotazione condivisa) -> modello (NWN ha z in alto: ruotato per three.js, centrato)
  const perno = new THREE.Group();
  scene.add(perno);
  let dati = null;
  try { dati = await palco.oggetto(); } catch (e) { console.error(e); }
  if (palco.finita) return;                     // chiusa mentre arrivava il modello
  let modello, lo, hi;
  if (dati && dati.m && dati.m.length) {
    modello = await costruisci(dati);
    if (palco.finita) return libera(modello);   // chiusa mentre si caricavano le texture
    lo = dati.lo; hi = dati.hi;
  } else {
    ({ g: modello, lo, hi } = segnaposto());
    if (palco.dati.oggetto) palco.diario("Il modello dell'oggetto non e' arrivato.", 'ko');
  }
  const centro = new THREE.Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
  const raggio = Math.max(.05, Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2);
  const dentro = new THREE.Group();          // coordinate NWN del modello, centrate
  dentro.rotation.x = -Math.PI / 2;
  modello.position.set(-centro.x, -centro.y, -centro.z);
  dentro.add(modello);
  perno.add(dentro);
  perno.quaternion.setFromEuler(new THREE.Euler(.35, -.6, 0));    // di tre quarti
  scene.add(alone(raggio));
  const polvere = pulviscolo(raggio);
  scene.add(polvere);
  // entrata: l'oggetto arriva in dissolvenza, un po' piu' piccolo e girato, e si posa
  const materiali = [];
  modello.traverse(o => { if (o.material) materiali.push(o.material); });
  const entra = { t0: performance.now(), dur: 900, giro: .55 };
  const finale = perno.quaternion.clone();
  perno.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -entra.giro));

  let dist = raggio * 3.2;
  const distMin = raggio * 1.2, distMax = raggio * 7;


  // ---- girare e avvicinare (un clic senza trascinare non gira: puo' essere su un pallino)
  let presa = null, mosso = 0, ultimoTocco = -1e9, ultimaVista = 0;
  const bersaglio = new THREE.Quaternion().copy(finale);      // l'entrata gira verso la posa giusta
  let distBersaglio = dist;

  function mandaVista(subito = false) {
    const ora = performance.now();
    if (!subito && ora - ultimaVista < VISTA_OGNI) return;
    ultimaVista = ora;
    const q = perno.quaternion;
    palco.invia('vista', { q: [q.x, q.y, q.z, q.w].map(v => +v.toFixed(4)), z: +(dist / raggio).toFixed(3) });
  }

  tela.addEventListener('pointerdown', e => {
    presa = { x: e.clientX, y: e.clientY };
    mosso = 0;
    tela.setPointerCapture(e.pointerId);
    tela.classList.add('presa');
  });
  tela.addEventListener('pointermove', e => {
    if (!presa) return;
    const dx = e.clientX - presa.x, dy = e.clientY - presa.y;
    presa = { x: e.clientX, y: e.clientY };
    mosso += Math.abs(dx) + Math.abs(dy);
    const k = 2 * Math.PI / Math.max(300, tela.clientHeight);
    const qy = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), dx * k);
    const qx = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), dy * k);
    perno.quaternion.premultiply(qy).premultiply(qx).normalize();
    bersaglio.copy(perno.quaternion);
    ultimoTocco = performance.now();
    mandaVista();
  });
  const lascia = e => {
    if (!presa) return;
    presa = null;
    tela.classList.remove('presa');
    if (mosso > 4) mandaVista(true);
    else if (palco.prova && e && e.shiftKey) dove(e);      // PROVA: Maiusc+clic = coordinate del punto
  };
  tela.addEventListener('pointerup', lascia);
  tela.addEventListener('pointercancel', () => lascia(null));
  tela.addEventListener('wheel', e => {
    e.preventDefault();
    dist = distBersaglio = Math.min(distMax, Math.max(distMin, dist * Math.exp(e.deltaY * .0012)));
    ultimoTocco = performance.now();
    mandaVista();
  }, { passive: false });

  const raggi = new THREE.Raycaster();

  // PROVA: dove ho cliccato, in coordinate del modello (da mettere nello script come "x y z")
  function dove(e) {
    const r = tela.getBoundingClientRect();
    raggi.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1,
      -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = raggi.intersectObject(modello, true)[0];
    if (!hit) return;
    const p = dentro.worldToLocal(hit.point.clone()).add(centro);
    palco.diario(`Punto: ${p.x.toFixed(2)} ${p.y.toFixed(2)} ${p.z.toFixed(2)}`, '', 'coordinate del modello');
  }

  // ---- i pallini: punti d'interesse sul modello, cliccabili (lo script tira e decide)
  // dati.punti = [{id, etichetta, lato: "alto|basso|fronte|retro|destra|sinistra"} o {.., pos: [x, y, z]}]
  const LATI = { alto: [0, 0, 1], basso: [0, 0, -1], fronte: [0, -1, 0], retro: [0, 1, 0],
                 destra: [1, 0, 0], sinistra: [-1, 0, 0] };
  const strato = document.createElement('div');
  strato.id = 'pallini';
  box.appendChild(strato);
  const punti = new Map();          // id -> {p: Vector3 (in "dentro"), el, stato}

  // il punto della superficie su quel lato: un raggio da fuori verso il centro
  function sulLato(lato) {
    const d = LATI[lato] || LATI.alto;
    perno.updateMatrixWorld(true);
    const fuori = new THREE.Vector3(...d).multiplyScalar(raggio * 3);
    const da = dentro.localToWorld(fuori.clone());
    const verso = dentro.localToWorld(new THREE.Vector3(0, 0, 0)).sub(da).normalize();
    raggi.set(da, verso);
    const hit = raggi.intersectObject(modello, true)[0];
    if (hit) return dentro.worldToLocal(hit.point.clone());
    return new THREE.Vector3(...d).multiplyScalar(raggio * .9);
  }

  function aggiungiPunto(pt) {
    if (!pt || !pt.id || punti.has(pt.id)) return;
    const p = Array.isArray(pt.pos) ? new THREE.Vector3(pt.pos[0], pt.pos[1], pt.pos[2]).sub(centro)
      : sulLato(pt.lato);
    const el = document.createElement('button');
    el.className = 'pallino';
    el.innerHTML = '<i></i><span></span>';
    el.querySelector('span').textContent = pt.etichetta || '';
    el.addEventListener('click', () => {
      if (el.classList.contains('ok') || el.classList.contains('ko') || el.classList.contains('attesa')) return;
      el.classList.add('attesa');
      palco.invia('punto', { id: pt.id });
    });
    strato.appendChild(el);
    punti.set(pt.id, { p, el });
  }
  (palco.dati.punti || []).forEach(aggiungiPunto);

  function statoPunto(id, stato, etichetta) {
    const pt = punti.get(id);
    if (!pt) return;
    pt.el.classList.remove('attesa', 'ok', 'ko', 'spento');
    if (stato) pt.el.classList.add(stato);
    if (etichetta) pt.el.querySelector('span').textContent = etichetta;
  }

  // dove far rotolare il dado: accanto al pallino del tiro (in pixel della finestra)
  const v = new THREE.Vector3();
  palco.posizioneDado = m => {
    const pt = m && punti.get(m.punto);
    if (!pt) return null;
    v.copy(pt.p);
    dentro.localToWorld(v).project(camera);
    const r = tela.getBoundingClientRect();
    return { x: r.left + (v.x + 1) / 2 * r.width, y: r.top + (1 - v.y) / 2 * r.height };
  };

  // ---- segni scoperti senza pallino (rivela con pos)
  const segni = new Map();
  function segna(id, pos) {
    if (segni.has(id) || punti.has(id) || !Array.isArray(pos)) return;
    const s = new THREE.Mesh(new THREE.SphereGeometry(raggio * .045, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xf2cf5b, transparent: true, opacity: .9, depthTest: false }));
    s.renderOrder = 10;
    s.position.set(pos[0] - centro.x, pos[1] - centro.y, pos[2] - centro.z);
    dentro.add(s);
    segni.set(id, s);
  }

  palco.su(m => {
    if (m.tipo === 'vista' && m.v && Array.isArray(m.v.q)) {
      if (performance.now() - ultimoTocco < MIA_PER) return;      // lo sto girando io
      bersaglio.set(...m.v.q).normalize();
      if (m.v.z) distBersaglio = Math.min(distMax, Math.max(distMin, m.v.z * raggio));
      chi.textContent = `${m.da} sta girando l'oggetto`;
      clearTimeout(chiTimer);
      chiTimer = setTimeout(() => { chi.textContent = ''; }, 1500);
    } else if (m.tipo === 'tiro' && m.punto && m.ok !== undefined) {
      dopoDadi().then(() => statoPunto(m.punto, m.ok ? 'ok' : 'ko'));    // si accende quando il dado si ferma
    } else if (m.tipo === 'punto') {
      if (!punti.has(m.id) && (m.lato || m.pos)) aggiungiPunto(m);
      statoPunto(m.id, m.stato, m.etichetta);
    } else if (m.tipo === 'rivela') {
      if (punti.has(m.id)) statoPunto(m.id, 'ok');
      else segna(m.id, m.pos);
      if (m.testo) palco.diario(m.testo, 'segreto');
    }
  });

  // ---- disegno
  const occhio = new THREE.Vector3(), mondo = new THREE.Vector3();
  function giro() {
    if (palco.finita) return;
    raf = requestAnimationFrame(giro);
    const w = tela.clientWidth, h = tela.clientHeight;
    if (tela.width !== Math.floor(w * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
    }
    if (performance.now() - ultimoTocco >= MIA_PER) {
      perno.quaternion.slerp(bersaglio, .18);
      dist += (distBersaglio - dist) * .18;
    }
    const t = performance.now() / 1000;
    const k = Math.min(1, (performance.now() - entra.t0) / entra.dur), e = 1 - Math.pow(1 - k, 3);
    if (k < 1 || materiali[0] && materiali[0].opacity < 1) {
      for (const m of materiali) { m.opacity = e; m.transparent = e < 1; }
      perno.scale.setScalar(.86 + .14 * e);
    }
    dentro.position.y = Math.sin(t * .9) * raggio * .015;          // galleggia appena
    muoviPulviscolo(polvere, t);
    for (const s of segni.values()) s.scale.setScalar(1 + .25 * Math.sin(t * 4));
    camera.position.set(0, 0, dist);
    camera.near = raggio / 50;
    camera.far = raggio * 50;
    camera.updateProjectionMatrix();
    camera.lookAt(0, 0, 0);
    perno.updateMatrixWorld(true);
    // pallini: seguono il modello; dietro l'oggetto si attenuano
    occhio.copy(camera.position);
    for (const pt of punti.values()) {
      mondo.copy(pt.p);
      dentro.localToWorld(mondo);
      const d = occhio.distanceTo(mondo);
      raggi.set(occhio, mondo.clone().sub(occhio).normalize());
      const hit = raggi.intersectObject(modello, true)[0];
      pt.el.classList.toggle('dietro', !!hit && hit.distance < d - raggio * .03);
      v.copy(mondo).project(camera);
      pt.el.style.transform = `translate(${(v.x + 1) / 2 * w}px, ${(1 - v.y) / 2 * h}px)`;
    }
    renderer.render(scene, camera);
  }
  giro();
  // "pronta" allo script la manda palco.js, dopo aver passato i messaggi arrivati nel frattempo
}
