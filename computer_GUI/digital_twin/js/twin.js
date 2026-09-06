import * as THREE from 'three';

const LANE_W = 3.6;
const LANES = [-LANE_W, 0, LANE_W];
const EGO_LANE = 1;
const SHOULDER_X = 7.6;
const V0 = 22.2;
const CRAWL = 8.0;
const LANE_CHANGE_SEC = 3.4;
const SHOULDER_MERGE_SEC = 5.6;
const HAZARD_HZ = 1.5;
const CAR_LEN = 4.3;
const TRAFFIC_COUNT = 14;

const ui = {
  phase: document.getElementById('phaseLabel'),
  badge: document.getElementById('statusBadge'),
  speed: document.getElementById('speedVal'),
  rr: document.getElementById('rrVal'),
  hr: document.getElementById('hrVal'),
  apnea: document.getElementById('apneaVal'),
  gap: document.getElementById('gapVal'),
};

const SUB_LABELS = {
  CRUISE: '정상 주행',
  DECEL: '비상등 · 감속 중',
  WAIT: '옆 차선 차량 통과 대기',
  CHANGE: '차선 변경 중',
  SHOULDER: '갓길 진입 중',
  PARK: '갓길 정차 완료',
};

const sensor = {
  status: 'NORMAL',
  present: true,
  rr: 14,
  hr: 72,
  apnea_sec: 0,
  detail: '',
  source: 'mock',
};

let phase = 'CRUISE';
let sub = 'CRUISE';
let speed = V0;
let carX = LANES[EGO_LANE];
let carZ = 0;
let egoLane = EGO_LANE;
let moveQueue = [];
let currentMove = null;
let onShoulder = false;
let leadGap = Infinity;
let lastSensorCmd = null;
let carYaw = 0;
let prevCarX = LANES[EGO_LANE];

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x071018);
scene.fog = new THREE.Fog(0x071018, 70, 300);

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 500);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
document.getElementById('view').appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0x6d7c93, 0.55));
const moon = new THREE.DirectionalLight(0xb7c7de, 0.7);
moon.position.set(-20, 40, 10);
moon.castShadow = true;
scene.add(moon);

const TILE_LEN = 80;
const TILE_COUNT = 8;
const TILE_SPAN = TILE_LEN * TILE_COUNT;
const LAMP_GAP = 18;

function makeRoadTexture() {
  const c = document.createElement('canvas');
  c.width = 256;
  c.height = 512;
  const g = c.getContext('2d');
  g.fillStyle = '#2a2e33';
  g.fillRect(0, 0, 256, 512);

  g.fillStyle = '#d8dde4';
  g.fillRect(5, 0, 6, 512);
  g.fillRect(245, 0, 6, 512);

  g.strokeStyle = '#d8dde4';
  g.lineWidth = 6;
  g.setLineDash([30, 26]);
  for (const u of [86, 170]) {
    g.beginPath();
    g.moveTo(u, 0);
    g.lineTo(u, 512);
    g.stroke();
  }

  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, TILE_LEN / 12);
  tex.anisotropy = 8;
  return tex;
}

const roadMat = new THREE.MeshStandardMaterial({ map: makeRoadTexture(), roughness: 0.92 });
const shoulderMat = new THREE.MeshStandardMaterial({ color: 0x4a4336, roughness: 1 });
const grassMat = new THREE.MeshStandardMaterial({ color: 0x102214, roughness: 1 });
const poleMat = new THREE.MeshStandardMaterial({ color: 0x22262c });
const railMat = new THREE.MeshStandardMaterial({ color: 0x9aa3ad, roughness: 0.4, metalness: 0.6 });
const medianMat = new THREE.MeshStandardMaterial({ color: 0x3c4148, roughness: 0.85 });

const worldTiles = [];
const worldProps = [];

function addTile(z) {
  const group = new THREE.Group();
  group.position.z = z;

  const road = new THREE.Mesh(new THREE.PlaneGeometry(11, TILE_LEN), roadMat);
  road.rotation.x = -Math.PI / 2;
  road.receiveShadow = true;
  group.add(road);

  const shoulder = new THREE.Mesh(new THREE.PlaneGeometry(5.2, TILE_LEN), shoulderMat);
  shoulder.rotation.x = -Math.PI / 2;
  shoulder.position.set(8.1, -0.01, 0);
  shoulder.receiveShadow = true;
  group.add(shoulder);

  const grass = new THREE.Mesh(new THREE.PlaneGeometry(80, TILE_LEN), grassMat);
  grass.rotation.x = -Math.PI / 2;
  grass.position.y = -0.04;
  grass.receiveShadow = true;
  group.add(grass);

  const rail = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.34, TILE_LEN), railMat);
  rail.position.set(10.8, 0.66, 0);
  group.add(rail);

  const median = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.72, TILE_LEN), medianMat);
  median.position.set(-6.1, 0.36, 0);
  group.add(median);

  scene.add(group);
  worldTiles.push(group);
}

function addLamp(z) {
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.1, 6, 8), poleMat);
  pole.position.set(-7.1, 3, z);
  scene.add(pole);
  const lamp = new THREE.PointLight(0xffd9a0, 8, 26, 2);
  lamp.position.set(-6.2, 5.8, z);
  scene.add(lamp);
  worldProps.push(pole, lamp);
}

for (let i = 0; i < TILE_COUNT; i += 1) {
  addTile((i - 2) * TILE_LEN);
}
for (let z = -2 * TILE_LEN; z < (TILE_COUNT - 2) * TILE_LEN; z += LAMP_GAP) {
  addLamp(z);
}

function recycleAlongZ(objects, behind) {
  for (const obj of objects) {
    while (obj.position.z < behind) obj.position.z += TILE_SPAN;
    while (obj.position.z > behind + TILE_SPAN) obj.position.z -= TILE_SPAN;
  }
}

function recycleWorld() {
  const behind = carZ - TILE_LEN * 2;
  recycleAlongZ(worldTiles, behind);
  recycleAlongZ(worldProps, behind);
}

function addBox(parent, w, h, d, x, y, z, color, extra = {}) {
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(w, h, d),
    new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.15, ...extra }),
  );
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  parent.add(mesh);
  return mesh;
}

function buildVehicle(color, len, tall) {
  const g = new THREE.Group();
  const bodyH = tall ? 0.78 : 0.52;
  const bodyY = 0.30 + bodyH / 2;
  addBox(g, 1.85, bodyH, len, 0, bodyY, 0, color);
  const cabinH = tall ? 0.80 : 0.48;
  addBox(
    g, 1.70, cabinH, tall ? 3.1 : 2.1,
    0, bodyY + bodyH / 2 + cabinH / 2 - 0.05, -0.15,
    0x6e8798, { transparent: true, opacity: 0.55 },
  );
  addBox(g, 1.82, 0.12, 0.18, 0, 0.58, len / 2 - 0.02, 0xf4f1c8, {
    emissive: 0xf4f1c8, emissiveIntensity: 0.4,
  });
  const brake = addBox(g, 1.84, 0.12, 0.14, 0, 0.58, -len / 2 + 0.02, 0x8a1515, {
    emissive: 0xff2a2a, emissiveIntensity: 0.25,
  });

  const wheels = [];
  const wMat = new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.8 });
  const wz = len / 2 - 1.0;
  for (const [x, z] of [[0.82, wz], [-0.82, wz], [0.82, -wz], [-0.82, -wz]]) {
    const w = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.22, 16), wMat);
    w.rotation.z = Math.PI / 2;
    w.position.set(x, 0.32, z);
    g.add(w);
    wheels.push(w);
  }

  g.userData = { brake, wheels, len };
  scene.add(g);
  return g;
}

function makeEgo() {
  const g = buildVehicle(0xdfe4ea, CAR_LEN, false);
  const lamp = (x, z) => addBox(g, 0.16, 0.12, 0.16, x, 0.62, z, 0xff9f1c, {
    emissive: 0xff9f1c, emissiveIntensity: 0,
  });
  g.userData.hazards = [lamp(0.9, 1.9), lamp(-0.9, 1.9), lamp(0.9, -1.95), lamp(-0.9, -1.95)];

  const marker = new THREE.Mesh(
    new THREE.RingGeometry(2.3, 2.6, 40),
    new THREE.MeshBasicMaterial({
      color: 0x39b6ff, transparent: true, opacity: 0.35, side: THREE.DoubleSide,
    }),
  );
  marker.rotation.x = -Math.PI / 2;
  marker.position.y = 0.03;
  g.add(marker);
  return g;
}

const car = makeEgo();

const TRAFFIC_PALETTE = [0x9aa7b6, 0xb2554e, 0x4f7a58, 0xc2a24c, 0x63588a, 0x3f5f86, 0xa9adb4];
const traffic = [];

function laneBase(lane) {
  return [26.5, 23.0, 19.5][lane] + (Math.random() * 2.0 - 1.0);
}

function slotFree(lane, z, gap, self) {
  for (const t of traffic) {
    if (t === self || t.lane !== lane) continue;
    if (Math.abs(t.z - z) < gap) return false;
  }
  return true;
}

function placeTraffic(t, z) {
  for (let i = 0; i < 20; i += 1) {
    const lane = Math.floor(Math.random() * 3);
    const zz = z + (Math.random() * 44 - 22);
    if (Math.abs(zz - carZ) < 40) continue;
    if (!slotFree(lane, zz, 30, t)) continue;
    t.lane = lane;
    t.z = zz;
    t.base = laneBase(lane);
    t.speed = t.base;
    return;
  }
  t.lane = Math.floor(Math.random() * 3);
  t.z = z;
  t.base = laneBase(t.lane);
  t.speed = t.base;
}

function spawnTraffic() {
  for (const t of traffic) scene.remove(t.mesh);
  traffic.length = 0;
  for (let i = 0; i < TRAFFIC_COUNT; i += 1) {
    const tall = Math.random() < 0.25;
    const len = tall ? 5.4 : CAR_LEN;
    const color = TRAFFIC_PALETTE[Math.floor(Math.random() * TRAFFIC_PALETTE.length)];
    const t = {
      mesh: buildVehicle(color, len, tall),
      lane: 1,
      z: carZ,
      speed: 0,
      base: 0,
      len,
      braking: false,
    };
    traffic.push(t);
    placeTraffic(t, carZ - 130 + Math.random() * 430);
  }
}

spawnTraffic();

function recycleTraffic() {
  for (const t of traffic) {
    if (t.z < carZ - 90) placeTraffic(t, carZ + 250);
    else if (t.z > carZ + 380) placeTraffic(t, carZ - 60);
  }
}

function buildOccupants() {
  const list = [];
  for (const t of traffic) {
    list.push({ ref: t, x: LANES[t.lane], z: t.z, speed: t.speed, len: t.len });
  }
  list.push({ ref: 'ego', x: carX, z: carZ, speed, len: CAR_LEN });
  return list;
}

function leadFor(x, z, len, list, self) {
  let gap = Infinity;
  let leadSpeed = 0;
  for (const o of list) {
    if (o.ref === self) continue;
    if (Math.abs(o.x - x) > 2.3) continue;
    const d = (o.z - o.len / 2) - (z + len / 2);
    if (d < -0.5) continue;
    if (d < gap) {
      gap = d;
      leadSpeed = o.speed;
    }
  }
  return { gap, speed: leadSpeed };
}

function followLimit(lead) {
  if (lead.gap === Infinity) return Infinity;
  const safe = 10 + lead.speed * 0.95;
  if (lead.gap >= safe) return Infinity;
  return Math.max(0, lead.speed * (lead.gap / safe));
}

function approach(cur, target, accel, decel, dt) {
  if (target > cur) return Math.min(target, cur + accel * dt);
  return Math.max(target, cur - decel * dt);
}

function smootherstep(t) {
  t = Math.min(1, Math.max(0, t));
  return t * t * t * (t * (t * 6 - 15) + 10);
}

function easeInOutCubic(t) {
  t = Math.min(1, Math.max(0, t));
  return t < 0.5 ? 4 * t * t * t : 1 - ((-2 * t + 2) ** 3) / 2;
}

function gapClear(targetX, list) {
  for (const o of list) {
    if (o.ref === 'ego') continue;
    if (Math.abs(o.x - targetX) > 2.4) continue;
    const dz = o.z - carZ;
    if (dz >= 0 && dz < 20 + Math.max(0, speed - o.speed) * 1.2) return false;
    if (dz < 0 && -dz < 14 + Math.max(0, o.speed - speed) * 1.15) return false;
  }
  return true;
}

function updateTraffic(dt, list) {
  for (const t of traffic) {
    const lead = leadFor(LANES[t.lane], t.z, t.len, list, t);
    let accel = (t.base - t.speed) * 0.6;
    if (lead.gap !== Infinity) {
      const safe = 9 + t.speed * 1.05;
      const closing = t.speed - lead.speed;
      if (lead.gap < safe || closing > 0.4) {
        accel = Math.min(accel, -((safe - lead.gap) * 0.55 + closing * 1.3));
      }
    }
    accel = Math.max(-7.5, Math.min(2.4, accel));
    t.speed = Math.max(0, t.speed + accel * dt);
    t.z += t.speed * dt;
    t.braking = accel < -0.7;

    t.mesh.position.set(LANES[t.lane], 0, t.z);
    t.mesh.userData.brake.material.emissiveIntensity = t.braking ? 1.6 : 0.25;
    for (const w of t.mesh.userData.wheels) w.rotation.x -= t.speed * dt * 2.4;
  }
}

function updateEgo(dt, list) {
  const lead = leadFor(carX, carZ, CAR_LEN, list, 'ego');
  leadGap = lead.gap;

  if (phase === 'CRUISE') {
    sub = 'CRUISE';
    speed = approach(speed, Math.min(V0, followLimit(lead)), 2.6, 5.0, dt);
    carX += (LANES[EGO_LANE] - carX) * Math.min(1, dt * 2.2);
    carZ += speed * dt;
    return;
  }

  if (phase === 'STOPPED') {
    sub = 'PARK';
    speed = 0;
    return;
  }

  let blocked = false;
  let moveProgress = 0;
  if (currentMove) {
    const dur = currentMove.kind === 'shoulder' ? SHOULDER_MERGE_SEC : LANE_CHANGE_SEC;
    currentMove.t += dt;
    moveProgress = Math.min(1, currentMove.t / dur);
    const ease = currentMove.kind === 'shoulder'
      ? easeInOutCubic(moveProgress)
      : smootherstep(moveProgress);
    carX = THREE.MathUtils.lerp(currentMove.from, currentMove.x, ease);
    if (moveProgress >= 1) {
      carX = currentMove.x;
      if (currentMove.kind === 'lane') egoLane = currentMove.lane;
      else onShoulder = true;
      currentMove = null;
    }
  } else if (moveQueue.length) {
    if (gapClear(moveQueue[0].x, list)) {
      currentMove = { ...moveQueue.shift(), from: carX, t: 0 };
    } else {
      blocked = true;
    }
  }

  const merging = onShoulder || (currentMove && currentMove.kind === 'shoulder');
  if (currentMove) sub = currentMove.kind === 'shoulder' ? 'SHOULDER' : 'CHANGE';
  else if (onShoulder) sub = 'SHOULDER';
  else if (blocked) sub = 'WAIT';
  else sub = 'DECEL';

  let target = Math.min(CRAWL, followLimit(lead));
  let accel = 1.4;
  let decel = 2.8;
  if (merging) {
    const p = currentMove && currentMove.kind === 'shoulder' ? moveProgress : 1;
    target = Math.min(target, THREE.MathUtils.lerp(CRAWL * 0.85, 0, easeInOutCubic(p)));
    decel = 1.6;
  } else if (onShoulder) {
    target = 0;
    decel = 1.8;
  }
  speed = approach(speed, target, accel, decel, dt);
  carZ += speed * dt;

  if (onShoulder && !currentMove && speed < 0.08) {
    speed = 0;
    phase = 'STOPPED';
    sub = 'PARK';
  }
}

function startEmergency() {
  if (phase !== 'CRUISE') return;
  phase = 'EMERGENCY';
  onShoulder = false;
  currentMove = null;
  moveQueue = [];
  for (let lane = egoLane + 1; lane <= 2; lane += 1) {
    moveQueue.push({ kind: 'lane', lane, x: LANES[lane] });
  }
  moveQueue.push({ kind: 'shoulder', x: SHOULDER_X });
  sub = 'DECEL';
}

function resetCruise(vitals) {
  phase = 'CRUISE';
  sub = 'CRUISE';
  onShoulder = false;
  currentMove = null;
  moveQueue = [];
  egoLane = EGO_LANE;
  speed = V0;
  carYaw = 0;
  prevCarX = LANES[EGO_LANE];
  spawnTraffic();
  if (vitals) Object.assign(sensor, vitals);
  else Object.assign(sensor, { status: 'NORMAL', rr: 14, hr: 72, apnea_sec: 0, source: 'mock' });
}

function applySensor(data) {
  if (!data) return;
  sensor.status = String(data.status || sensor.status).toUpperCase();
  sensor.present = data.present !== false;
  sensor.rr = Number(data.rr ?? sensor.rr);
  sensor.hr = Number(data.hr ?? sensor.hr);
  sensor.apnea_sec = Number(data.apnea_sec ?? sensor.apnea_sec);
  sensor.detail = data.detail || '';
  sensor.source = data.source || sensor.source;

  const cmd = data.cmd || (sensor.status === 'DANGER' ? 'EMERGENCY_START' : null);
  if (cmd === 'EMERGENCY_START' && lastSensorCmd !== cmd) {
    startEmergency();
  }
  if (sensor.status === 'NORMAL' && phase === 'STOPPED' && data.source === 'driver_vitals') {
    lastSensorCmd = null;
  }
  lastSensorCmd = cmd;
}

function updateLights(t) {
  const blinkOn = Math.sin(t * Math.PI * 2 * HAZARD_HZ) > 0;
  const emergency = phase === 'EMERGENCY' || phase === 'STOPPED';
  for (const lamp of car.userData.hazards) {
    lamp.material.emissiveIntensity = emergency && blinkOn ? 2.4 : 0;
  }
  car.userData.brake.material.emissiveIntensity = emergency ? 1.4 : 0.25;
}

function updateHud() {
  ui.speed.textContent = String(Math.round(speed * 3.6));
  ui.rr.textContent = String(Math.round(sensor.rr));
  ui.hr.textContent = String(Math.round(sensor.hr));
  ui.apnea.textContent = sensor.apnea_sec.toFixed(1) + 's';
  if (ui.gap) {
    ui.gap.textContent = leadGap === Infinity ? '—' : Math.round(Math.max(0, leadGap)) + 'm';
  }

  ui.phase.textContent = SUB_LABELS[sub] || SUB_LABELS.CRUISE;
  ui.badge.textContent = sensor.status + (sensor.source === 'driver_vitals' ? ' · LIVE' : ' · DEMO');
  ui.badge.className = 'badge ' + (
    sensor.status === 'DANGER' ? 'danger' : phase === 'STOPPED' ? 'warn' : 'ok'
  );
}

function updateCamera() {
  const target = new THREE.Vector3(carX + 3.8, 3.8, carZ - 9.5);
  camera.position.lerp(target, 0.08);
  camera.lookAt(carX, 0.9, carZ + 6);
}

function postEvent(payload) {
  fetch('/event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

document.getElementById('btnDanger').onclick = () => {
  const payload = {
    source: 'mock', status: 'DANGER', present: true,
    rr: 0, hr: 68, apnea_sec: 3.5, detail: '긴급 시연', cmd: 'EMERGENCY_START',
  };
  Object.assign(sensor, payload);
  postEvent(payload);
  startEmergency();
};
document.getElementById('btnNormal').onclick = () => {
  const payload = {
    source: 'mock', status: 'NORMAL', present: true,
    rr: 14, hr: 72, apnea_sec: 0, detail: '정상', cmd: null,
  };
  resetCruise(payload);
  postEvent(payload);
};
document.getElementById('btnReset').onclick = () => {
  carZ = 0;
  recycleWorld();
  const payload = {
    source: 'mock', status: 'NORMAL', present: true,
    rr: 14, hr: 72, apnea_sec: 0, detail: '리셋', cmd: null,
  };
  resetCruise(payload);
  postEvent(payload);
};
window.addEventListener('keydown', (e) => {
  if (e.key === 'd' || e.key === 'D') document.getElementById('btnDanger').click();
  if (e.key === 'n' || e.key === 'N') document.getElementById('btnNormal').click();
  if (e.key === 'r' || e.key === 'R') document.getElementById('btnReset').click();
});
window.addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

async function pollSensor() {
  try {
    const res = await fetch('/state', { cache: 'no-store' });
    if (res.ok) applySensor(await res.json());
  } catch (_) {
    /* file:// 또는 서버 미기동 시 로컬 데모만 동작 */
  }
}
setInterval(pollSensor, 160);
pollSensor();

const clock = new THREE.Clock();
function tick() {
  const dt = Math.min(0.05, clock.getDelta());
  const list = buildOccupants();
  updateEgo(dt, list);
  updateTraffic(dt, list);
  recycleWorld();
  recycleTraffic();

  car.position.set(carX, 0, carZ);
  const vx = (carX - prevCarX) / Math.max(dt, 1 / 120);
  prevCarX = carX;
  const desiredYaw = THREE.MathUtils.clamp(
    Math.atan2(vx, Math.max(speed, 3.5)),
    -0.28,
    0.28,
  );
  carYaw += (desiredYaw - carYaw) * Math.min(1, dt * 2.4);
  car.rotation.y = carYaw;
  for (const w of car.userData.wheels) w.rotation.x += speed * dt * 2.4;

  updateLights(clock.elapsedTime);
  updateCamera();
  updateHud();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}
tick();
