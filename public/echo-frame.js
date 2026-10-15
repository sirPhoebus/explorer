// Unreal is Z-up, X forward, Y right, centimeters.
// The view is Y-up. +X stays +X, +Z becomes +Y, and +Y becomes -Z.
// That keeps the basis right-handed: unreal +X, +Y, +Z map to three axes
// whose scalar triple product is +1.

function zero(value) {
  return Object.is(value, -0) ? 0 : value;
}

export function unrealToView(x, y, z) {
  return [zero(x), zero(z), zero(-y)];
}

export function unrealSizeToView(sx, sy, sz) {
  return [sx, sz, sy];
}

export function unrealYawForward(yawDeg) {
  const yaw = (yawDeg * Math.PI) / 180;
  return unrealToView(Math.cos(yaw), Math.sin(yaw), 0);
}

export function subtract(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

export function cross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

export function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function normalize(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}

// Column-major. Camera looks from eye toward target, with +Y up in view space.
export function lookAt(eye, target, up) {
  const zAxis = normalize(subtract(eye, target));
  const xAxis = normalize(cross(up, zAxis));
  const yAxis = cross(zAxis, xAxis);
  return [
    xAxis[0], yAxis[0], zAxis[0], 0,
    xAxis[1], yAxis[1], zAxis[1], 0,
    xAxis[2], yAxis[2], zAxis[2], 0,
    -dot(xAxis, eye), -dot(yAxis, eye), -dot(zAxis, eye), 1,
  ];
}

export function perspective(fovY, aspect, near, far) {
  const f = 1 / Math.tan(fovY / 2);
  const span = 1 / (near - far);
  return [
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * span, -1,
    0, 0, 2 * far * near * span, 0,
  ];
}

export function multiply(a, b) {
  const out = new Array(16);
  for (let col = 0; col < 4; col += 1) {
    for (let row = 0; row < 4; row += 1) {
      out[col * 4 + row] =
        a[row] * b[col * 4] +
        a[4 + row] * b[col * 4 + 1] +
        a[8 + row] * b[col * 4 + 2] +
        a[12 + row] * b[col * 4 + 3];
    }
  }
  return out;
}

export function transformPoint(matrix, point) {
  const x = point[0];
  const y = point[1];
  const z = point[2];
  const w = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15] || 1;
  return [
    (matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]) / w,
    (matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]) / w,
    (matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]) / w,
  ];
}
