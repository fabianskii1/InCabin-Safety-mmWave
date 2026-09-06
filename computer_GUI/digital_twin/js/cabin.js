import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';

const FOLD_ANGLE = 1.48;
const SEATS = [
  { id: 'L', name: '뒷좌-좌', x: -0.36, w: 0.50 },
  { id: 'C', name: '뒷좌-중', x: 0.00, w: 0.34 },
  { id: 'R', name: '뒷좌-우', x: 0.36, w: 0.50 },
];
const sitPose = {
  L: { x: -0.36, y: 0.04, z: -0.70 },
  C: { x: 0.00, y: 0.04, z: -0.70 },
  R: { x: 0.36, y: 0.04, z: -0.70 },
};
let modelFold = null;

const ui = {
  foldLabel: document.getElementById('foldLabel'),
  foldBadge: document.getElementById('foldBadge'),
  foldVal: document.getElementById('foldVal'),
  stL: document.getElementById('stL'),
  stC: document.getElementById('stC'),
  stR: document.getElementById('stR'),
};

const occupied = { L: true, C: false, R: false };
const folded = { L: 0, C: 0, R: 0 };
const targetFold = { L: 0, C: 0, R: 0 };
const shake = { L: 0, C: 0, R: 0 };
let message = '뒷좌석에 사람이 있으면 폴딩 차단';

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x10151c);

const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.1, 40);
camera.position.set(1.28, 1.18, 1.05);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
document.getElementById('view').appendChild(renderer.domElement);

const HOME_POS = new THREE.Vector3(1.28, 1.18, 1.05);
const HOME_TARGET = new THREE.Vector3(0.02, 0.40, -0.48);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.enablePan = true;
controls.screenSpacePanning = true;
controls.minDistance = 0.45;
controls.maxDistance = 10;
controls.maxPolarAngle = Math.PI * 0.92;
controls.target.copy(HOME_TARGET);
controls.update();

let cabinRoot = null;
const moveKeys = {};
function resetView() {
  camera.position.copy(HOME_POS);
  controls.target.copy(HOME_TARGET);
  controls.update();
}

scene.add(new THREE.AmbientLight(0xb8c4d4, 0.7));
const key = new THREE.DirectionalLight(0xfff4e5, 1.05);
key.position.set(2.2, 3.2, 1.4);
key.castShadow = true;
scene.add(key);
const fill = new THREE.PointLight(0x7aa0c8, 12, 8, 2);
fill.position.set(0, 1.15, 0.2);
scene.add(fill);

function box(w, h, d, color, extra = {}) {
  return new THREE.Mesh(
    new THREE.BoxGeometry(w, h, d),
    new THREE.MeshStandardMaterial({ color, roughness: 0.7, ...extra }),
  );
}

function leatherMat(hex, stitch = true) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  g.fillStyle = hex;
  g.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 1200; i += 1) {
    const n = Math.floor(Math.random() * 18);
    g.fillStyle = `rgba(${n},${n},${n},${0.04 + Math.random() * 0.05})`;
    g.fillRect(Math.random() * 256, Math.random() * 256, 1 + Math.random() * 3, 1);
  }
  if (stitch) {
    g.strokeStyle = 'rgba(255,255,255,0.14)';
    g.lineWidth = 1.2;
    g.setLineDash([4, 5]);
    g.strokeRect(14, 14, 228, 228);
    g.beginPath();
    g.moveTo(128, 18);
    g.lineTo(128, 238);
    g.stroke();
  }
  const map = new THREE.CanvasTexture(c);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.repeat.set(1.2, 1.2);
  map.anisotropy = 4;
  return new THREE.MeshStandardMaterial({
    map,
    color: 0xffffff,
    roughness: 0.58,
    metalness: 0.06,
  });
}

const leather = leatherMat('#3a322e');
const leatherDark = leatherMat('#2a2421', false);
const plastic = new THREE.MeshStandardMaterial({ color: 0x1a1c1f, roughness: 0.45, metalness: 0.15 });
const metal = new THREE.MeshStandardMaterial({ color: 0x8a9098, roughness: 0.28, metalness: 0.7 });
const belt = new THREE.MeshStandardMaterial({ color: 0x2b241c, roughness: 0.7 });

function addMesh(parent, geo, mat, x, y, z, rx = 0, ry = 0, rz = 0) {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  m.rotation.set(rx, ry, rz);
  m.castShadow = true;
  m.receiveShadow = true;
  parent.add(m);
  return m;
}

const carpet = new THREE.MeshStandardMaterial({ color: 0x2c2a28, roughness: 0.95 });
const dashMat = new THREE.MeshStandardMaterial({ color: 0x16181c, roughness: 0.42, metalness: 0.12 });
const glassMat = new THREE.MeshStandardMaterial({
  color: 0x7ea8c4, transparent: true, opacity: 0.18, roughness: 0.08, metalness: 0.25,
});
const screenMat = new THREE.MeshStandardMaterial({
  color: 0x0b1220, emissive: 0x1a4d7a, emissiveIntensity: 0.35, roughness: 0.2,
});

function buildDoor(side) {
  const door = new THREE.Group();
  const s = side;
  addMesh(door, new THREE.BoxGeometry(0.07, 0.46, 2.35), dashMat, s * 0.93, 0.30, 0.0);
  addMesh(door, new THREE.BoxGeometry(0.10, 0.07, 2.35), leatherDark, s * 0.88, 0.50, 0.0);
  addMesh(door, new THREE.BoxGeometry(0.08, 0.06, 0.42), leather, s * 0.86, 0.40, 0.55);
  addMesh(door, new THREE.BoxGeometry(0.08, 0.06, 0.38), leather, s * 0.86, 0.40, -0.55);
  addMesh(door, new THREE.BoxGeometry(0.02, 0.04, 0.12), metal, s * 0.84, 0.48, 0.35);
  addMesh(door, new THREE.BoxGeometry(0.02, 0.04, 0.12), metal, s * 0.84, 0.48, -0.35);
  addMesh(door, new THREE.BoxGeometry(0.02, 0.38, 1.05), glassMat, s * 0.94, 0.72, 0.42);
  addMesh(door, new THREE.BoxGeometry(0.02, 0.38, 0.95), glassMat, s * 0.94, 0.72, -0.52);
  addMesh(door, new THREE.BoxGeometry(0.04, 0.62, 0.06), dashMat, s * 0.93, 0.42, 0.08);
  return door;
}

function buildCabin() {
  const cabin = new THREE.Group();

  addMesh(cabin, new THREE.BoxGeometry(1.92, 0.05, 2.75), carpet, 0, 0.0, 0.0);
  addMesh(cabin, new THREE.BoxGeometry(0.28, 0.10, 1.85), dashMat, 0, 0.08, 0.15);
  addMesh(cabin, new THREE.BoxGeometry(1.78, 0.16, 0.46), dashMat, 0, 0.20, 1.08);
  addMesh(cabin, new THREE.BoxGeometry(1.72, 0.10, 0.28), leatherDark, 0, 0.34, 1.02);
  addMesh(cabin, new THREE.BoxGeometry(0.36, 0.22, 0.04), screenMat, 0.08, 0.46, 0.92);
  addMesh(cabin, new THREE.BoxGeometry(0.28, 0.10, 0.08), dashMat, -0.38, 0.42, 0.92);
  addMesh(cabin, new THREE.BoxGeometry(0.08, 0.03, 0.08), plastic, -0.62, 0.36, 0.90);
  addMesh(cabin, new THREE.BoxGeometry(0.08, 0.03, 0.08), plastic, 0.52, 0.36, 0.90);
  addMesh(cabin, new THREE.BoxGeometry(0.42, 0.16, 0.22), dashMat, 0.58, 0.28, 1.02);

  const wheel = new THREE.Group();
  addMesh(wheel, new THREE.CylinderGeometry(0.03, 0.03, 0.22, 10), plastic, 0, 0, 0.08, Math.PI / 2, 0, 0);
  addMesh(wheel, new THREE.TorusGeometry(0.17, 0.022, 10, 28), plastic, 0, 0, 0);
  addMesh(wheel, new THREE.BoxGeometry(0.22, 0.02, 0.03), plastic, 0, 0, 0);
  addMesh(wheel, new THREE.BoxGeometry(0.02, 0.16, 0.03), plastic, 0, -0.05, 0);
  wheel.position.set(-0.38, 0.62, 0.80);
  wheel.rotation.x = -0.22;
  cabin.add(wheel);

  addMesh(cabin, new THREE.PlaneGeometry(1.72, 0.58), glassMat, 0, 0.78, 1.20, -0.38, 0, 0);
  addMesh(cabin, new THREE.BoxGeometry(0.05, 0.55, 0.05), dashMat, -0.86, 0.55, 1.12, 0, 0, 0.18);
  addMesh(cabin, new THREE.BoxGeometry(0.05, 0.55, 0.05), dashMat, 0.86, 0.55, 1.12, 0, 0, -0.18);

  addMesh(cabin, new THREE.BoxGeometry(0.32, 0.16, 0.95), dashMat, 0, 0.18, 0.18);
  addMesh(cabin, new THREE.BoxGeometry(0.28, 0.06, 0.22), leather, 0, 0.28, 0.02);
  addMesh(cabin, new THREE.CylinderGeometry(0.035, 0.035, 0.04, 12), plastic, -0.06, 0.29, 0.28);
  addMesh(cabin, new THREE.CylinderGeometry(0.035, 0.035, 0.04, 12), plastic, 0.06, 0.29, 0.28);
  addMesh(cabin, new THREE.BoxGeometry(0.22, 0.05, 0.28), leatherDark, 0, 0.30, -0.12);

  addMesh(cabin, new THREE.BoxGeometry(1.70, 0.08, 0.28), leatherDark, 0, 0.22, -1.18);
  addMesh(cabin, new THREE.BoxGeometry(1.55, 0.02, 0.22), carpet, 0, 0.27, -1.18);

  cabin.add(buildDoor(-1));
  cabin.add(buildDoor(1));
  cabinRoot = cabin;
  scene.add(cabin);
}

function makePerson() {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({
    color: 0xf2f2f4, roughness: 0.4, metalness: 0.02,
  });

  const ball = (r, x, y, z, sx = 1, sy = 1, sz = 1) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 18, 14), mat);
    m.position.set(x, y, z);
    m.scale.set(sx, sy, sz);
    m.castShadow = true;
    g.add(m);
    return m;
  };

  const bone = (ax, ay, az, bx, by, bz, radius) => {
    const a = new THREE.Vector3(ax, ay, az);
    const b = new THREE.Vector3(bx, by, bz);
    const dir = b.clone().sub(a);
    const dist = dir.length();
    const cyl = Math.max(0.002, dist - radius * 2);
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(radius, cyl, 6, 12), mat);
    m.position.copy(a).add(b).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
    m.castShadow = true;
    g.add(m);
  };

  bone(0, 0.14, 0.04, 0, 0.58, 0.00, 0.078);
  ball(0.11, 0, 0.76, 0.05);

  for (const s of [-1, 1]) {
    bone(s * 0.10, 0.16, 0.04, s * 0.10, 0.16, 0.42, 0.048);
    bone(s * 0.10, 0.16, 0.42, s * 0.10, -0.18, 0.46, 0.044);
    ball(0.048, s * 0.10, -0.20, 0.50, 1.25, 0.72, 1.45);
    bone(0, 0.54, 0.01, s * 0.18, 0.54, 0.02, 0.042);
    bone(s * 0.18, 0.54, 0.02, s * 0.13, 0.18, 0.32, 0.038);
  }

  const lock = new THREE.Mesh(
    new THREE.BoxGeometry(0.08, 0.012, 0.012),
    new THREE.MeshStandardMaterial({ color: 0xff4d4f, emissive: 0xff4d4f, emissiveIntensity: 0.85 }),
  );
  lock.position.set(0, 0.94, 0.06);
  lock.visible = false;
  lock.castShadow = true;
  g.add(lock);
  g.userData.lock = lock;
  return g;
}

function makeRearSeat(spec) {
  const seat = new THREE.Group();
  const w = spec.w;
  const cushion = addMesh(seat, new THREE.BoxGeometry(w * 0.92, 0.07, 0.40), leather, 0, 0.205, 0.04);
  cushion.scale.set(1, 1.15, 1);
  addMesh(seat, new THREE.BoxGeometry(w * 0.78, 0.035, 0.28), leatherDark, 0, 0.248, 0.06);
  addMesh(seat, new THREE.CapsuleGeometry(0.035, w * 0.72, 6, 10), leather, 0, 0.22, 0.22, 0, 0, Math.PI / 2);
  addMesh(seat, new THREE.CapsuleGeometry(0.038, 0.28, 6, 10), leather, -w * 0.42, 0.24, 0.02);
  addMesh(seat, new THREE.CapsuleGeometry(0.038, 0.28, 6, 10), leather, w * 0.42, 0.24, 0.02);
  addMesh(seat, new THREE.BoxGeometry(w, 0.06, 0.42), plastic, 0, 0.155, 0.02);

  const pivot = new THREE.Group();
  pivot.position.set(0, 0.24, -0.17);
  addMesh(pivot, new THREE.BoxGeometry(w * 0.90, 0.46, 0.07), leather, 0, 0.26, 0.01);
  addMesh(pivot, new THREE.BoxGeometry(w * 0.62, 0.16, 0.04), leatherDark, 0, 0.16, 0.04);
  addMesh(pivot, new THREE.CapsuleGeometry(0.03, 0.34, 6, 10), leather, -w * 0.40, 0.26, 0.03);
  addMesh(pivot, new THREE.CapsuleGeometry(0.03, 0.34, 6, 10), leather, w * 0.40, 0.26, 0.03);
  addMesh(pivot, new THREE.CylinderGeometry(0.008, 0.008, 0.08, 8), metal, -0.055, 0.50, 0.0);
  addMesh(pivot, new THREE.CylinderGeometry(0.008, 0.008, 0.08, 8), metal, 0.055, 0.50, 0.0);
  addMesh(pivot, new THREE.BoxGeometry(0.16, 0.09, 0.055), leather, 0, 0.58, 0.01);
  addMesh(pivot, new THREE.BoxGeometry(0.014, 0.38, 0.012), belt, w * 0.36, 0.28, 0.05);
  addMesh(pivot, new THREE.BoxGeometry(0.03, 0.04, 0.02), metal, w * 0.36, 0.08, 0.06);
  seat.add(pivot);

  const person = makePerson();
  person.position.set(0, 0.02, 0.06);
  seat.add(person);

  seat.userData = { pivot, person, lock: person.userData.lock };
  return seat;
}

function makeSeat(color, withPerson) {
  const seat = new THREE.Group();
  const cushion = box(0.42, 0.1, 0.42, color);
  cushion.position.y = 0.22;
  cushion.castShadow = true;
  seat.add(cushion);

  const pivot = new THREE.Group();
  pivot.position.set(0, 0.27, -0.16);
  const back = box(0.42, 0.48, 0.09, color);
  back.position.set(0, 0.24, 0);
  back.castShadow = true;
  pivot.add(back);
  seat.add(pivot);

  const person = makePerson(withPerson ? 'L' : 'C');
  person.position.set(0, 0.08, 0.02);
  seat.add(person);

  seat.userData = { pivot, person, lock: person.userData.lock };
  if (!withPerson) person.visible = false;
  person.userData.lock.visible = false;
  return seat;
}

buildCabin();

const frontL = makeSeat(0x2f3a48, true);
frontL.position.set(-0.38, 0, 0.55);
scene.add(frontL);
const frontR = makeSeat(0x2f3a48, false);
frontR.position.set(0.38, 0, 0.55);
scene.add(frontR);

const bench = new THREE.Group();
addMesh(bench, new THREE.BoxGeometry(1.58, 0.05, 0.48), plastic, 0, 0.13, -0.72);
addMesh(bench, new THREE.BoxGeometry(1.58, 0.22, 0.06), leatherDark, 0, 0.28, -0.96);
scene.add(bench);

const rear = {};
for (const spec of SEATS) {
  const seat = makeRearSeat(spec);
  seat.position.set(spec.x, 0, -0.70);
  scene.add(seat);
  rear[spec.id] = seat;
}

function enableShadows(root) {
  root.traverse((obj) => {
    if (obj.isMesh) {
      obj.castShadow = true;
      obj.receiveShadow = true;
    }
  });
}

function cleanSketchUpScene(root) {
  const drop = [];
  root.traverse((obj) => {
    if (obj.isLine || obj.isLineSegments || obj.isLineLoop || obj.isPoints) {
      drop.push(obj);
      return;
    }
    if (!obj.isMesh || !obj.material) return;
    const list = Array.isArray(obj.material) ? obj.material : [obj.material];
    const next = list.map((mat) => {
      if (!mat) return mat;
      const map = mat.map || null;
      if (map) map.colorSpace = THREE.SRGBColorSpace;
      return new THREE.MeshStandardMaterial({
        map,
        color: map ? 0xffffff : (mat.color ? mat.color.clone() : new THREE.Color(0x6b6460)),
        roughness: 0.72,
        metalness: 0.04,
        side: THREE.DoubleSide,
        transparent: Boolean(mat.transparent),
        opacity: mat.opacity == null ? 1 : mat.opacity,
        vertexColors: Boolean(mat.vertexColors),
        wireframe: false,
      });
    });
    obj.material = Array.isArray(obj.material) ? next : next[0];
    obj.castShadow = true;
    obj.receiveShadow = true;
  });
  for (const obj of drop) {
    if (obj.parent) obj.parent.remove(obj);
  }
}

function occupancyOnly(seat) {
  for (const child of [...seat.children]) {
    if (child !== seat.userData.person && child !== seat.userData.lock) {
      child.visible = false;
    }
  }
}

function fitSeatModel(src) {
  const model = src.clone(true);
  enableShadows(model);
  const wrap = new THREE.Group();
  wrap.add(model);
  const box = new THREE.Box3().setFromObject(wrap);
  const size = box.getSize(new THREE.Vector3());
  wrap.scale.setScalar(0.90 / Math.max(size.y, 0.001));
  const box2 = new THREE.Box3().setFromObject(wrap);
  const center = box2.getCenter(new THREE.Vector3());
  wrap.position.x -= center.x;
  wrap.position.z -= center.z;
  wrap.position.y -= box2.min.y;
  return wrap;
}

function wrapModelSeat(visual, withPerson) {
  const seat = new THREE.Group();
  const pivot = new THREE.Group();
  pivot.position.set(0, 0.22, -0.18);
  visual.position.set(0, -0.22, 0.18);
  pivot.add(visual);
  seat.add(pivot);
  const person = makePerson('C');
  person.position.set(0, 0.10, 0.04);
  seat.add(person);
  seat.userData = { pivot, person, lock: person.userData.lock };
  if (!withPerson) person.visible = false;
  person.userData.lock.visible = false;
  return seat;
}

function replaceWithModelSeats(gltfScene) {
  const template = fitSeatModel(gltfScene);
  scene.remove(frontL);
  scene.remove(frontR);
  const fl = wrapModelSeat(template.clone(true), true);
  fl.position.set(-0.38, 0, 0.55);
  scene.add(fl);
  const fr = wrapModelSeat(template.clone(true), false);
  fr.position.set(0.38, 0, 0.55);
  scene.add(fr);
  for (const spec of SEATS) {
    scene.remove(rear[spec.id]);
    const seat = wrapModelSeat(template.clone(true), occupied[spec.id]);
    seat.scale.set(spec.id === 'C' ? 0.86 : 1, 1, 1);
    seat.position.set(spec.x, 0, -0.70);
    scene.add(seat);
    rear[spec.id] = seat;
  }
  message = 'Foulques 시트 모델로 5석 배치';
}

function findNamed(root, name) {
  let found = null;
  root.traverse((obj) => {
    if (obj.name === name) found = obj;
  });
  return found;
}

function clearModelFold() {
  if (!modelFold) return;
  if (modelFold.pivot && modelFold.pivot.parent) {
    modelFold.pivot.parent.remove(modelFold.pivot);
  }
  modelFold = null;
}

function meshWorldBox(mesh) {
  mesh.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(mesh);
}

function cabinFrontDir(interior, rearCenter) {
  const frontCenters = [];
  interior.traverse((obj) => {
    if (obj.name === 'Seat_5') {
      frontCenters.push(new THREE.Box3().setFromObject(obj).getCenter(new THREE.Vector3()));
    }
  });
  if (frontCenters.length) {
    const avg = frontCenters[0].clone();
    for (let i = 1; i < frontCenters.length; i += 1) avg.add(frontCenters[i]);
    avg.multiplyScalar(1 / frontCenters.length);
    const dir = avg.sub(rearCenter);
    dir.y = 0;
    if (dir.lengthSq() > 1e-6) return dir.normalize();
  }
  const dash = findNamed(interior, 'Dashboard');
  if (dash) {
    const dir = new THREE.Box3().setFromObject(dash).getCenter(new THREE.Vector3()).sub(rearCenter);
    dir.y = 0;
    if (dir.lengthSq() > 1e-6) return dir.normalize();
  }
  return new THREE.Vector3(-1, 0, 0);
}

function foldBasisQuat(frontDir) {
  const yAxis = new THREE.Vector3(0, 1, 0);
  const xAxis = new THREE.Vector3().crossVectors(yAxis, frontDir).normalize();
  const zAxis = new THREE.Vector3().crossVectors(xAxis, yAxis).normalize();
  return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(xAxis, yAxis, zAxis));
}

function geometryFromVertexList(src, indices) {
  const dst = new THREE.BufferGeometry();
  for (const name of Object.keys(src.attributes)) {
    const attr = src.attributes[name];
    const itemSize = attr.itemSize;
    const srcArr = attr.array;
    const data = new srcArr.constructor(indices.length * itemSize);
    for (let i = 0; i < indices.length; i += 1) {
      const from = indices[i] * itemSize;
      const to = i * itemSize;
      for (let k = 0; k < itemSize; k += 1) data[to + k] = srcArr[from + k];
    }
    dst.setAttribute(name, new THREE.BufferAttribute(data, itemSize, attr.normalized));
  }
  return dst;
}

function makeSubMesh(srcMesh, geom) {
  const m = new THREE.Mesh(geom, srcMesh.material);
  m.castShadow = srcMesh.castShadow;
  m.receiveShadow = srcMesh.receiveShadow;
  srcMesh.parent.add(m);
  m.position.copy(srcMesh.position);
  m.quaternion.copy(srcMesh.quaternion);
  m.scale.copy(srcMesh.scale);
  m.updateMatrixWorld(true);
  return m;
}

function splitMeshByPoint(mesh, keyOfWorldPoint) {
  mesh.updateMatrixWorld(true);
  const src = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry;
  const pos = src.attributes.position;
  if (!pos || pos.count < 3) return {};
  const mw = mesh.matrixWorld;
  const va = new THREE.Vector3();
  const vb = new THREE.Vector3();
  const vc = new THREE.Vector3();
  const buckets = new Map();
  for (let i = 0; i < pos.count; i += 3) {
    va.set(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(mw);
    vb.set(pos.getX(i + 1), pos.getY(i + 1), pos.getZ(i + 1)).applyMatrix4(mw);
    vc.set(pos.getX(i + 2), pos.getY(i + 2), pos.getZ(i + 2)).applyMatrix4(mw);
    const key = keyOfWorldPoint(
      (va.x + vb.x + vc.x) / 3,
      (va.y + vb.y + vc.y) / 3,
      (va.z + vb.z + vc.z) / 3,
    );
    let arr = buckets.get(key);
    if (!arr) {
      arr = [];
      buckets.set(key, arr);
    }
    arr.push(i, i + 1, i + 2);
  }
  if (buckets.size === 1) {
    const only = [...buckets.keys()][0];
    return { [only]: mesh };
  }
  const out = {};
  for (const [key, idx] of buckets) {
    out[key] = makeSubMesh(mesh, geometryFromVertexList(src, idx));
  }
  mesh.visible = false;
  return out;
}

function creaseHinge(seatBox, frontDir) {
  const size = seatBox.getSize(new THREE.Vector3());
  const center = seatBox.getCenter(new THREE.Vector3());
  const seatLen = Math.abs(frontDir.x) * size.x + Math.abs(frontDir.z) * size.z;
  const hinge = center.clone();
  hinge.y = seatBox.min.y + size.y * 0.30;
  hinge.addScaledVector(frontDir, -seatLen * 0.16);
  return hinge;
}

function isBackPoint(x, y, z, hinge, frontDir) {
  const along = (x - hinge.x) * frontDir.x + (z - hinge.z) * frontDir.z;
  const up = y - hinge.y;
  return up > along * 0.30 + 0.02;
}

function meshFoldKind(mesh, seatBox, frontDir, hinge) {
  const b = meshWorldBox(mesh);
  const c = b.getCenter(new THREE.Vector3());
  const h = b.max.y - b.min.y;
  const seatH = Math.max(0.01, seatBox.max.y - seatBox.min.y);
  const seatLen = Math.abs(frontDir.x) * (seatBox.max.x - seatBox.min.x)
    + Math.abs(frontDir.z) * (seatBox.max.z - seatBox.min.z);
  const depth = Math.abs(frontDir.x) * (b.max.x - b.min.x)
    + Math.abs(frontDir.z) * (b.max.z - b.min.z);
  const relY = (c.y - seatBox.min.y) / seatH;
  if (h > seatH * 0.52 && depth > seatLen * 0.38) return 'fused';
  if (relY > 0.42 || (h > depth * 1.2 && relY > 0.30)) return 'back';
  if (relY < 0.38 && depth > h * 0.8) return 'cushion';
  return 'fused';
}

function collectBackrestParts(seat6, seatBox, frontDir, hinge) {
  const meshes = [];
  seat6.traverse((obj) => {
    if (obj.isMesh && obj.visible) meshes.push(obj);
  });
  const backs = [];
  for (const mesh of meshes) {
    const kind = meshFoldKind(mesh, seatBox, frontDir, hinge);
    if (kind === 'cushion') continue;
    if (kind === 'back') {
      backs.push(mesh);
      continue;
    }
    const parts = splitMeshByPoint(mesh, (x, y, z) => (
      isBackPoint(x, y, z, hinge, frontDir) ? 'back' : 'cushion'
    ));
    if (parts.back) backs.push(parts.back);
  }
  return backs;
}

function rigRearBenchFold(interior) {
  clearModelFold();
  interior.updateMatrixWorld(true);
  const seat6 = findNamed(interior, 'Seat_6') || findNamed(interior, 'instance_3');
  if (!seat6) {
    console.warn('[cabin] Seat_6 not found — cannot rig rear fold');
    return;
  }

  const box = new THREE.Box3().setFromObject(seat6);
  const center = box.getCenter(new THREE.Vector3());
  const frontDir = cabinFrontDir(interior, center);
  const hinge = creaseHinge(box, frontDir);
  const backs = collectBackrestParts(seat6, box, frontDir, hinge);
  if (!backs.length) {
    console.warn('[cabin] no rear backrest meshes');
    return;
  }

  const baseQuat = foldBasisQuat(frontDir);
  const pivot = new THREE.Group();
  pivot.name = 'rearFoldPivot';
  pivot.position.copy(hinge);
  pivot.quaternion.copy(baseQuat);
  scene.add(pivot);
  for (const mesh of backs) pivot.attach(mesh);
  modelFold = { pivot, baseQuat };
  console.info('[cabin] rear fold: backrest only', { parts: backs.length, frontDir, hinge });
}

function fitInteriorModel(root) {
  cleanSketchUpScene(root);
  enableShadows(root);
  root.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(root);
  let size = box.getSize(new THREE.Vector3());
  if (size.z > size.y * 1.3 && size.z > size.x) {
    root.rotation.x = -Math.PI / 2;
    root.updateMatrixWorld(true);
    box = new THREE.Box3().setFromObject(root);
    size = box.getSize(new THREE.Vector3());
  }
  const width = Math.min(size.x, size.z);
  root.scale.multiplyScalar(1.85 / Math.max(width, 0.001));
  root.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(root);
  const center = box.getCenter(new THREE.Vector3());
  root.position.x -= center.x;
  root.position.z -= center.z;
  box = new THREE.Box3().setFromObject(root);
  root.position.y -= box.min.y;
}

function orientPeopleForward(interior, origin) {
  const frontDir = cabinFrontDir(interior, origin);
  if (frontDir.lengthSq() < 1e-6) return;
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), frontDir);
  for (const spec of SEATS) rear[spec.id].quaternion.copy(q);
}

function placePeopleOnRearBench(interior) {
  interior.updateMatrixWorld(true);
  const rearMesh = findNamed(interior, 'Seat_6');
  if (rearMesh) {
    const rb = new THREE.Box3().setFromObject(rearMesh);
    const rc = rb.getCenter(new THREE.Vector3());
    const rw = rb.max.x - rb.min.x;
    const rd = rb.max.z - rb.min.z;
    const ySit = rb.min.y + (rb.max.y - rb.min.y) * 0.18;
    const alongX = rw >= rd;
    const minW = alongX ? rb.min.x : rb.min.z;
    const maxW = alongX ? rb.max.x : rb.max.z;
    const t = [0.05, 0.50, 0.88];
    SEATS.forEach((spec, i) => {
      occupancyOnly(rear[spec.id]);
      const w = minW + (maxW - minW) * t[i];
      const x = alongX ? w : rc.x;
      const z = alongX ? rc.z : w;
      sitPose[spec.id] = { x, y: ySit, z };
      rear[spec.id].position.set(x, ySit, z);
    });
    orientPeopleForward(interior, rc);
    return;
  }

  const box = new THREE.Box3().setFromObject(interior);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const dash = findNamed(interior, 'Dashboard');
  const dashPos = new THREE.Vector3();
  if (dash) dash.getWorldPosition(dashPos);
  else dashPos.copy(center);

  const lengthIsZ = size.z >= size.x;
  const ySit = box.min.y + Math.min(0.38, size.y * 0.26);
  const lane = (lengthIsZ ? size.x : size.z) * 0.20;
  const lanes = [-lane, 0, lane];

  SEATS.forEach((spec, i) => {
    occupancyOnly(rear[spec.id]);
    let x;
    let z;
    if (lengthIsZ) {
      const rearIsMin = dashPos.z > center.z;
      z = rearIsMin ? box.min.z + size.z * 0.18 : box.max.z - size.z * 0.18;
      x = lanes[i];
    } else {
      const rearIsMin = dashPos.x > center.x;
      x = rearIsMin ? box.min.x + size.x * 0.18 : box.max.x - size.x * 0.18;
      z = lanes[i];
    }
    sitPose[spec.id] = { x, y: ySit, z };
    rear[spec.id].position.set(x, ySit, z);
  });
  orientPeopleForward(interior, center);
}

function applyCarInterior(sceneRoot) {
  fitInteriorModel(sceneRoot);
  if (cabinRoot) scene.remove(cabinRoot);
  scene.remove(frontL);
  scene.remove(frontR);
  scene.remove(bench);
  scene.add(sceneRoot);
  rigRearBenchFold(sceneRoot);
  placePeopleOnRearBench(sceneRoot);
  message = '뒷좌석 등받이 폴딩 준비';
}

function loadCarInterior() {
  const dae = new ColladaLoader();
  dae.load(
    'models/car_interior/model.dae',
    (file) => applyCarInterior(file.scene),
    undefined,
    () => loadSketchfabSeat(),
  );
}

function loadSketchfabSeat() {
  const gltf = new GLTFLoader();
  const dae = new ColladaLoader();
  const jobs = [
    ['gltf', 'models/car-seat.glb'],
    ['gltf', 'models/scene.gltf'],
    ['gltf', 'models/car_seat.glb'],
    ['dae', 'models/car-seat.dae'],
    ['dae', 'models/scene.dae'],
  ];
  const tryNext = (i) => {
    if (i >= jobs.length) {
      console.warn('Seat model missing. Use models/car-seat.glb or models/scene.dae');
      return;
    }
    const [kind, path] = jobs[i];
    const onOk = (root) => replaceWithModelSeats(root);
    const onFail = () => tryNext(i + 1);
    if (kind === 'gltf') {
      gltf.load(path, (file) => onOk(file.scene), undefined, onFail);
    } else {
      dae.load(path, (file) => onOk(file.scene), undefined, onFail);
    }
  };
  tryNext(0);
}

loadCarInterior();

function anyoneRear() {
  return occupied.L || occupied.C || occupied.R;
}

function foldAllowed() {
  return !anyoneRear();
}

function setOccupied(id, value) {
  occupied[id] = value;
  if (value) {
    for (const key of Object.keys(targetFold)) {
      targetFold[key] = 0;
      folded[key] = Math.min(folded[key], 0.15);
    }
  }
}

function toggleSeat(id) {
  setOccupied(id, !occupied[id]);
  message = occupied[id]
    ? `${SEATS.find((s) => s.id === id).name} 탑승 → 폴딩 잠금`
    : `${SEATS.find((s) => s.id === id).name} 비움`;
}

function tryFold() {
  if (!foldAllowed()) {
    message = '사람이 감지되어 뒷좌석 폴딩을 차단했습니다';
    for (const id of Object.keys(occupied)) {
      if (occupied[id]) shake[id] = 0.35;
    }
    return;
  }
  for (const id of Object.keys(targetFold)) targetFold[id] = FOLD_ANGLE;
  message = '뒷좌석 비어 있음 → 폴딩 허용';
}

function unfoldAll() {
  for (const id of Object.keys(targetFold)) targetFold[id] = 0;
  message = '시트를 펼쳤습니다';
}

function updateHud() {
  const map = { L: ui.stL, C: ui.stC, R: ui.stR };
  for (const spec of SEATS) {
    map[spec.id].textContent = occupied[spec.id] ? '탑승' : '비움';
  }
  const allowed = foldAllowed();
  ui.foldVal.textContent = allowed ? '허용' : '차단';
  ui.foldLabel.textContent = message;
  ui.foldBadge.textContent = allowed ? 'EMPTY · FOLD OK' : 'OCCUPIED · LOCK';
  ui.foldBadge.className = 'badge ' + (allowed ? 'ok' : 'danger');

  for (const spec of SEATS) {
    const btn = document.getElementById('btn' + spec.id);
    btn.classList.toggle('on', occupied[spec.id]);
    btn.classList.toggle('locked', occupied[spec.id]);
  }
  document.getElementById('btnFold').classList.toggle('locked', !allowed);
}

document.getElementById('btnL').onclick = () => toggleSeat('L');
document.getElementById('btnC').onclick = () => toggleSeat('C');
document.getElementById('btnR').onclick = () => toggleSeat('R');
document.getElementById('btnEmpty').onclick = () => {
  setOccupied('L', false); setOccupied('C', false); setOccupied('R', false);
  message = '뒷좌석 전원 비움 → 폴딩 가능';
};
document.getElementById('btnOccupy').onclick = () => {
  setOccupied('L', true); setOccupied('C', true); setOccupied('R', true);
  message = '뒷좌석 전원 탑승 → 폴딩 차단';
};
document.getElementById('btnFold').onclick = tryFold;
document.getElementById('btnUnfold').onclick = unfoldAll;
document.getElementById('btnView').onclick = resetView;

function onKey(e, down) {
  const k = e.key.toLowerCase();
  if (['w', 'a', 's', 'd', 'q', 'z', 'shift'].includes(k)) {
    moveKeys[k] = down;
    e.preventDefault();
  }
  if (!down) return;
  if (k === '1') toggleSeat('L');
  if (k === '2') toggleSeat('C');
  if (k === '3') toggleSeat('R');
  if (k === 'f') tryFold();
  if (k === 'u') unfoldAll();
  if (k === 'x') document.getElementById('btnEmpty').click();
  if (e.key === 'Home') resetView();
}
window.addEventListener('keydown', (e) => onKey(e, true));
window.addEventListener('keyup', (e) => onKey(e, false));

function flyCamera(dt) {
  const speed = (moveKeys.shift ? 2.6 : 1.15) * dt;
  const forward = new THREE.Vector3();
  camera.getWorldDirection(forward);
  const right = new THREE.Vector3().crossVectors(forward, camera.up).normalize();
  const delta = new THREE.Vector3();
  if (moveKeys.w) delta.add(forward);
  if (moveKeys.s) delta.sub(forward);
  if (moveKeys.a) delta.sub(right);
  if (moveKeys.d) delta.add(right);
  if (moveKeys.q) delta.y += 1;
  if (moveKeys.z) delta.y -= 1;
  if (delta.lengthSq() === 0) return;
  delta.normalize().multiplyScalar(speed);
  camera.position.add(delta);
  controls.target.add(delta);
}

window.addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const clock = new THREE.Clock();
function tick() {
  const dt = Math.min(0.05, clock.getDelta());
  for (const spec of SEATS) {
    const id = spec.id;
    const seat = rear[id];
    folded[id] += (targetFold[id] - folded[id]) * Math.min(1, dt * 5);
    shake[id] = Math.max(0, shake[id] - dt);
    const wobble = shake[id] > 0 ? Math.sin(clock.elapsedTime * 28) * 0.08 : 0;
    seat.userData.pivot.rotation.x = folded[id] + wobble;
    seat.userData.person.visible = occupied[id];
    seat.userData.lock.visible = occupied[id];
    const pose = sitPose[id];
    seat.position.set(
      pose.x + (shake[id] > 0 ? Math.sin(clock.elapsedTime * 40) * 0.012 : 0),
      pose.y,
      pose.z,
    );
  }
  if (modelFold) {
    const ang = (folded.L + folded.C + folded.R) / 3;
    const lockWobble = Math.max(shake.L, shake.C, shake.R) > 0
      ? Math.sin(clock.elapsedTime * 28) * 0.04
      : 0;
    const qFold = new THREE.Quaternion().setFromAxisAngle(
      new THREE.Vector3(1, 0, 0),
      ang + lockWobble,
    );
    modelFold.pivot.quaternion.copy(modelFold.baseQuat).multiply(qFold);
  }
  flyCamera(dt);
  controls.update();
  updateHud();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}
tick();
