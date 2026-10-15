import {
  lookAt,
  multiply,
  perspective,
  unrealSizeToView,
  unrealToView,
  unrealYawForward,
} from "./echo-frame.js";

const SOLID = [0.89, 0.63, 0.35];
const FLOOR = [0.49, 0.75, 0.53];
const WALL = [0.88, 0.48, 0.41];
const PAWN = [0.95, 0.94, 0.91];
const NOSE = [0.96, 0.78, 0.42];

const canvas = document.getElementById("echo-view");
const caption = document.getElementById("echo-caption");
const empty = document.getElementById("echo-empty");

const view = {
  yaw: 0.8,
  pitch: 0.55,
  distance: 1800,
  focus: [0, 200, 0],
  follow: true,
  map: null,
  key: "",
  instances: new Float32Array(0),
  count: 0,
  dragging: null,
};

function boxCorners() {
  const faces = [
    [[1, 0, 0], [[0.5, -0.5, -0.5], [0.5, 0.5, -0.5], [0.5, 0.5, 0.5], [0.5, -0.5, 0.5]]],
    [[-1, 0, 0], [[-0.5, -0.5, 0.5], [-0.5, 0.5, 0.5], [-0.5, 0.5, -0.5], [-0.5, -0.5, -0.5]]],
    [[0, 1, 0], [[-0.5, 0.5, -0.5], [-0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [0.5, 0.5, -0.5]]],
    [[0, -1, 0], [[-0.5, -0.5, 0.5], [-0.5, -0.5, -0.5], [0.5, -0.5, -0.5], [0.5, -0.5, 0.5]]],
    [[0, 0, 1], [[-0.5, -0.5, 0.5], [0.5, -0.5, 0.5], [0.5, 0.5, 0.5], [-0.5, 0.5, 0.5]]],
    [[0, 0, -1], [[0.5, -0.5, -0.5], [-0.5, -0.5, -0.5], [-0.5, 0.5, -0.5], [0.5, 0.5, -0.5]]],
  ];
  const corners = [];
  const normals = [];
  for (const [normal, quad] of faces) {
    const [a, b, c, d] = quad;
    for (const vertex of [a, b, c, a, c, d]) {
      corners.push(...vertex);
      normals.push(...normal);
    }
  }
  return { corners: new Float32Array(corners), normals: new Float32Array(normals), count: corners.length / 3 };
}

function pushBox(list, center, size, color) {
  const placed = unrealToView(center[0], center[1], center[2]);
  const scaled = unrealSizeToView(size[0], size[1], size[2]);
  list.push(...placed, ...scaled, ...color);
}

function buildInstances(map) {
  const list = [];
  const cell = Number(map.cell) || 35;
  const body = cell * 0.92;
  for (const solid of map.solids || []) {
    pushBox(
      list,
      [(solid[0] + 0.5) * cell, (solid[1] + 0.5) * cell, (solid[2] + 0.5) * cell],
      [body, body, body],
      SOLID,
    );
  }
  const slab = Math.min(8, cell * 0.2);
  for (const floor of map.floors || []) {
    pushBox(
      list,
      [(floor[0] + 0.5) * cell, (floor[1] + 0.5) * cell, floor[2]],
      [cell * 0.9, cell * 0.9, slab],
      FLOOR,
    );
  }
  const mark = cell * 0.38;
  for (const wall of map.walls || []) {
    pushBox(
      list,
      [(wall[0] + 0.5) * cell, (wall[1] + 0.5) * cell, wall[2]],
      [mark, mark, mark],
      WALL,
    );
  }
  if (map.pawn) {
    pushBox(list, [map.pawn.x, map.pawn.y, map.pawn.z], [70, 70, 180], PAWN);
    const forward = unrealYawForward(map.pawn.yaw);
    pushBox(
      list,
      [map.pawn.x + forward[0] * 55, map.pawn.y - forward[2] * 55, map.pawn.z + forward[1] * 55],
      [22, 22, 22],
      NOSE,
    );
    return { data: new Float32Array(list), focus: unrealToView(map.pawn.x, map.pawn.y, map.pawn.z) };
  }
  if (!list.length) return { data: new Float32Array(0), focus: [0, 0, 0] };
  let sx = 0;
  let sy = 0;
  let sz = 0;
  const boxes = list.length / 9;
  for (let index = 0; index < list.length; index += 9) {
    sx += list[index];
    sy += list[index + 1];
    sz += list[index + 2];
  }
  return { data: new Float32Array(list), focus: [sx / boxes, sy / boxes, sz / boxes] };
}

function mapKey(map) {
  const pawn = map.pawn ? `${map.pawn.x},${map.pawn.y},${map.pawn.z},${map.pawn.yaw}` : "";
  return `${map.updatedAt}|${(map.solids || []).length}|${(map.floors || []).length}|${(map.walls || []).length}|${pawn}`;
}

function describe(map) {
  if (!map || map.empty) return map?.reason || "No echo map yet.";
  const pawn = map.pawn
    ? `Pawn at (${Math.round(map.pawn.x)}, ${Math.round(map.pawn.y)}, ${Math.round(map.pawn.z)}).`
    : "Pawn position is not in this save.";
  return `${map.level} · ${(map.solids || []).length} solid volumes · ${(map.floors || []).length} floors · ${(map.walls || []).length} walls. ${pawn} Drag to orbit.`;
}

const gl = canvas?.getContext("webgl2", { antialias: true, depth: true });
let program = null;
let mesh = null;
let instanceBuffer = null;

function compile(type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) || "Shader failed.");
  }
  return shader;
}

function initGl() {
  if (!gl) return;
  const vertex = compile(gl.VERTEX_SHADER, `#version 300 es
    layout(location=0) in vec3 corner;
    layout(location=1) in vec3 normal;
    layout(location=2) in vec3 iCenter;
    layout(location=3) in vec3 iSize;
    layout(location=4) in vec3 iColor;
    uniform mat4 uViewProj;
    out vec3 vColor;
    out vec3 vNormal;
    void main() {
      vec3 world = iCenter + corner * iSize;
      gl_Position = uViewProj * vec4(world, 1.0);
      vColor = iColor;
      vNormal = normal;
    }
  `);
  const fragment = compile(gl.FRAGMENT_SHADER, `#version 300 es
    precision mediump float;
    in vec3 vColor;
    in vec3 vNormal;
    out vec4 fragColor;
    void main() {
      float light = 0.38 + 0.62 * max(dot(normalize(vNormal), normalize(vec3(0.35, 0.82, 0.28))), 0.0);
      fragColor = vec4(vColor * light, 1.0);
    }
  `);
  program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) || "Program failed.");
  }
  const geometry = boxCorners();
  mesh = { count: geometry.count, vao: gl.createVertexArray() };
  gl.bindVertexArray(mesh.vao);
  const corners = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, corners);
  gl.bufferData(gl.ARRAY_BUFFER, geometry.corners, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
  const normals = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, normals);
  gl.bufferData(gl.ARRAY_BUFFER, geometry.normals, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
  instanceBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, 16, gl.DYNAMIC_DRAW);
  const stride = 9 * 4;
  gl.enableVertexAttribArray(2);
  gl.vertexAttribPointer(2, 3, gl.FLOAT, false, stride, 0);
  gl.vertexAttribDivisor(2, 1);
  gl.enableVertexAttribArray(3);
  gl.vertexAttribPointer(3, 3, gl.FLOAT, false, stride, 12);
  gl.vertexAttribDivisor(3, 1);
  gl.enableVertexAttribArray(4);
  gl.vertexAttribPointer(4, 3, gl.FLOAT, false, stride, 24);
  gl.vertexAttribDivisor(4, 1);
  gl.bindVertexArray(null);
  gl.enable(gl.DEPTH_TEST);
  gl.clearColor(0.063, 0.071, 0.086, 1);
}

function resize() {
  if (!canvas) return;
  const width = Math.max(2, canvas.clientWidth);
  const height = Math.max(2, canvas.clientHeight);
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  const pixelsWide = Math.floor(width * scale);
  const pixelsHigh = Math.floor(height * scale);
  if (canvas.width !== pixelsWide || canvas.height !== pixelsHigh) {
    canvas.width = pixelsWide;
    canvas.height = pixelsHigh;
  }
}

function draw() {
  if (!gl || !program || !mesh) return;
  resize();
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  if (!view.count) return;
  const aspect = canvas.width / canvas.height;
  const pitch = Math.max(-1.2, Math.min(1.2, view.pitch));
  const eye = [
    view.focus[0] + view.distance * Math.cos(pitch) * Math.sin(view.yaw),
    view.focus[1] + view.distance * Math.sin(pitch),
    view.focus[2] + view.distance * Math.cos(pitch) * Math.cos(view.yaw),
  ];
  const viewProj = multiply(perspective(0.9, aspect, 10, 200000), lookAt(eye, view.focus, [0, 1, 0]));
  gl.useProgram(program);
  gl.uniformMatrix4fv(gl.getUniformLocation(program, "uViewProj"), false, viewProj);
  gl.bindVertexArray(mesh.vao);
  gl.drawArraysInstanced(gl.TRIANGLES, 0, mesh.count, view.count);
  gl.bindVertexArray(null);
}

function applyMap(map) {
  view.map = map;
  if (caption) caption.textContent = describe(map);
  const hasLevel = Boolean(
    map && !map.empty && ((map.solids || []).length || (map.floors || []).length || (map.walls || []).length),
  );
  const showPawn = Boolean(map && map.pawn);
  if (empty) {
    empty.hidden = hasLevel || showPawn;
    if (!hasLevel && !showPawn) {
      empty.textContent = (map && map.reason) || "No echo map yet. Ask the pawn to walk around.";
    }
  }
  if (!hasLevel && !showPawn) {
    view.key = "";
    view.count = 0;
    return;
  }
  const key = mapKey(map);
  if (key === view.key) {
    if (view.follow && map.pawn) view.focus = unrealToView(map.pawn.x, map.pawn.y, map.pawn.z);
    return;
  }
  const built = buildInstances(map);
  view.key = key;
  view.instances = built.data;
  view.count = built.data.length / 9;
  if (view.follow) view.focus = built.focus;
  if (!view.distanceReady) {
    view.distance = Math.max(600, Number(map.cell || 35) * 40);
    view.distanceReady = true;
  }
  if (gl && instanceBuffer) {
    gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, built.data, gl.DYNAMIC_DRAW);
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/echo-map");
    if (!response.ok) throw new Error("The echo map request failed.");
    applyMap(await response.json());
  } catch (error) {
    if (caption) caption.textContent = error.message;
  }
}

const clearMapButton = document.getElementById("clear-map");

async function clearMap() {
  if (!clearMapButton) return;
  if (!window.confirm("Clear the map? The pawn forgets this level and the next walk starts empty.")) return;
  clearMapButton.disabled = true;
  try {
    const response = await fetch("/api/echo-map/clear", { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "The map could not be cleared.");
    view.follow = true;
    await refresh();
  } catch (error) {
    if (caption) caption.textContent = error.message;
  } finally {
    clearMapButton.disabled = false;
  }
}

function bindPointer() {
  if (!canvas) return;
  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    view.dragging = { x: event.clientX, y: event.clientY, button: event.button };
    if (event.button === 0) view.follow = false;
  });
  canvas.addEventListener("pointerup", () => {
    view.dragging = null;
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!view.dragging) return;
    const dx = event.clientX - view.dragging.x;
    const dy = event.clientY - view.dragging.y;
    view.dragging.x = event.clientX;
    view.dragging.y = event.clientY;
    if (view.dragging.button === 2 || view.dragging.button === 1 || event.shiftKey) {
      const pan = view.distance * 0.0015;
      view.focus = [view.focus[0] - dx * pan, view.focus[1] + dy * pan, view.focus[2]];
      view.follow = false;
      return;
    }
    view.yaw -= dx * 0.008;
    view.pitch = Math.max(-1.2, Math.min(1.2, view.pitch + dy * 0.006));
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    view.distance = Math.max(80, Math.min(80000, view.distance * (event.deltaY > 0 ? 1.1 : 0.9)));
  }, { passive: false });
  canvas.addEventListener("dblclick", () => {
    view.follow = true;
    view.key = "";
    if (view.map) applyMap(view.map);
  });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
}

function frame() {
  draw();
  requestAnimationFrame(frame);
}

if (canvas) {
  try {
    initGl();
  } catch (error) {
    if (caption) caption.textContent = error.message;
  }
  bindPointer();
  if (clearMapButton) clearMapButton.addEventListener("click", () => { clearMap(); });
  refresh();
  setInterval(refresh, 2000);
  requestAnimationFrame(frame);
}
