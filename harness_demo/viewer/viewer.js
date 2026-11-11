// Interactive 3D viewer of the routing results (three.js). Assembled into docs/index.html by
// harness_demo/presentation.py; the data (`#data` JSON) is embedded by the same step.
// Everything added for realism here (structure, clamps, smoothing, lighting) is visual only.

// three.js is embedded in the page as a bundle (harness_demo/viewer/vendor), so no CDN is needed.
const { THREE, OrbitControls, RoomEnvironment, CSS2DRenderer, CSS2DObject } = window.__three;

const D = JSON.parse(document.getElementById("data").textContent);
const CAPTURE = new URLSearchParams(location.search).has("capture");   // README image rendering
if (CAPTURE) {   // README images always use the dark theme, whatever the capturing browser prefers
  document.body.classList.add("capture");
  document.documentElement.dataset.theme = "dark";
}
// Large numeric arrays are embedded as base64 little-endian typed arrays (see presentation.py).
function decode(spec) {
  const bin = atob(spec.b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const T = { f32: Float32Array, u16: Uint16Array, u32: Uint32Array, i16: Int16Array, i32: Int32Array }[spec.dtype];
  return new T(bytes.buffer);
}
const R = D.radius, LEN = D.length, V = decode(D.mesh.v);
const LIFT = 0.105;          // visual standoff of the bundle centreline off the skin [m]
const SLOT = 0.017;          // spacing of cables inside a bundle [m]
const CABLE_R = { power: 0.0075, signal: 0.0055, data: 0.005 };
const CLAMP_STEP = 0.32;     // visual clamp spacing along bundles [m]
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
// Interface texts (Turkish / English) come from viewer/i18n.json through viewer/i18n.js.
const I18N = window.__i18n, t = I18N.t;
const clsName = k => (I18N.has("class." + k) ? t("class." + k) : k);
// Equipment and keep-out names are scenario data (Turkish in the example); the dictionary translates the known
// ones ("name.<text>") and any other name is shown as written in the scenario file.
const placeName = n => (I18N.has("name." + n) ? t("name." + n) : n);
// Method labels come from the pipeline in Turkish; other languages translate them by method key.
function methodLabel(m) {
  if (I18N.lang === "tr" || !I18N.has("method." + m.key)) return m.label;
  return t("method." + m.key, { k: (m.label.match(/\s*\(K=[^)]*\)/) || [""])[0] });
}
// Everything interpolated into HTML goes through esc(): data could one day come from a user file.
const esc = v => String(v).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

const vtx = i => new THREE.Vector3(V[3 * i], V[3 * i + 1], V[3 * i + 2]);
const inward = p => new THREE.Vector3(0, -p.y, -p.z).normalize();   // towards the fuselage axis
const thetaOf = p => Math.atan2(p.z, p.y);
const sep = (a, b) => D.separation[a + "|" + b] || 0;

// ---------------------------------------------------------------- renderer, scene, camera
const host = document.getElementById("viewer");
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
host.prepend(renderer.domElement);
const labelRenderer = new CSS2DRenderer();
labelRenderer.domElement.className = "labels";
host.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55;

const camera = new THREE.PerspectiveCamera(42, 1, 0.02, 200);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 0.3;
controls.maxDistance = 25;
controls.autoRotateSpeed = 0.7;

const CENTER = new THREE.Vector3(LEN / 2, 0, 1.1);
const hemi = new THREE.HemisphereLight(0xe9eef4, 0x4a453f, 0.75);
hemi.position.set(0, 0, 1);
scene.add(hemi);
// Key light shines from inside/below onto the interior of the arch so cables and frames cast shadows on the skin.
const key = new THREE.DirectionalLight(0xfff3e2, 2.3);
key.position.copy(CENTER).add(new THREE.Vector3(-2.4, -1.8, -4.6));
key.target.position.copy(CENTER);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -4.6, right: 4.6, top: 4.6, bottom: -4.6, near: 0.5, far: 16 });
key.shadow.bias = -0.0004;
key.shadow.normalBias = 0.015;
scene.add(key, key.target);
const fill = new THREE.DirectionalLight(0xdbe6ff, 0.9);
fill.position.copy(CENTER).add(new THREE.Vector3(3, 2.6, -2.2));
fill.target.position.copy(CENTER);
scene.add(fill, fill.target);
const rim = new THREE.DirectionalLight(0xffffff, 0.6);
rim.position.copy(CENTER).add(new THREE.Vector3(-6, 0, 0.4));
rim.target.position.copy(CENTER);
scene.add(rim, rim.target);

// ---------------------------------------------------------------- materials
const M = {
  primer: new THREE.MeshStandardMaterial({ color: 0x8a9c6c, roughness: 0.62, metalness: 0.2 }),
  skinOut: new THREE.MeshPhysicalMaterial({ color: 0xd5d9de, metalness: 0.6, roughness: 0.3, transparent: true,
                                            opacity: 0.12, depthWrite: false }),
  frame: new THREE.MeshStandardMaterial({ color: 0x77895c, roughness: 0.55, metalness: 0.3 }),
  alu: new THREE.MeshStandardMaterial({ color: 0xb9bec4, roughness: 0.32, metalness: 0.85 }),
  floor: new THREE.MeshStandardMaterial({ color: 0x3d434b, roughness: 0.85, metalness: 0.05 }),
  dark: new THREE.MeshStandardMaterial({ color: 0x2e3237, roughness: 0.45, metalness: 0.6 }),
  box: new THREE.MeshStandardMaterial({ color: 0x3a4047, roughness: 0.55, metalness: 0.45 }),
  boxFace: new THREE.MeshStandardMaterial({ color: 0x23272c, roughness: 0.4, metalness: 0.5 }),
  clamp: new THREE.MeshStandardMaterial({ color: 0x2a2d31, roughness: 0.5, metalness: 0.3 }),
  bracket: new THREE.MeshStandardMaterial({ color: 0x9aa1a8, roughness: 0.38, metalness: 0.8 }),
  sleeve: new THREE.MeshStandardMaterial({ color: 0xf2f1ec, roughness: 0.6 }),
  pipe: new THREE.MeshPhysicalMaterial({ color: 0xc4c8cd, metalness: 0.95, roughness: 0.22 }),
  tape: new THREE.MeshStandardMaterial({ color: 0xb8322a, roughness: 0.6 }),
  warn: new THREE.MeshStandardMaterial({ color: 0xe0aa12, roughness: 0.45, metalness: 0.25 }),
  panel: new THREE.MeshStandardMaterial({ color: 0xaab1b7, roughness: 0.36, metalness: 0.7, side: THREE.DoubleSide }),
  koFill: new THREE.MeshBasicMaterial({ color: 0xe34948, transparent: true, opacity: 0.1, depthWrite: false,
                                        side: THREE.DoubleSide }),
  koLine: new THREE.LineBasicMaterial({ color: 0xff6a62, transparent: true, opacity: 0.8 }),
  koWire: new THREE.LineBasicMaterial({ color: 0xff6a62, transparent: true, opacity: 0.22 }),
  koMargin: new THREE.LineDashedMaterial({ color: 0xff8a80, dashSize: 0.04, gapSize: 0.03, transparent: true,
                                           opacity: 0.55 }),
  viol: new THREE.MeshBasicMaterial({ color: 0xff2a3d }),
};
function shadowed(o, cast = true, receive = true) { o.castShadow = cast; o.receiveShadow = receive; return o; }

// Maps a shape drawn in the (y, z) cross-section plane and extruded along its local z onto the fuselage x axis.
const SECTION_TO_WORLD = new THREE.Matrix4().set(0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1);
function arcSection(r0, r1, a0, a1, depth, x0) {
  const s = new THREE.Shape();
  s.absarc(0, 0, r1, a0, a1, false);
  s.absarc(0, 0, r0, a1, a0, true);
  s.closePath();
  const g = new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: false, curveSegments: 120 });
  g.applyMatrix4(SECTION_TO_WORLD);
  g.translate(x0, 0, 0);
  return g;
}
// Local frame at a point: x = along the fuselage, y = circumferential, z = towards the axis.
function surfaceBasis(theta) {
  return new THREE.Matrix4().makeBasis(new THREE.Vector3(1, 0, 0),
    new THREE.Vector3(0, -Math.sin(theta), Math.cos(theta)), new THREE.Vector3(0, -Math.cos(theta), -Math.sin(theta)));
}
const labels = new THREE.Group();
scene.add(labels);
const labelEls = [];   // decluttering order: keep-out volumes first, then equipment, then cable names
const koLabels = [];   // keep-out labels ("Yasak: …"), re-texted when the language changes
const eqLabels = [];   // equipment labels, re-texted when the language changes
const LABEL_PRIORITY = { ko: 0, eq: 1, "": 2 };
function addLabel(text, pos, cls = "", color = null) {
  const el = document.createElement("div");
  el.className = "lbl " + cls;
  el.textContent = text;
  if (color) el.style.setProperty("--c", color);
  const o = new CSS2DObject(el);
  o.position.copy(pos);
  labels.add(o);
  labelEls.push({ el, prio: LABEL_PRIORITY[cls] ?? 3 });
  return o;
}

// ---------------------------------------------------------------- fuselage skin
const skinGeo = new THREE.BufferGeometry();
skinGeo.setAttribute("position", new THREE.Float32BufferAttribute(V, 3));
skinGeo.setIndex(new THREE.BufferAttribute(decode(D.mesh.f), 1));
skinGeo.computeVertexNormals();
let outwardScore = 0;
const nrm = skinGeo.attributes.normal;
for (let i = 0; i < nrm.count; i += 37) outwardScore += nrm.getY(i) * V[3 * i + 1] + nrm.getZ(i) * V[3 * i + 2];
const outwardFront = outwardScore > 0;
M.primer.side = outwardFront ? THREE.BackSide : THREE.FrontSide;   // interior: zinc-chromate style primer
M.skinOut.side = outwardFront ? THREE.FrontSide : THREE.BackSide;  // exterior: translucent bare aluminium
const skin = new THREE.Group();
skin.add(shadowed(new THREE.Mesh(skinGeo, M.primer), false, true));
skin.add(new THREE.Mesh(skinGeo, M.skinOut));
scene.add(skin);

// ---------------------------------------------------------------- structure: frames, stringers
const structure = new THREE.Group();
const FRAME_H = 0.064;
const frameWeb = arcSection(R - FRAME_H, R - 0.002, 0, Math.PI, 0.012, -0.006);
const frameCap = arcSection(R - FRAME_H - 0.004, R - FRAME_H, 0, Math.PI, 0.05, -0.025);
for (let x = 0; x <= LEN + 1e-6; x += 0.5) {
  for (const g of [frameWeb, frameCap]) {
    const m = shadowed(new THREE.Mesh(g, M.frame));
    m.position.x = x;
    structure.add(m);
  }
}
const stringerGeo = new THREE.BoxGeometry(LEN, 0.01, 0.026);
for (let deg = 7.5; deg < 180; deg += 15) {
  const t = THREE.MathUtils.degToRad(deg), r = R - 0.014;
  const m = shadowed(new THREE.Mesh(stringerGeo, M.frame));
  m.position.set(LEN / 2, r * Math.cos(t), r * Math.sin(t));
  m.rotation.x = t - Math.PI / 2;
  structure.add(m);
}
scene.add(structure);

// ---------------------------------------------------------------- floor: panels, seat tracks, beams
const floor = new THREE.Group();
const floorW = 2 * R - 0.04;
const panel = shadowed(new THREE.Mesh(new THREE.BoxGeometry(LEN, floorW, 0.018), M.floor), false, true);
panel.position.set(LEN / 2, 0, -0.009);
floor.add(panel);
for (const y of [-1.25, -0.75, 0.75, 1.25]) {
  const rail = shadowed(new THREE.Mesh(new THREE.BoxGeometry(LEN, 0.028, 0.012), M.alu), false, true);
  rail.position.set(LEN / 2, y, 0.006);
  floor.add(rail);
}
for (let x = 0; x <= LEN + 1e-6; x += 0.5) {
  const beam = new THREE.Mesh(new THREE.BoxGeometry(0.035, floorW, 0.12), M.frame);
  beam.position.set(x, 0, -0.08);
  floor.add(beam);
}
scene.add(floor);

// ---------------------------------------------------------------- keep-out volumes and the hardware inside them
const keepout = new THREE.Group();
const props = new THREE.Group();
let actuatorArm = null;
for (const vol of D.volumes) {
  let center;
  if (vol.kind === "box") {
    const lo = new THREE.Vector3(...vol.lo), hi = new THREE.Vector3(...vol.hi);
    const size = hi.clone().sub(lo);
    center = lo.clone().add(hi).multiplyScalar(0.5);
    const g = new THREE.BoxGeometry(size.x, size.y, size.z);
    const fillM = new THREE.Mesh(g, M.koFill);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g), M.koLine);
    fillM.position.copy(center); edges.position.copy(center);
    keepout.add(fillM, edges);
    if (D.clearance > 0) {   // clearance envelope used by the routing graph
      const c2 = D.clearance * 2;
      const margin = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x + c2, size.y + c2, size.z + c2)), M.koMargin);
      margin.computeLineDistances();
      margin.position.copy(center);
      keepout.add(margin);
    }
    if (vol.prop === "pipe") buildPipe(lo, hi);
    if (vol.prop === "panel") buildAccessPanel(lo, hi);
  } else {
    center = new THREE.Vector3(...vol.center);
    const fillM = new THREE.Mesh(new THREE.SphereGeometry(vol.radius, 48, 24), M.koFill);
    const wire = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.SphereGeometry(vol.radius, 18, 10)), M.koWire);
    fillM.position.copy(center); wire.position.copy(center);
    keepout.add(fillM, wire);
    if (D.clearance > 0) {
      const ring = new THREE.EllipseCurve(0, 0, vol.radius + D.clearance, vol.radius + D.clearance, 0, 2 * Math.PI);
      for (const rot of [[0, 0, 0], [Math.PI / 2, 0, 0], [0, Math.PI / 2, 0]]) {
        const l = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(ring.getPoints(96)), M.koMargin);
        l.computeLineDistances();
        l.rotation.set(...rot);
        l.position.copy(center);
        keepout.add(l);
      }
    }
    if (vol.prop === "actuator") buildActuator(center, vol.radius);
  }
  koLabels.push({ el: addLabel(t("keepout.label", { name: placeName(vol.name) }),
                               center.clone().add(inward(center).multiplyScalar(0.12)), "ko").element, name: vol.name });
}
scene.add(keepout, props);

function buildPipe(lo, hi) {
  const r = 0.045, yc = (lo.y + hi.y) / 2;
  const zc = Math.min((lo.z + hi.z) / 2, Math.sqrt(R * R - yc * yc) - 0.17);
  const len = hi.x - lo.x - 0.04, xc = (lo.x + hi.x) / 2;
  const along = g => { g.rotateZ(-Math.PI / 2); return g; };
  const tube = shadowed(new THREE.Mesh(along(new THREE.CylinderGeometry(r, r, len, 40)), M.pipe));
  tube.position.set(xc, yc, zc);
  props.add(tube);
  for (const x of [lo.x + 0.04, hi.x - 0.04, xc]) {
    const flange = shadowed(new THREE.Mesh(along(new THREE.CylinderGeometry(r * 1.45, r * 1.45, 0.03, 40)), M.pipe));
    flange.position.set(x, yc, zc);
    props.add(flange);
  }
  for (const x of [lo.x + 0.4, xc - 0.45, xc + 0.45, hi.x - 0.4]) {
    const band = new THREE.Mesh(along(new THREE.CylinderGeometry(r * 1.02, r * 1.02, 0.06, 40)), M.tape);
    band.position.set(x, yc, zc);
    props.add(band);
  }
  for (const x of [lo.x + 0.3, xc, hi.x - 0.3]) {           // support brackets up to the crown
    const top = Math.sqrt(R * R - yc * yc) - 0.01, h = top - (zc + r);
    const b = shadowed(new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.05, h), M.bracket));
    b.position.set(x, yc, zc + r + h / 2);
    props.add(b);
  }
}

function buildAccessPanel(lo, hi) {
  const inBox = t => { const y = R * Math.cos(t), z = R * Math.sin(t);
                       return y > lo.y && y < hi.y && z > lo.z && z < hi.z; };
  let t0 = null, t1 = null;
  for (let t = 0; t <= Math.PI; t += Math.PI / 720) if (inBox(t)) { if (t0 === null) t0 = t; t1 = t; }
  if (t0 === null) return;
  const r = R - 0.006, nx = 6, nt = 18, pos = [], idx = [];
  for (let i = 0; i <= nx; i++) for (let j = 0; j <= nt; j++) {
    const x = lo.x + (hi.x - lo.x) * i / nx, t = t0 + (t1 - t0) * j / nt;
    pos.push(x, r * Math.cos(t), r * Math.sin(t));
  }
  for (let i = 0; i < nx; i++) for (let j = 0; j < nt; j++) {
    const a = i * (nt + 1) + j, b = a + nt + 1;
    idx.push(a, b, a + 1, b, b + 1, a + 1);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  props.add(shadowed(new THREE.Mesh(g, M.panel), false, true));
  // Fasteners along the panel border.
  const pts = [];
  const rr = R - 0.009, step = 0.06;
  for (let x = lo.x + 0.02; x <= hi.x - 0.02; x += step) for (const t of [t0 + 0.012, t1 - 0.012]) pts.push([x, t]);
  for (let t = t0 + 0.012; t <= t1 - 0.012; t += step / R) for (const x of [lo.x + 0.02, hi.x - 0.02]) pts.push([x, t]);
  const fast = new THREE.InstancedMesh(new THREE.SphereGeometry(0.006, 8, 6), M.dark, pts.length);
  const mtx = new THREE.Matrix4();
  pts.forEach(([x, t], k) => fast.setMatrixAt(k, mtx.makeTranslation(x, rr * Math.cos(t), rr * Math.sin(t))));
  props.add(fast);
  // Yellow border marking.
  const border = [];
  for (let j = 0; j <= nt; j++) border.push(new THREE.Vector3(lo.x, 0, 0).setY(rr * Math.cos(t0 + (t1 - t0) * j / nt)).setZ(rr * Math.sin(t0 + (t1 - t0) * j / nt)));
  for (let j = nt; j >= 0; j--) border.push(new THREE.Vector3(hi.x, rr * Math.cos(t0 + (t1 - t0) * j / nt), rr * Math.sin(t0 + (t1 - t0) * j / nt)));
  border.push(border[0].clone());
  props.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(border), new THREE.LineBasicMaterial({ color: 0xf2c230 })));
}

function buildActuator(c, envR) {
  const n = inward(c), basis = surfaceBasis(thetaOf(c));
  const mount = shadowed(new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.16, 0.025), M.bracket));
  mount.quaternion.setFromRotationMatrix(basis);
  mount.position.copy(c).addScaledVector(n, 0.014);
  props.add(mount);
  const bodyLen = 0.26;
  const body = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.05, bodyLen, 32), M.dark));
  body.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), n);
  body.position.copy(c).addScaledVector(n, 0.03 + bodyLen / 2);
  props.add(body);
  const hinge = c.clone().addScaledVector(n, 0.03 + bodyLen + 0.02);
  const pin = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.12, 24).rotateZ(-Math.PI / 2), M.alu));
  pin.position.copy(hinge);
  props.add(pin);
  // The arm swings about the fuselage axis direction, sweeping inside the keep-out envelope.
  actuatorArm = new THREE.Group();
  actuatorArm.position.copy(hinge);
  const armLen = Math.min(0.17, envR - 0.32);
  const arm = shadowed(new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.035, armLen), M.warn));
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
  arm.quaternion.copy(q);
  arm.position.copy(n).multiplyScalar(armLen / 2);
  const rod = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, armLen * 0.9, 16), M.pipe));
  rod.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), n);
  rod.position.copy(n).multiplyScalar(armLen / 2).add(new THREE.Vector3(0.04, 0, 0));
  actuatorArm.add(arm, rod);
  props.add(actuatorArm);
}

// ---------------------------------------------------------------- equipment racks at the cable terminals
const equipment = new THREE.Group();
{
  // equipment name (from the scenario) -> terminal positions; a Map, so names like "constructor" are just names
  const groups = new Map();
  for (const c of D.cables) for (const [node, name] of [[c.start, c.from], [c.end, c.to]]) {
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(vtx(node));
  }
  for (const [name, pts] of groups) {
    const ths = pts.map(thetaOf), aft = pts[0].x < LEN / 2;
    const t0 = Math.min(...ths) - 0.07, t1 = Math.max(...ths) + 0.07;
    const xs = pts.map(p => p.x), depth = 0.18;
    const x0 = aft ? Math.max(0.02, Math.min(...xs) - 0.05 - depth) : Math.min(LEN - 0.02 - depth, Math.max(...xs) + 0.05);
    const box = shadowed(new THREE.Mesh(arcSection(R - 0.22, R - 0.004, t0, t1, depth, x0), M.box));
    equipment.add(box);
    const face = new THREE.Mesh(arcSection(R - 0.21, R - 0.014, t0 + 0.012, t1 - 0.012, 0.004, aft ? x0 + depth : x0 - 0.004), M.boxFace);
    equipment.add(face);
    const tm = (t0 + t1) / 2, rm = R - 0.11;
    eqLabels.push({ el: addLabel(placeName(name), new THREE.Vector3(x0 + depth / 2, rm * Math.cos(tm), rm * Math.sin(tm)),
                                 "eq").element, name });
  }
}
scene.add(equipment);

// ---------------------------------------------------------------- cables (one group per routing method)
const SLOTS = [[0, 0]];
for (let a = 0; a < 6; a++) SLOTS.push([SLOT * Math.cos(a * Math.PI / 3), SLOT * Math.sin(a * Math.PI / 3)]);
for (let a = 0; a < 6; a++) SLOTS.push([SLOT * 1.75 * Math.cos(a * Math.PI / 3 + Math.PI / 6),
                                        SLOT * 1.75 * Math.sin(a * Math.PI / 3 + Math.PI / 6)]);

// Centreline lifted off the skin, lightly smoothed and kept on the offset cylinder (visual only).
function centreline(path) {
  let pts = path.map(i => { const p = vtx(i); return p.addScaledVector(inward(p), LIFT); });
  for (let it = 0; it < 4; it++) {
    pts = pts.map((p, i) => (i === 0 || i === pts.length - 1) ? p
      : p.clone().multiplyScalar(0.5).addScaledVector(pts[i - 1], 0.25).addScaledVector(pts[i + 1], 0.25));
    for (const p of pts) { const rr = Math.hypot(p.y, p.z), s = (R - LIFT) / rr; p.y *= s; p.z *= s; }
  }
  return pts;
}
function tangents(pts, sign) {
  return pts.map((p, i) => {
    const a = pts[Math.max(i - 1, 0)], b = pts[Math.min(i + 1, pts.length - 1)];
    const t = b.clone().sub(a).multiplyScalar(sign), n = inward(p);
    return t.addScaledVector(n, -t.dot(n)).normalize();
  });
}

const methods = D.methods.map(m => buildMethod(m));
function buildMethod(m) {
  const group = new THREE.Group();
  const clampsG = new THREE.Group(), violG = new THREE.Group();
  const cables = [];
  const centres = [];
  D.cables.forEach((c, k) => {
    const path = m.routes[c.name];
    const ctr = centreline(path);
    const dir = ctr[ctr.length - 1].clone().sub(ctr[0]);
    const sign = (dir.x + 0.05 * dir.z) >= 0 ? 1 : -1;  // one lateral orientation for cables travelling the same way
    const tg = tangents(ctr, sign);
    const [u, v] = SLOTS[k % SLOTS.length];
    const pts = ctr.map((p, i) => {
      const n = inward(p), b = new THREE.Vector3().crossVectors(n, tg[i]).normalize();
      return p.clone().addScaledVector(b, u).addScaledVector(n, v);
    });
    const curve = new THREE.CatmullRomCurve3(pts, false, "centripetal");
    const segs = Math.max(32, Math.round(curve.getLength() / 0.018));
    const radial = 10;
    const geo = new THREE.TubeGeometry(curve, segs, CABLE_R[c.cls] ?? 0.005, radial, false);
    const mat = new THREE.MeshPhysicalMaterial({ color: D.classColors[c.cls], roughness: 0.5, metalness: 0.0,
                                                 clearcoat: 0.3, clearcoatRoughness: 0.45 });
    const mesh = shadowed(new THREE.Mesh(geo, mat));
    mesh.userData = { cable: c, k };
    group.add(mesh);
    // Connectors (backshell + coupling ring in class colour) and white ID sleeves near both ends.
    const ends = new THREE.Group();
    const len = curve.getLength();
    for (const [u0, outDir] of [[0, -1], [1, 1]]) {
      const p = curve.getPointAt(u0), t = curve.getTangentAt(u0).multiplyScalar(outDir);
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), t);
      const shell = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(0.013, 0.016, 0.05, 20), M.dark));
      shell.quaternion.copy(q); shell.position.copy(p).addScaledVector(t, 0.025);
      const ring = new THREE.Mesh(new THREE.CylinderGeometry(0.0185, 0.0185, 0.012, 20),
                                  new THREE.MeshStandardMaterial({ color: D.classColors[c.cls], roughness: 0.4, metalness: 0.3 }));
      ring.quaternion.copy(q); ring.position.copy(p).addScaledVector(t, 0.046);
      ends.add(shell, ring);
      const us = Math.min(0.12 / len, 0.3), uu = u0 === 0 ? us : 1 - us;
      const sl = new THREE.Mesh(new THREE.CylinderGeometry((CABLE_R[c.cls] ?? 0.005) * 1.35, (CABLE_R[c.cls] ?? 0.005) * 1.35, 0.035, 14), M.sleeve);
      sl.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), curve.getTangentAt(uu));
      sl.position.copy(curve.getPointAt(uu));
      ends.add(sl);
    }
    group.add(ends);
    cables.push({ c, mesh, geo, segs, radial, curve, ends, length: m.lengths[c.name], margin: m.margins && Object.hasOwn(m.margins, c.name) ? m.margins[c.name] : undefined });
    centres.push(ctr);
  });
  buildClamps(centres, clampsG);
  buildViolations(m.violations, violG);
  group.add(clampsG, violG);
  group.visible = false;
  scene.add(group);
  const seq = (m.order || D.cables.map(c => c.name)).map(n => D.cables.findIndex(c => c.name === n));
  return { m, group, cables, clampsG, violG, seq };
}

function distToPolyline(p, pts) {
  let best = Infinity;
  const ab = new THREE.Vector3(), ap = new THREE.Vector3();
  for (let i = 0; i < pts.length - 1; i++) {
    ab.subVectors(pts[i + 1], pts[i]); ap.subVectors(p, pts[i]);
    const t = THREE.MathUtils.clamp(ap.dot(ab) / Math.max(ab.lengthSq(), 1e-12), 0, 1);
    best = Math.min(best, ap.sub(ab.multiplyScalar(t)).length());
  }
  return best;
}
function buildClamps(centres, parent) {
  const spots = [];
  centres.forEach(ctr => {
    let acc = CLAMP_STEP * 0.6;
    for (let i = 1; i < ctr.length; i++) {
      const seg = ctr[i].distanceTo(ctr[i - 1]);
      acc += seg;
      if (acc < CLAMP_STEP || i === ctr.length - 1) continue;
      acc = 0;
      const p = ctr[i];
      if (spots.some(s => s.p.distanceTo(p) < 0.09)) continue;
      const t = ctr[i + 1].clone().sub(ctr[i - 1]).normalize();
      const n = inward(p);
      t.addScaledVector(n, -t.dot(n)).normalize();
      const count = centres.filter(o => distToPolyline(p, o) < 0.012).length;
      spots.push({ p, t, n, count });
    }
  });
  const torus = new THREE.InstancedMesh(new THREE.TorusGeometry(0.02, 0.0026, 8, 28), M.clamp, spots.length);
  const brk = new THREE.InstancedMesh(new THREE.BoxGeometry(0.016, 0.026, 1), M.bracket, spots.length);
  const mtx = new THREE.Matrix4(), s = new THREE.Vector3(), q = new THREE.Quaternion();
  spots.forEach((sp, k) => {
    const rad = 0.0095 + (sp.count > 1 ? SLOT : 0) + (sp.count > 7 ? SLOT * 0.75 : 0);
    const basisT = new THREE.Matrix4().makeBasis(sp.n, new THREE.Vector3().crossVectors(sp.t, sp.n), sp.t);
    q.setFromRotationMatrix(basisT);
    mtx.compose(sp.p, q, s.set(rad / 0.02, rad / 0.02, 1));
    torus.setMatrixAt(k, mtx);
    const h = Math.max(0.01, LIFT - rad);
    const basisB = new THREE.Matrix4().makeBasis(sp.t, new THREE.Vector3().crossVectors(sp.n, sp.t), sp.n);
    q.setFromRotationMatrix(basisB);
    const base = sp.p.clone().addScaledVector(sp.n, -LIFT + h / 2);
    mtx.compose(base, q, s.set(1, 1, h));
    brk.setMatrixAt(k, mtx);
  });
  shadowed(torus); shadowed(brk);
  parent.add(torus, brk);
}
function buildViolations(spec, parent) {
  const mm = decode(spec), pts = [];   // violation points in millimetres, already de-duplicated
  for (let i = 0; i < mm.length; i += 3) {
    const p = new THREE.Vector3(mm[i] / 1000, mm[i + 1] / 1000, mm[i + 2] / 1000);
    pts.push(p.addScaledVector(inward(p), LIFT));
  }
  if (!pts.length) return;
  const inst = new THREE.InstancedMesh(new THREE.SphereGeometry(CAPTURE ? 0.024 : 0.012, 10, 8), M.viol, pts.length);
  const mtx = new THREE.Matrix4();
  pts.forEach((p, k) => inst.setMatrixAt(k, mtx.makeTranslation(p.x, p.y, p.z)));
  parent.add(inst);
}

// Cable name tags at the start terminals (same for every method).
const cableLabels = new THREE.Group();
labels.add(cableLabels);
for (const c of D.cables) {
  const p = vtx(c.start);
  const o = addLabel(c.name, p.addScaledVector(inward(p), LIFT + 0.07), "", D.classColors[c.cls]);
  labels.remove(o); cableLabels.add(o);
}

// ---------------------------------------------------------------- UI: theme, tabs, summary, stats, legend, toggles, views
labelEls.sort((a, b) => a.prio - b.prio);

// Theme switch: follows the OS setting until it is flipped once; the choice is then remembered.
const themeBtn = document.getElementById("theme");
const darkMedia = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => (document.documentElement.dataset.theme || (darkMedia.matches ? "dark" : "light")) === "dark";
const syncThemeSwitch = () => themeBtn.setAttribute("aria-checked", String(isDark()));
themeBtn.addEventListener("click", () => {
  const t = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("emc-kablo:theme", t); } catch (e) { /* storage blocked: the switch still works for this visit */ }
  syncThemeSwitch();
});
darkMedia.addEventListener("change", syncThemeSwitch);
syncThemeSwitch();

// Language: TR / EN segmented control; every text the viewer renders is redrawn on change.
const langEl = document.getElementById("lang");
const syncLang = () => setRadio(langEl, langEl.querySelector(`[data-lang="${I18N.lang}"]`));
for (const b of langEl.querySelectorAll("button")) b.addEventListener("click", () => I18N.set(b.dataset.lang));
radioKeys(langEl);
syncLang();

// Arrow keys move through a radiogroup of buttons (speed, camera presets) and activate the new one.
function radioKeys(group) {
  group.addEventListener("keydown", e => {
    const items = [...group.querySelectorAll('[role="radio"]')], i = items.indexOf(document.activeElement);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (i < 0 || !step) return;
    e.preventDefault();
    const next = items[(i + step + items.length) % items.length];
    next.focus();
    next.click();
  });
}
function setRadio(group, current) {   // aria-checked + roving tabindex, then slide the thumb to it
  for (const b of group.querySelectorAll('[role="radio"]')) {
    b.setAttribute("aria-checked", String(b === current));
    b.tabIndex = b === current ? 0 : -1;
  }
  placeThumb(group);
}
// The highlighted background of a segmented control is one element that moves under the checked button.
function placeThumb(group, instant = false) {
  let thumb = group.querySelector(".seg-thumb");
  if (!thumb) {
    thumb = document.createElement("span");
    thumb.className = "seg-thumb";
    thumb.setAttribute("aria-hidden", "true");
    group.prepend(thumb);
    new ResizeObserver(() => placeThumb(group, true)).observe(group);   // layout changes: follow without sliding
    instant = true;
  }
  const cur = group.querySelector('[aria-checked="true"]');
  if (!cur) return;
  if (instant) thumb.style.transition = "none";
  thumb.style.setProperty("--x", `${cur.offsetLeft}px`);
  thumb.style.setProperty("--w", `${cur.offsetWidth}px`);
  if (instant) { void thumb.offsetWidth; thumb.style.transition = ""; }   // apply, then re-enable the animation
}
const CHECK_SVG = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

const fmt = (x, d) => x.toLocaleString(I18N.locale(), { minimumFractionDigits: d, maximumFractionDigits: d });
const CHECKS = [   // [metric key, label key, unit key]
  ["emc_points", "check.emc", "unit.points"], ["capacity_edges", "check.capacity", "unit.edges"],
  ["bend_points", "check.bend", "unit.points"], ["forbidden_points", "check.forbidden", "unit.points"],
  ["clearance_points", "check.clearance", "unit.points"],
];
const checkList = m => failedChecks(m).map(([k, l, u]) => `${esc(t(l))} ${fmt(m[k], 0)} ${esc(t(u))}`).join(", ");
const mark = ok => (ok ? "✓" : "✗");   // status is never shown by colour alone
const failedChecks = m => CHECKS.filter(([k]) => m[k]);

let active = null;
const tabsEl = document.getElementById("tabs"), panelEl = document.getElementById("panel");
methods.forEach((v, i) => {
  const b = document.createElement("button");
  b.type = "button"; b.id = "tab-" + v.m.key; b.textContent = methodLabel(v.m);
  b.setAttribute("role", "tab"); b.setAttribute("aria-controls", "panel"); b.dataset.key = v.m.key;
  b.addEventListener("click", () => select(v.m.key));
  b.addEventListener("keydown", e => {   // roving focus: arrows, Home, End (the tab is selected on focus)
    const n = methods.length;
    const to = { ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    select(methods[to].m.key);
    tabsEl.children[to].focus();
  });
  tabsEl.appendChild(b);
});

function renderSummary(m) {
  const bad = failedChecks(m);
  const badge = bad.length
    ? `<span class="badge ng">✗ ${esc(t("summary.bad", { n: bad.length }))}</span>`
    : `<span class="badge ok">✓ ${esc(t("summary.ok"))}</span>`;
  document.getElementById("summary").innerHTML = badge +
    esc(t("summary.line", { len: fmt(m.total_length_m, 2), ratio: fmt(m.bundling_ratio, 3) })) +
    (bad.length ? ` · ${checkList(m)}` : "");
}
function renderStats(m) {
  const items = [
    [t("stats.total"), fmt(m.total_length_m, 2) + " m", ""],
    [t("stats.unique"), fmt(m.unique_length_m, 2) + " m", ""],
    [t("stats.bundling"), fmt(m.bundling_ratio, 3), ""],
    ...CHECKS.map(([k, l, u]) => [t("stats.violation", { check: t(l) }), `${mark(!m[k])} ${fmt(m[k], 0)} ${t(u)}`,
                                  m[k] ? "ng" : "ok"]),
  ];
  document.getElementById("stats").innerHTML = items
    .map(([k, v, c]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${c}">${esc(v)}</div></div>`).join("");
}
const statsBox = document.getElementById("statsbox");
statsBox.open = matchMedia("(min-width: 721px)").matches;   // on phones the 3D view comes first

function renderLegend() {
  document.getElementById("legend").innerHTML =
    Object.entries(D.classColors).map(([k, c]) =>
      `<div><span class="sw" style="background:${esc(c)}"></span>${esc(clsName(k))}</div>`).join("") +
    `<div><span class="sw" style="background:#e34948;opacity:.75"></span>${esc(t("legend.keepout"))}</div>` +
    `<div><span class="dot" style="background:#ff2a3d"></span>${esc(t("legend.viol"))}</div>`;
}
renderLegend();

const toggles = [   // [key, initially on, apply]; the chip text is t("layer." + key)
  ["skin", true, v => { skin.visible = v; }],
  ["structure", true, v => { structure.visible = v; }],
  ["floor", true, v => { floor.visible = v; }],
  ["keepout", true, v => { keepout.visible = v; }],
  ["equipment", true, v => { equipment.visible = v; props.visible = v; }],
  ["clamps", true, v => methods.forEach(x => { x.clampsG.visible = v; })],
  ["viol", true, v => methods.forEach(x => { x.violG.visible = v; })],
  ["labels", true, v => { labels.visible = v; }],
  // on at start, except for README captures (fixed camera) and when the user asks for reduced motion
  ["rotate", !CAPTURE && !REDUCED, v => { controls.autoRotate = v; }],
];
const chipEls = {}, chipText = {};
const chips = document.getElementById("chips");
for (const [key, on, fn] of toggles) {
  const l = document.createElement("label");
  l.className = "chip";
  const input = document.createElement("input");
  input.type = "checkbox"; input.className = "sr-only"; input.checked = on;
  const ind = document.createElement("span");
  ind.className = "chip-ind"; ind.setAttribute("aria-hidden", "true"); ind.innerHTML = CHECK_SVG;
  chipText[key] = document.createTextNode(t("layer." + key));
  l.append(input, ind, chipText[key]);
  const sync = () => l.classList.toggle("on", input.checked);
  input.addEventListener("change", () => { sync(); fn(input.checked); requestRender(); });
  chipEls[key] = input;
  chips.appendChild(l);
  sync();
  fn(on);
}
function setToggle(key, v) { if (chipEls[key].checked !== v) { chipEls[key].checked = v; chipEls[key].dispatchEvent(new Event("change")); } }

const VIEWS = {
  inside: { pos: [-1.35, -0.3, 1.0], target: [3.2, 0, 1.3], floor: true },   // button text: t("view." + key)
  below: { pos: [0.1, -3.4, -3.9], target: [3, 0, 1.1], floor: false },
  side: { pos: [3.9, -7.6, 1.7], target: [3, 0, 1.15], floor: true },
  top: { pos: [3, -1.3, 8.2], target: [3, 0, 0.8], floor: true },
};
const viewsEl = document.getElementById("views"), viewBtns = {};
for (const key of Object.keys(VIEWS)) {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = t("view." + key); b.setAttribute("role", "radio");
  b.addEventListener("click", () => flyTo(key));
  viewsEl.appendChild(b);
  viewBtns[key] = b;
}
radioKeys(viewsEl);
let tween = null, currentView = "side";
function flyTo(key, instant = false) {
  const v = VIEWS[key];
  currentView = key;
  setRadio(viewsEl, viewBtns[key]);
  setToggle("floor", v.floor);
  const p1 = new THREE.Vector3(...v.pos), t1 = new THREE.Vector3(...v.target);
  if (instant || REDUCED) { camera.position.copy(p1); controls.target.copy(t1); tween = null; }
  else tween = { p0: camera.position.clone(), t0: controls.target.clone(), p1, t1, start: performance.now(), dur: 1100 };
  writeHash();
  requestRender();
}

// ---------------------------------------------------------------- routing playback: play, scrub, speed
const statusEl = document.getElementById("status");
const playBtn = document.getElementById("play"), scrub = document.getElementById("scrub"), speedEl = document.getElementById("speed");
let anim = null, speed = 1;
const PER_CABLE = 0.75;   // seconds per cable at 1x
function setProgress(view, f) {   // f in [0, n]: cables drawn in routing order, the first floor(f) complete
  view.seq.forEach((ci, rank) => {
    const cb = view.cables[ci];
    const frac = THREE.MathUtils.clamp(f - rank, 0, 1);
    cb.geo.setDrawRange(0, Math.floor(frac * cb.segs) * cb.radial * 6);
    cb.ends.visible = frac > 0;
  });
  const n = view.cables.length, done = f >= n;
  view.clampsG.visible = done && chipEls.clamps.checked;
  view.violG.visible = done && chipEls.viol.checked;
  scrub.value = String(f / n);
  scrub.style.setProperty("--p", `${(Math.min(f / n, 1) * 100).toFixed(1)}%`);   // filled part of the track
  const k = Math.min(Math.floor(f), n - 1), cb = view.cables[view.seq[k]];
  statusEl.textContent = done ? "" : t("status.routing", { name: cb.c.name, cls: clsName(cb.c.cls), k: k + 1, n });
  requestRender();
}
function play() {
  const v = methods[active], n = v.cables.length;
  const from = Number(scrub.value) * n;
  anim = { view: v, start: performance.now(), from: from >= n ? 0 : from };
  setPlaying(true);
  setProgress(v, anim.from);
}
function stop() {   // pause where it is
  anim = null;
  setPlaying(false);
}
function setPlaying(on) {
  playBtn.classList.toggle("playing", on);
  playBtn.setAttribute("aria-label", t(on ? "play.pause" : "play.aria"));
  playBtn.querySelector(".play-text").textContent = t(on ? "play.pause" : "play.play");
}
function finish() {
  if (anim) setProgress(anim.view, anim.view.cables.length);
  stop();
}
playBtn.addEventListener("click", () => (anim ? stop() : play()));
scrub.addEventListener("input", () => { stop(); const v = methods[active]; setProgress(v, Number(scrub.value) * v.cables.length); });
for (const b of speedEl.querySelectorAll("button")) {
  b.addEventListener("click", () => {
    speed = Number(b.dataset.speed);
    setRadio(speedEl, b);
    if (!anim) return;   // keep the current position when the speed changes mid-playback
    const v = methods[active];
    anim = { view: v, start: performance.now(), from: Number(scrub.value) * v.cables.length };
  });
}
const speedTexts = () => speedEl.querySelectorAll("button").forEach(b => {
  b.textContent = fmt(Number(b.dataset.speed), b.dataset.speed.includes(".") ? 1 : 0) + "×";   // 0,5× / 0.5×
});
speedTexts();
setRadio(speedEl, speedEl.querySelector('[data-speed="1"]'));
radioKeys(speedEl);

// ---------------------------------------------------------------- fullscreen 3D view
// Element fullscreen where the browser has it; otherwise (e.g. iPhone Safari) the view fills the window.
const fsBtn = document.getElementById("fullscreen");
const inFullscreen = () => document.fullscreenElement === host || host.classList.contains("pseudo-fs");
function syncFullscreen() {
  const on = inFullscreen();
  fsBtn.classList.toggle("on", on);
  fsBtn.setAttribute("aria-label", t(on ? "fs.exit" : "fs.enter"));
  fsBtn.title = t(on ? "fs.exitTitle" : "fs.enterTitle");
  document.body.classList.toggle("fs-lock", host.classList.contains("pseudo-fs"));
}
async function toggleFullscreen() {
  if (!inFullscreen()) tourEnd();   // the tour card lives outside the 3D view and would be hidden behind it
  if (document.fullscreenElement) await document.exitFullscreen();
  else if (host.classList.contains("pseudo-fs")) host.classList.remove("pseudo-fs");
  else if (host.requestFullscreen && document.fullscreenEnabled) {
    try { await host.requestFullscreen({ navigationUI: "hide" }); } catch (e) { host.classList.add("pseudo-fs"); }
  } else host.classList.add("pseudo-fs");
  syncFullscreen();
}
fsBtn.addEventListener("click", toggleFullscreen);
document.addEventListener("fullscreenchange", syncFullscreen);
addEventListener("keydown", e => {
  const typing = e.target.closest && e.target.closest("input, select, textarea");
  if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey && !typing) toggleFullscreen();
  // Escape closes the tour first (its own handler); only a later Escape leaves the window-filling view
  else if (e.key === "Escape" && host.classList.contains("pseudo-fs") && tourStep < 0) toggleFullscreen();
});

// ---------------------------------------------------------------- method selection, cable table, comparison
const cableBody = document.querySelector("#cabletable tbody");
const cmpBody = document.querySelector("#compare tbody");
function select(key) {   // the playback never starts by itself: the Play button starts it
  finish();
  clearSelection();
  setHover(null);   // the hovered cable belongs to the method being hidden
  const idx = methods.findIndex(v => v.m.key === key);
  methods.forEach((v, i) => { v.group.visible = i === idx; });
  active = idx;
  [...tabsEl.children].forEach(b => {
    const on = b.dataset.key === key;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
  });
  panelEl.setAttribute("aria-labelledby", "tab-" + key);
  const m = methods[idx].m;
  renderSummary(m);
  renderStats(m);
  renderCableTable(methods[idx]);
  [...cmpBody.children].forEach(tr => tr.setAttribute("aria-selected", String(tr.dataset.key === key)));
  setProgress(methods[idx], methods[idx].cables.length);
  updateHover();   // a resting pointer now points at this method's cables (or at none)
  writeHash();
}

const cm = v => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${fmt(v * 100, 1)} cm`);
function renderCableTable(view) {
  cableBody.replaceChildren(...view.cables.map(cb => {
    const mg = cb.margin || {}, c = cb.c;
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.cable = c.name;
    tr.setAttribute("aria-selected", String(selected === cb));
    const okEmc = mg.emc == null || mg.emc >= 0, okBend = mg.bend == null || mg.bend >= D.minBendRadius;
    const okKo = mg.keepout == null || mg.keepout >= D.clearance;
    tr.innerHTML =
      `<th scope="row">${esc(c.name)}</th>` +
      `<td><span class="sw-cls" style="background:${esc(D.classColors[c.cls])}"></span>${esc(clsName(c.cls))}</td>` +
      `<td>${esc(placeName(c.from))} → ${esc(placeName(c.to))}</td>` +
      `<td class="num">${fmt(cb.length, 2)}</td>` +
      `<td class="num ${okEmc ? "ok" : "ng"}">${mark(okEmc)} ${esc(cm(mg.emc))}</td>` +
      `<td class="num ${okBend ? "ok" : "ng"}">${mark(okBend)} ${mg.bend == null ? "—" : fmt(mg.bend, 3)}</td>` +
      `<td class="num ${okKo ? "ok" : "ng"}">${mark(okKo)} ${mg.keepout == null ? "—" : fmt(mg.keepout * 100, 1) + " cm"}</td>` +
      `<td class="num">${mg.parallel == null ? "—" : fmt(mg.parallel, 2)}</td>` +
      `<td class="${mg.status === "OK" ? "ok" : "ng"}">${mark(mg.status === "OK")} ${esc(mg.status ?? "—")}</td>`;
    const pick = () => (selected === cb ? clearSelection() : selectCable(cb));
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    return tr;
  }));
}

const maxLen = Math.max(...methods.map(v => v.m.total_length_m));
function renderCompare() {
  cmpBody.replaceChildren(...methods.map(v => {
    const m = v.m, bad = failedChecks(m);
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.key = m.key;
    tr.setAttribute("aria-selected", String(active !== null && methods[active].m.key === m.key));
    tr.innerHTML =
      `<th scope="row">${esc(methodLabel(m))}</th>` +
      `<td><div class="bar"><span class="fill" style="width:${(m.total_length_m / maxLen * 140).toFixed(0)}px"></span>` +
      `${fmt(m.total_length_m, 2)} m</div></td>` +
      `<td class="num">${fmt(m.bundling_ratio, 3)}</td>` +
      `<td class="${bad.length ? "ng" : "ok"}">${bad.length ? "✗ " + checkList(m) : "✓ " + esc(t("compare.allClean"))}</td>`;
    const go = () => { select(m.key); document.getElementById("tab-" + m.key).scrollIntoView({ block: "nearest" }); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    return tr;
  }));
}
renderCompare();

// ---------------------------------------------------------------- hover tooltip and click-to-select
const tip = document.getElementById("tip"), selinfo = document.getElementById("selinfo");
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let pointerPx = null, hovered = null, selected = null, envelope = null, downAt = null;
renderer.domElement.addEventListener("pointermove", e => {
  const r = renderer.domElement.getBoundingClientRect();
  pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  pointerPx = [e.clientX - r.left, e.clientY - r.top];
  updateHover();
});
renderer.domElement.addEventListener("pointerleave", () => { pointerPx = null; updateHover(); });
renderer.domElement.addEventListener("pointerdown", e => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener("pointerup", e => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;
  const hit = pick();   // a click on the selected cable deselects it, as in the cable table
  if (hit && hit !== selected) selectCable(hit); else clearSelection();
});
function pick() {
  if (active === null || !pointerPx) return null;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(methods[active].cables.map(c => c.mesh), false);
  return hits.length ? methods[active].cables.find(c => c.mesh === hits[0].object) : null;
}
function updateHover() {
  const cb = pointerPx && !tween ? pick() : null;
  setHover(cb);
  if (!cb) { tip.style.display = "none"; return; }
  tip.style.display = "block";
  tip.style.left = pointerPx[0] + "px"; tip.style.top = pointerPx[1] + "px";
  tip.innerHTML = `<b>${esc(cb.c.name)}</b> · ${esc(clsName(cb.c.cls))} · ${fmt(cb.length, 2)} m` +
    (cb.margin ? `<br>${marginHtml(cb.margin)}` : "");
}
// Per-cable verification margins (independent checks): EMC separation, bend radius, keep-out distance.
function marginHtml(mg) {
  const cls = ok => (ok ? "ok" : "ng");
  const parts = [
    [t("margin.emc"), mg.emc === null || mg.emc >= 0, cm(mg.emc)],
    [t("margin.bend"), mg.bend === null || mg.bend >= D.minBendRadius, mg.bend === null ? "—" : fmt(mg.bend, 3) + " m"],
    [t("margin.keepout"), mg.keepout === null || mg.keepout >= D.clearance,
     mg.keepout === null ? "—" : fmt(mg.keepout * 100, 1) + " cm"],
  ].map(([k, ok, v]) => `${esc(k)} <span class="${cls(ok)}">${mark(ok)} ${esc(v)}</span>`);
  return parts.join(" · ") + ` · <span class="${cls(mg.status === "OK")}">${mark(mg.status === "OK")} ${esc(mg.status)}</span>`;
}
function setHover(cb) {
  if (hovered === cb) return;
  if (hovered && hovered !== selected) hovered.mesh.material.emissive.setHex(0x000000);
  hovered = cb;
  if (cb) cb.mesh.material.emissive.setHex(0x333333);
  requestRender();
}
function selectCable(cb) {
  clearSelection();
  selected = cb;
  cb.mesh.material.emissive.setHex(0x555555);
  const others = [...new Set(D.cables.map(c => c.cls))].filter(k => k !== cb.c.cls);
  const radius = Math.max(0, ...others.map(k => sep(cb.c.cls, k)));
  if (radius > 0) {
    envelope = new THREE.Mesh(new THREE.TubeGeometry(cb.curve, Math.round(cb.segs / 2), radius, 28, false),
      new THREE.MeshBasicMaterial({ color: D.classColors[cb.c.cls], transparent: true, opacity: 0.12, depthWrite: false,
                                    side: THREE.DoubleSide }));
    scene.add(envelope);
  }
  renderSelinfo(cb);
  for (const tr of cableBody.children) tr.setAttribute("aria-selected", String(tr.dataset.cable === cb.c.name));
  writeHash();
  requestRender();
}
function renderSelinfo(cb) {
  const others = [...new Set(D.cables.map(c => c.cls))].filter(k => k !== cb.c.cls);
  const seps = others.map(k => `${clsName(k)}: ${fmt(sep(cb.c.cls, k), 2)} m`).join(", ");
  selinfo.innerHTML = `<b>${esc(t("sel.title", { name: cb.c.name }))}</b> ` +
    `(${esc(clsName(cb.c.cls))}, ${esc(placeName(cb.c.from))} → ${esc(placeName(cb.c.to))}) — ` +
    `${esc(t("sel.length", { len: fmt(cb.length, 2) }))} ` +
    (cb.margin ? t("sel.margins", { margins: marginHtml(cb.margin) }) + " " : "") +   // marginHtml escapes its parts
    esc(t("sel.envelope", { seps }));
}
function clearSelection() {
  // a cable that is still under the pointer keeps its hover highlight
  if (selected) selected.mesh.material.emissive.setHex(selected === hovered ? 0x333333 : 0x000000);
  selected = null;
  if (envelope) { scene.remove(envelope); envelope.geometry.dispose(); envelope.material.dispose(); envelope = null; }
  selinfo.textContent = "";
  for (const tr of cableBody.children) tr.setAttribute("aria-selected", "false");
  writeHash();
  requestRender();
}

// ---------------------------------------------------------------- shareable state in the URL (#m=…&v=…&c=…)
const VALID = { m: new Set(methods.map(v => v.m.key)), v: new Set(Object.keys(VIEWS)), c: new Set(D.cables.map(c => c.name)) };
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1)), out = {};
  for (const k of ["m", "v", "c"]) out[k] = VALID[k].has(p.get(k)) ? p.get(k) : null;   // only known values
  return out;
}
let hashReady = false;   // no writes while the initial state is restored
function writeHash() {
  if (CAPTURE || !hashReady || active === null) return;
  const p = new URLSearchParams({ m: methods[active].m.key, v: currentView });
  if (selected) p.set("c", selected.c.name);
  try { history.replaceState(null, "", "#" + p.toString()); } catch (e) { /* e.g. sandboxed frame */ }
}
function applyHash() {
  const h = readHash();
  if (h.m && h.m !== methods[active]?.m.key) select(h.m);
  if (h.v && h.v !== currentView) flyTo(h.v, true);
  const cb = h.c && methods[active].cables.find(x => x.c.name === h.c);
  if (cb && cb !== selected) selectCable(cb);
  else if (!cb && selected) clearSelection();   // no (known) cable in the address: nothing selected
}
addEventListener("hashchange", applyHash);

// ---------------------------------------------------------------- language change: redraw every rendered text
function refreshTexts() {
  syncLang();
  [...tabsEl.children].forEach((b, i) => { b.textContent = methodLabel(methods[i].m); });
  renderLegend();
  for (const [key] of toggles) chipText[key].textContent = t("layer." + key);
  for (const [key, b] of Object.entries(viewBtns)) b.textContent = t("view." + key);
  speedTexts();
  for (const g of [viewsEl, speedEl]) placeThumb(g, true);   // button widths changed with the text
  setPlaying(Boolean(anim));
  syncFullscreen();
  for (const { el, name } of koLabels) el.textContent = t("keepout.label", { name: placeName(name) });
  for (const { el, name } of eqLabels) el.textContent = placeName(name);
  const view = methods[active];
  renderSummary(view.m);
  renderStats(view.m);
  renderCableTable(view);
  renderCompare();
  if (selected) renderSelinfo(selected);
  if (!anim) setProgress(view, Number(scrub.value) * view.cables.length);   // status line
  if (tourStep >= 0) tourShow(tourStep, false);
  requestRender();
}
I18N.onChange(refreshTexts);

// ---------------------------------------------------------------- guided tour (first visit; the ? button restarts it)
const TOUR = [["methods", "tabs"], ["view", "viewer"], ["play", "playbar"], ["toolbar", "toolbar"], ["table", "cabletable"]];
const tourPop = document.getElementById("tour-pop"), tourRing = document.getElementById("tour-ring");
const tourNext = document.getElementById("tour-next"), tourBack = document.getElementById("tour-back");
const tourBtn = document.getElementById("tour-btn");
let tourStep = -1;
function tourShow(i, scroll = true) {
  tourStep = i;
  const [key, id] = TOUR[i];
  document.getElementById("tour-title").textContent = t(`tour.${key}.title`);
  document.getElementById("tour-body").textContent = t(`tour.${key}.body`);
  document.getElementById("tour-count").textContent = t("tour.count", { i: i + 1, n: TOUR.length });
  document.getElementById("tour-dots").replaceChildren(...TOUR.map((_, k) => {
    const d = document.createElement("span");
    if (k === i) d.className = "on";
    return d;
  }));
  tourBack.hidden = i === 0;
  tourNext.textContent = t(i === TOUR.length - 1 ? "tour.done" : "tour.next");
  const opening = tourPop.hidden;   // appear in place; only moves between steps are animated
  for (const el of [tourPop, tourRing]) el.style.transition = opening ? "none" : "";
  tourPop.hidden = tourRing.hidden = false;
  if (scroll) {
    document.getElementById(id).scrollIntoView({ block: "center", behavior: REDUCED ? "auto" : "smooth" });
    tourNext.focus({ preventScroll: true });
  }
  placeTour();
  if (opening) requestAnimationFrame(() => { for (const el of [tourPop, tourRing]) el.style.transition = ""; });
}
function placeTour() {   // ring around the target; card below it, else above it, else inside its top edge
  if (tourStep < 0) return;
  const r = document.getElementById(TOUR[tourStep][1]).getBoundingClientRect();
  const vw = document.documentElement.clientWidth, vh = innerHeight, pad = 6, gap = 12, edge = 16;
  const top = Math.max(r.top - pad, 4), bottom = Math.min(r.bottom + pad, vh - 4);
  Object.assign(tourRing.style, { left: `${r.left - pad}px`, top: `${top}px`, width: `${r.width + 2 * pad}px`,
                                  height: `${Math.max(bottom - top, 0)}px` });
  const pw = tourPop.offsetWidth, ph = tourPop.offsetHeight;
  let y = r.bottom + gap;
  if (y + ph > vh - edge) y = r.top - gap - ph;
  if (y < edge) y = Math.min(vh - edge - ph, Math.max(edge, r.top + edge));
  tourPop.style.left = `${Math.min(Math.max(edge, r.left), vw - edge - pw)}px`;
  tourPop.style.top = `${y}px`;
}
function tourEnd() {
  if (tourStep < 0) return;
  tourStep = -1;
  tourPop.hidden = tourRing.hidden = true;
  try { localStorage.setItem("emc-kablo:tour", "done"); } catch (e) { /* shown again next visit */ }
}
tourNext.addEventListener("click", () => (tourStep < TOUR.length - 1 ? tourShow(tourStep + 1) : tourEnd()));
tourBack.addEventListener("click", () => tourShow(Math.max(0, tourStep - 1)));
document.getElementById("tour-close").addEventListener("click", tourEnd);
tourBtn.addEventListener("click", () => tourShow(0));
addEventListener("keydown", e => { if (e.key === "Escape" && tourStep >= 0) { tourEnd(); tourBtn.focus(); } });
let tourFrame = 0;   // follow scrolling and resizing at most once per frame
const followTour = () => { if (tourStep >= 0 && !tourFrame) tourFrame = requestAnimationFrame(() => { tourFrame = 0; placeTour(); }); };
addEventListener("scroll", followTour, { passive: true });
addEventListener("resize", followTour);

// ---------------------------------------------------------------- label decluttering
const hudEls = ["legend", "playbar", "status", "caption", "fullscreen"].map(id => document.getElementById(id));
function declutter() {   // hide labels that overlap a higher-priority label or the HUD, or leave the view
  if (!labels.visible) return;
  const box = host.getBoundingClientRect();
  const placed = hudEls.filter(el => el.offsetParent !== null && el.offsetWidth).map(el => el.getBoundingClientRect());
  for (const { el } of labelEls) {
    el.classList.remove("culled");
    if (el.style.display === "none" || !el.isConnected) continue;
    const r = el.getBoundingClientRect();
    if (!r.width) continue;
    const outside = r.left < box.left + 2 || r.right > box.right - 2 || r.top < box.top + 2 || r.bottom > box.bottom - 2;
    const overlap = placed.some(p => r.left < p.right + 3 && r.right > p.left - 3 && r.top < p.bottom + 2 && r.bottom > p.top - 2);
    if (outside || overlap) el.classList.add("culled");
    else placed.push(r);
  }
}

// ---------------------------------------------------------------- resize and on-demand rendering
function resize() {
  const w = host.clientWidth, h = host.clientHeight;
  renderer.setSize(w, h, false);
  labelRenderer.setSize(w, h);
  camera.aspect = w / h;
  // Keep roughly the same horizontal field of view on narrow (portrait) screens.
  const vfov = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(72) / 2) / camera.aspect);
  camera.fov = THREE.MathUtils.clamp(THREE.MathUtils.radToDeg(vfov), 42, 80);
  camera.updateProjectionMatrix();
  requestRender();
}

// A frame is drawn only when something changed (camera, playback, hover, toggles), not 60 times a second.
let frameQueued = false;
function requestRender() {
  if (frameQueued) return;
  frameQueued = true;
  requestAnimationFrame(loop);
}
controls.addEventListener("change", requestRender);
const ease = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
function loop(now) {
  frameQueued = false;
  let again = controls.autoRotate;
  if (tween) {
    const k = Math.min(1, (now - tween.start) / tween.dur), e = ease(k);
    camera.position.lerpVectors(tween.p0, tween.p1, e);
    controls.target.lerpVectors(tween.t0, tween.t1, e);
    if (k >= 1) tween = null;
    again = true;
  }
  if (anim) {
    // rAF timestamps can precede the performance.now() taken in play(): never go below 0.
    const n = anim.view.cables.length;
    const f = anim.from + Math.max(0, (now - anim.start) / 1000 / PER_CABLE * speed);
    if (f >= n) finish();
    else {
      setProgress(anim.view, f);
      if (actuatorArm && !REDUCED) actuatorArm.rotation.x = 0.55 * Math.sin(now / 1000 * 0.9);
      again = true;
    }
  }
  if (controls.update() && pointerPx) updateHover();   // the camera moved under a resting pointer
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
  declutter();
  if (again) requestRender();
}
new ResizeObserver(resize).observe(host);
resize();

// ---------------------------------------------------------------- scripting interface (README image capture)
function captionHtml(m) {
  const cls = v => (v ? "ng" : "ok");
  return `<b>${esc(methodLabel(m))}</b><br>EMC <span class="${cls(m.emc_points)}">${mark(!m.emc_points)} ` +
    `${fmt(m.emc_points, 0)} ${esc(t("unit.points"))}</span> · ${esc(t("caption.capacity"))} <span class="${cls(m.capacity_edges)}">` +
    `${mark(!m.capacity_edges)} ${m.capacity_edges} ${esc(t("unit.edges"))}</span> · ${esc(t("caption.bend"))} ` +
    `<span class="${cls(m.bend_points)}">${mark(!m.bend_points)} ${m.bend_points}</span> · ` +
    `${esc(t("caption.bundling"))} ${fmt(m.bundling_ratio, 3)}`;
}
const nextFrame = () => new Promise(r => { requestRender(); requestAnimationFrame(() => requestAnimationFrame(r)); });
window.viewer = {
  methods: methods.map(v => v.m.key),
  cableCount: D.cables.length,
  select(key) { select(key); document.getElementById("caption").innerHTML = captionHtml(methods[active].m); },
  view(key) { flyTo(key, true); },
  orbit(deg) {   // rotate the current preset camera about the vertical axis through its target
    const v = VIEWS[currentView], t = new THREE.Vector3(...v.target);
    const off = new THREE.Vector3(...v.pos).sub(t).applyAxisAngle(new THREE.Vector3(0, 0, 1), THREE.MathUtils.degToRad(deg));
    camera.position.copy(t).add(off); controls.target.copy(t); controls.update(); requestRender();
  },
  progress(f) { stop(); setProgress(methods[active], f); },   // f in [0, n]: cables drawn so far in routing order
  camera(pos, target, fov) {   // explicit camera for captures; fov keeps the horizontal field of view otherwise
    camera.position.set(...pos); controls.target.set(...target);
    if (fov) { camera.fov = fov; camera.updateProjectionMatrix(); }
    controls.update(); requestRender();
  },
  labels(mode) {   // "all" | "context" (volumes and equipment only) | "none"
    labels.visible = mode !== "none";
    cableLabels.visible = mode === "all";
    requestRender();
  },
  toggle: setToggle,
  lang(code) { I18N.set(code); },   // "tr" | "en"
  actuator(angle) { if (actuatorArm) { actuatorArm.rotation.x = angle; requestRender(); } },
  frame: nextFrame,
};

const initial = CAPTURE ? {} : readHash();
flyTo(initial.v || "side", true);   // opens outside: the whole fuselage and every cable in one view
if (actuatorArm) actuatorArm.rotation.x = 0.35;
select(initial.m || D.initial);   // all cables drawn; no autoplay
if (CAPTURE) window.viewer.select(D.initial);
hashReady = true;
applyHash();
document.getElementById("loading").remove();
window.__viewerReady = true;

let tourSeen = true;   // without storage the tour is not forced on every visit
try { tourSeen = localStorage.getItem("emc-kablo:tour") === "done"; } catch (e) { /* storage blocked */ }
if (!CAPTURE && !tourSeen) setTimeout(() => { if (tourStep < 0) tourShow(0); }, 1200);
