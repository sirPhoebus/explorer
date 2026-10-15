"""Echo eyes for the possessed pawn, aimed at the level open in this editor.

The Quake bot is not running here. This is the same sensing rule, in
centimeters, using Unreal line traces in the play world. The eyes are a
forward cone, wide across and narrower above, looking where the camera looks.
A short glance to either side catches passages beside the pawn. Space behind
it stays unknown until the pawn turns. Each sample is kept as a volume.
Unknown cells are not walkable, a clear sample never erases a solid volume,
and the close contact ring stays on after the map starts to fill. The pawn
turns with look input and walks with W only while the next cell is known to
be free and the space ahead is clear.
"""

from __future__ import annotations

import json
import math
import os
import time
import traceback
from collections import deque

WALK_NORMAL_Z = 0.7
TRACE_PROFILE = "BlockAll"
# A person looks about 120 degrees across. The band above the horizon is
# narrower. The band below reaches the next floor cell: the eye is about one
# body height up, so that cell is a steep look down, not a level gaze.
CONE_YAW_HALF = 60.0
CONE_PITCH_UP = 25.0
CONE_PITCH_DOWN = 70.0
CONE_STEP_DEG = 8.0
GLANCE_YAW_DEG = 75.0
GLANCE_PERIOD_S = 3.5
GLANCE_HOLD_S = 0.4
RAYS_PER_TICK = 8
TURN_RATE_DEG_S = 720.0
FULL_SPEED_TURN_DEG = 25.0
PLAN_INTERVAL_S = 0.4
# A finished level has no frontier, so a patrol replan used to walk the whole
# map. Keep the current leg and turn around without searching again.
PATROL_INTERVAL_S = 2.0
MAX_OPEN_VOLUMES = 8192
GOAL_HOLD_S = 1.2
STUCK_WINDOW_S = 1.0
# Sensed ramp samples land a little past one step. A real curb taller than
# this still does not link.
LINK_SLACK = 10.0
BFS_LIMIT = 20000
ATTACH_RADIUS = 3
LOOKAHEAD_CELLS = 1.5
ARRIVAL_CELLS = 0.3
RANGE_CELLS = 64
# The cone records a wall as soon as a ray hits it. W used to stay down until
# a separate probe, one capsule radius plus 16 cm, touched that wall. Release
# W while the wall is still two meters in front of the body.
BRAKE_REACH_CM = 200.0
ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))


def wrap_deg(angle):
    return (angle + 180.0) % 360.0 - 180.0


def _band_slot(voxel, origin):
    """0 +X, 1 -X, 2 +Y, 3 -Y, 4 +Z, 5 -Z. The largest axis wins."""
    dx = voxel[0] - origin[0]
    dy = voxel[1] - origin[1]
    dz = voxel[2] - origin[2]
    ax = abs(dx)
    ay = abs(dy)
    az = abs(dz)
    if ax == 0 and ay == 0 and az == 0:
        return None
    if az >= ax and az >= ay:
        return 4 if dz > 0 else 5
    if ax >= ay:
        return 0 if dx > 0 else 1
    return 2 if dy > 0 else 3


def _ue():
    import unreal

    return unreal


class EchoMap:
    """Floors for walking, plus the solid and open volumes around the pawn."""

    def __init__(self, cell, step, body_height):
        self.cell = float(cell)
        self.step = float(step)
        self.body_height = float(body_height)
        self.void_depth = max(120.0, self.step * 2.0)
        self.floors = {}
        self.walls = {}
        self.empty_lo = {}
        self.solids = set()
        self.opens = set()
        self.pawn = None
        self.revision = 0
        self._count_key = None
        self._count_value = None

    def cell_of(self, x, y):
        return (math.floor(float(x) / self.cell), math.floor(float(y) / self.cell))

    def center(self, cell):
        return ((cell[0] + 0.5) * self.cell, (cell[1] + 0.5) * self.cell)

    def add_floor(self, x, y, z):
        """Keep the ground. A higher sample within body height is the same floor lifted into the air."""
        cell = self.cell_of(x, y)
        layers = self.floors.setdefault(cell, [])
        height = float(z)
        limit = self.step + LINK_SLACK
        for index, existing in enumerate(layers):
            if abs(existing - height) <= limit:
                if height < existing - 1.0:
                    layers[index] = height
                    self.revision += 1
                    return True
                return False
        if any(0.0 < height - existing < self.body_height for existing in layers):
            return False
        pulled = False
        kept = []
        for existing in layers:
            if 0.0 < existing - height < self.body_height:
                if not pulled:
                    kept.append(height)
                    pulled = True
            else:
                kept.append(existing)
        if pulled:
            self.floors[cell] = kept
            self.revision += 1
            return True
        layers.append(height)
        self.revision += 1
        return True

    def collapse_floors(self):
        """One column keeps its lowest floor, and a deck only when it clears the body."""
        changed = False
        for cell, layers in self.floors.items():
            ordered = sorted(layers)
            kept = [ordered[0]]
            for height in ordered[1:]:
                if height - kept[-1] >= self.body_height:
                    kept.append(height)
            if kept != list(layers):
                self.floors[cell] = kept
                changed = True
        if changed:
            self.revision += 1
        return changed

    def add_wall(self, x, y, z):
        cell = self.cell_of(x, y)
        samples = self.walls.setdefault(cell, [])
        height = float(z)
        for existing in samples:
            if abs(existing - height) <= self.step * 0.5:
                return False
        if len(samples) >= 8:
            return False
        samples.append(height)
        self.revision += 1
        return True

    def add_empty(self, x, y, z):
        cell = self.cell_of(x, y)
        height = float(z)
        current = self.empty_lo.get(cell)
        if current is None or height < current:
            self.empty_lo[cell] = height

    def voxel_of(self, x, y, z):
        return (
            math.floor(float(x) / self.cell),
            math.floor(float(y) / self.cell),
            math.floor(float(z) / self.cell),
        )

    def _body_voxel(self, voxel, protect, feet_z):
        if protect is None or feet_z is None or (voxel[0], voxel[1]) != protect:
            return False
        center_z = (voxel[2] + 0.5) * self.cell
        return feet_z <= center_z < feet_z + self.body_height

    def add_solid(self, x, y, z, protect=None, feet_z=None):
        """Remember a solid volume. A later clear sample cannot remove it."""
        voxel = self.voxel_of(x, y, z)
        if self._body_voxel(voxel, protect, feet_z):
            return False
        self.opens.discard(voxel)
        if voxel in self.solids:
            return False
        self.solids.add(voxel)
        self.revision += 1
        return True

    def add_open_volume(self, x, y, z):
        """Remember looked-through air. This never erases a solid volume.

        The set is capped. A finished level was keeping more than a million
        air cells, and every status check walked all of them on the editor thread.
        """
        voxel = self.voxel_of(x, y, z)
        if voxel in self.solids or voxel in self.opens:
            return False
        if len(self.opens) >= MAX_OPEN_VOLUMES:
            return False
        self.opens.add(voxel)
        return True

    def solid_in_body(self, cell, feet_z):
        low = feet_z + self.step
        high = feet_z + self.body_height
        if high <= low:
            return False
        z_index = math.floor(low / self.cell)
        last = math.floor((high - 0.01) / self.cell)
        while z_index <= last:
            if (cell[0], cell[1], z_index) in self.solids:
                return True
            z_index += 1
        return False

    def volume_bands(self, x, y, z):
        """Solid counts on six sides of the pawn, plus open volumes above it."""
        origin = self.voxel_of(x, y, z)
        bands = [0, 0, 0, 0, 0, 0]
        open_above = 0
        for voxel in self.solids:
            slot = _band_slot(voxel, origin)
            if slot is not None:
                bands[slot] += 1
        above = origin[2] + 1
        # Past the cap this would walk a million cells on the editor thread.
        if len(self.opens) <= MAX_OPEN_VOLUMES:
            for voxel in self.opens:
                if voxel[2] > above:
                    open_above += 1
        return (bands[0], bands[1], bands[2], bands[3], bands[4], bands[5], open_above)

    def floor_near(self, cell, feet_z):
        best = None
        best_distance = None
        for height in self.floors.get(cell, ()):
            distance = abs(height - feet_z)
            if distance <= self.step and (best_distance is None or distance < best_distance):
                best = height
                best_distance = distance
        return best

    def wall_in_body(self, cell, feet_z):
        low = feet_z + self.step
        high = feet_z + self.body_height
        for height in self.walls.get(cell, ()):
            if low <= height < high:
                return True
        return self.solid_in_body(cell, feet_z)

    def known_drop(self, cell, feet_z):
        if self.floor_near(cell, feet_z) is not None:
            return False
        if any(height < feet_z - self.step for height in self.floors.get(cell, ())):
            return True
        lowest = self.empty_lo.get(cell)
        return lowest is not None and lowest < feet_z - self.step - self.void_depth * 0.25

    def standable(self, cell, feet_z):
        return self.linked_floor(cell, feet_z) is not None

    def linked_floor(self, cell, height):
        """Floor in this column the pawn can walk onto from height.

        A ramp links when the rise stays inside one step, plus a small slack
        for samples that landed just past that step. A cube face does not,
        because that column has a wall through the body above its floor.
        """
        best = None
        best_distance = None
        limit = self.step + LINK_SLACK
        for floor in self.floors.get(cell, ()):
            distance = abs(floor - height)
            if distance <= limit and not self.wall_in_body(cell, floor):
                if best_distance is None or distance < best_distance:
                    best = floor
                    best_distance = distance
        return best

    def unknown(self, cell, feet_z):
        if self.floor_near(cell, feet_z) is not None:
            return False
        if self.wall_in_body(cell, feet_z):
            return False
        if self.known_drop(cell, feet_z):
            return False
        # A ramp stores several heights in one column. Those other heights are
        # the slope we already saw, not an unmapped opening to turn toward.
        if cell in self.floors or cell in self.walls:
            return False
        return True

    def is_frontier(self, cell, feet_z):
        if not self.standable(cell, feet_z):
            return False
        for step_x, step_y in ORTHO:
            if self.unknown((cell[0] + step_x, cell[1] + step_y), feet_z):
                return True
        return False

    def counts(self, feet_z):
        key = (self.revision, int(round(float(feet_z) / self.step)))
        if key == self._count_key:
            return self._count_value
        free = 0
        frontiers = 0
        for cell in self.floors:
            if not self.standable(cell, feet_z):
                continue
            free += 1
            if self.is_frontier(cell, feet_z):
                frontiers += 1
        result = (free, len(self.walls), frontiers, len(self.solids), len(self.opens))
        self._count_key = key
        self._count_value = result
        return result

    def snapshot(self):
        floors = []
        for cell, layers in self.floors.items():
            for height in layers:
                floors.append([cell[0], cell[1], round(height, 1)])
        walls = []
        for cell, layers in self.walls.items():
            for height in layers:
                walls.append([cell[0], cell[1], round(height, 1)])
        solids = [[voxel[0], voxel[1], voxel[2]] for voxel in self.solids]
        payload = {"cell": self.cell, "step": self.step, "floors": floors, "walls": walls, "solids": solids}
        if self.pawn is not None:
            payload["pawn"] = self.pawn
        return payload

    def restore(self, payload):
        if abs(float(payload.get("cell", 0)) - self.cell) > 1.0:
            return False
        for item in payload.get("floors", []):
            cell = (int(item[0]), int(item[1]))
            self.floors.setdefault(cell, []).append(float(item[2]))
        for item in payload.get("walls", []):
            self.add_wall((item[0] + 0.5) * self.cell, (item[1] + 0.5) * self.cell, item[2])
        for item in payload.get("solids", []):
            self.add_solid((item[0] + 0.5) * self.cell, (item[1] + 0.5) * self.cell, (item[2] + 0.5) * self.cell)
        self.collapse_floors()
        return True


def _unwind(parent, end):
    path = [end]
    while path[-1] in parent:
        path.append(parent[path[-1]])
    path.reverse()
    return path


def _nearest_standable(echo_map, origin, feet_z):
    if echo_map.standable(origin, feet_z):
        return origin
    best = None
    best_distance = None
    for step_x in range(-ATTACH_RADIUS, ATTACH_RADIUS + 1):
        for step_y in range(-ATTACH_RADIUS, ATTACH_RADIUS + 1):
            cell = (origin[0] + step_x, origin[1] + step_y)
            if not echo_map.standable(cell, feet_z):
                continue
            distance = step_x * step_x + step_y * step_y
            if best_distance is None or distance < best_distance:
                best = cell
                best_distance = distance
    return best


def _height_bin(echo_map, height):
    return int(round(float(height) / echo_map.step))


def _unwind_cells(parent, end_key):
    path = [end_key[0]]
    cursor = end_key
    while cursor in parent:
        cursor = parent[cursor]
        path.append(cursor[0])
    path.reverse()
    return path


def _linked_search(echo_map, origin, feet_z, accept, sink=None):
    """Walk columns whose floors differ by at most one step, so a ramp stays connected."""
    start = _nearest_standable(echo_map, origin, feet_z)
    if start is None:
        return None
    start_h = echo_map.floor_near(start, feet_z)
    if start_h is None:
        return None
    start_key = (start, _height_bin(echo_map, start_h))
    seen = {start_key}
    parent = {}
    queue = deque([(start, start_h)])
    expansions = 0
    while queue:
        cell, height = queue.popleft()
        expansions += 1
        if expansions > BFS_LIMIT:
            return None
        for step_x, step_y in ORTHO:
            nxt = (cell[0] + step_x, cell[1] + step_y)
            floor = echo_map.linked_floor(nxt, height)
            if floor is None:
                continue
            key = (nxt, _height_bin(echo_map, floor))
            if key in seen:
                continue
            seen.add(key)
            parent[key] = (cell, _height_bin(echo_map, height))
            if accept(nxt, floor):
                return _unwind_cells(parent, key)
            queue.append((nxt, floor))
    if sink is not None:
        sink["parent"] = parent
    return None


def plan_nearest(echo_map, origin, feet_z, avoid=()):
    """Shortest ramp-aware walk to the nearest frontier the pawn is not already in."""
    blocked = set(avoid or ())
    start = _nearest_standable(echo_map, origin, feet_z)
    if start is None:
        return None
    path = _linked_search(
        echo_map,
        origin,
        feet_z,
        lambda cell, floor: cell != start and cell not in blocked and echo_map.is_frontier(cell, floor),
    )
    if path is not None:
        return path
    if echo_map.is_frontier(start, feet_z):
        return [start]
    return None


def choose_path(echo_map, origin, feet_z, previous, avoid=()):
    """Keep the current lookout while it is still an open frontier.

    A fresh nearest-frontier search flips between two equal ramps and the pawn
    yaws left and right without moving. The previous goal stays until it is no
    longer reachable.
    """
    blocked = set(avoid or ())
    if previous is not None and previous != origin and previous not in blocked:
        kept = _linked_search(
            echo_map,
            origin,
            feet_z,
            lambda cell, floor: cell == previous and echo_map.is_frontier(cell, floor),
        )
        if kept:
            return kept
    return plan_nearest(echo_map, origin, feet_z, blocked)


def plan_to(echo_map, origin, goal, feet_z):
    start = _nearest_standable(echo_map, origin, feet_z)
    if start is None:
        return None
    if start == goal:
        return [start]
    return _linked_search(echo_map, origin, feet_z, lambda cell, floor: cell == goal)


def plan_farthest(echo_map, origin, feet_z, avoid):
    start = _nearest_standable(echo_map, origin, feet_z)
    if start is None:
        return None
    found = {"cell": start, "distance": -1, "key": None}

    def consider(cell, floor):
        distance = abs(cell[0] - start[0]) + abs(cell[1] - start[1])
        if cell not in avoid and distance > found["distance"]:
            found["cell"] = cell
            found["distance"] = distance
            found["key"] = (cell, _height_bin(echo_map, floor))
        return False

    sink = {}
    _linked_search(echo_map, origin, feet_z, consider, sink)
    if found["key"] is None or found["cell"] == start or "parent" not in sink:
        return [start]
    return _unwind_cells(sink["parent"], found["key"])


class _Walker:
    def __init__(self):
        self.handle = None
        self.running = False
        self.holding = False
        self.echo_map = None
        self.body = None
        self.level_name = ""
        self.path = []
        self.path_index = 0
        self.goal_cell = None
        self.commit_cell = None
        self.idle_since = None
        self.mode = "explore"
        self.force_plan = True
        self.next_plan = 0.0
        self.revision_planned = -1
        self.scan_index = 0
        self.rays = []
        self.glance_side = 1
        self.glance_yaw = 0.0
        self.glance_until = 0.0
        self.next_glance = 0.0
        self.stuck = []
        self.stuck_strikes = 0
        self.avoid_goals = []
        self.summary = (0, 0, 0, 0, 0)
        self.summary_at = 0.0
        self.contact_blocked = False
        self.hold_reason = ""
        self.rays_cast = 0
        self.last_error = ""
        self.loaded_map = False
        self.last_save = 0.0
        self.frontier_revision = -1

    def reset_motion(self):
        self.path = []
        self.path_index = 0
        self.goal_cell = None
        self.commit_cell = None
        self.idle_since = None
        self.mode = "explore"
        self.force_plan = True
        self.stuck = []
        self.stuck_strikes = 0
        self.avoid_goals = []
        self.contact_blocked = False
        self.hold_reason = ""
        self.scan_index = 0
        self.rays = []
        self.glance_side = 1
        self.glance_yaw = 0.0
        self.glance_until = 0.0
        self.next_glance = 0.0


WALKER = _Walker()


def _level_name(pawn):
    world = pawn.get_world()
    name = str(world.get_name()) if world is not None else "open level"
    if name.startswith("UEDPIE_"):
        parts = name.split("_", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    return name


def _measure(pawn):
    capsule = getattr(pawn, "capsule_component", None)
    if capsule is None:
        capsule = pawn.get_component_by_class(_ue().CapsuleComponent)
    if capsule is None:
        raise RuntimeError("The possessed pawn has no capsule, so echo cannot size its eyes to this level.")
    radius = float(capsule.get_scaled_capsule_radius())
    half = float(capsule.get_scaled_capsule_half_height())
    if radius < 5.0 or half < 5.0:
        raise RuntimeError("The capsule size is unusable (radius %.1f, half-height %.1f)." % (radius, half))
    step = 45.0
    movement = getattr(pawn, "character_movement", None)
    if movement is not None:
        value = getattr(movement, "max_step_height", None)
        try:
            if callable(value):
                step = float(value())
            elif value is not None:
                step = float(value)
        except Exception:
            step = 45.0
    if step < 5.0:
        step = 45.0
    # Cube faces are steeper than this. Ramp surfaces are not.
    walk_nz = WALK_NORMAL_Z
    if movement is not None:
        value = getattr(movement, "get_walkable_floor_z", None)
        try:
            if callable(value):
                walk_nz = float(value())
        except Exception:
            walk_nz = WALK_NORMAL_Z
    if not 0.2 < walk_nz < 0.99:
        walk_nz = WALK_NORMAL_Z
    return {"radius": radius, "half": half, "step": step, "height": half * 2.0, "walk_nz": walk_nz}


def _pawn():
    import player_bridge

    return player_bridge._pawn()


def _safe_level(level_name):
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in level_name)


def _echo_folder():
    return os.path.join(_ue().Paths.project_saved_dir(), "EchoMaps")


def _map_path(level_name, folder=None):
    directory = folder if folder is not None else _echo_folder()
    return os.path.join(directory, _safe_level(level_name) + ".json")


def forget(folder=None):
    """Stop the walk without saving, and drop the level from memory and disk."""
    walker = WALKER
    walker.running = False
    handle = walker.handle
    walker.handle = None
    _release_w()
    if handle is not None:
        try:
            _ue().unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
    if walker.level_name:
        path = _map_path(walker.level_name, folder)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as exc:
            try:
                _ue().log_warning("Echo map was not cleared: " + str(exc))
            except Exception:
                pass
    walker.echo_map = None
    walker.level_name = ""
    walker.loaded_map = False
    walker.rays_cast = 0
    walker.summary = (0, 0, 0, 0, 0)
    walker.last_save = 0.0
    walker.reset_motion()
    try:
        _ue().log("Echo map cleared. The next walk starts empty.")
    except Exception:
        pass


def honor_clear(folder=None):
    """The page writes EchoMaps/.clear when the user wants a fresh map."""
    directory = folder if folder is not None else _echo_folder()
    marker = os.path.join(directory, ".clear")
    if not os.path.isfile(marker):
        return False
    try:
        os.remove(marker)
    except Exception:
        pass
    forget(directory)
    return True


def _load_map(echo_map, level_name):
    path = _map_path(level_name)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return echo_map.restore(payload)
    except Exception as exc:
        _ue().log_warning("Echo map was not loaded: " + str(exc))
        return False


def _remember_pawn(location, yaw):
    if WALKER.echo_map is None or location is None:
        return
    WALKER.echo_map.pawn = [
        round(float(location.x), 1),
        round(float(location.y), 1),
        round(float(location.z), 1),
        round(float(yaw), 1),
    ]


def _save_map():
    walker = WALKER
    if walker.echo_map is None or not walker.level_name:
        return
    try:
        controller, pawn = _pawn()
        if pawn is not None and controller is not None:
            _remember_pawn(pawn.get_actor_location(), controller.get_control_rotation().yaw)
    except Exception:
        pass
    path = _map_path(walker.level_name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(walker.echo_map.snapshot(), handle, separators=(",", ":"))
        walker.last_save = time.monotonic()
    except Exception as exc:
        _ue().log_warning("Echo map was not saved: " + str(exc))


def _release_w():
    if not WALKER.holding:
        return
    WALKER.holding = False
    try:
        import player_key_input

        player_key_input.release("W")
    except Exception:
        try:
            import player_key_input

            player_key_input.release_all()
        except Exception:
            pass


def _hold_w():
    if WALKER.holding:
        return
    import player_key_input

    player_key_input.press("W", 0)
    WALKER.holding = True


def _set_w(down):
    if down:
        _hold_w()
    else:
        _release_w()


def _read_hit(hit):
    values = hit.to_tuple()
    if len(values) < 7:
        return {
            "blocking": False,
            "overlap": True,
            "distance": 0.0,
            "impact": (0.0, 0.0, 0.0),
            "normal": (0.0, 0.0, 1.0),
        }
    impact = _xyz(values[5])
    normal = _xyz(values[6])
    return {
        "blocking": bool(values[0]),
        "overlap": bool(values[1]),
        "distance": float(values[3]),
        "impact": impact,
        "normal": normal,
    }


def _xyz(value):
    if hasattr(value, "x"):
        return (float(value.x), float(value.y), float(value.z))
    if len(value) < 3:
        return (0.0, 0.0, 0.0)
    return (float(value[0]), float(value[1]), float(value[2]))


def _vector(x, y, z):
    return _ue().Vector(float(x), float(y), float(z))


def _direction(yaw_deg, pitch_deg):
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    planar = math.cos(pitch)
    return (math.cos(yaw) * planar, math.sin(yaw) * planar, math.sin(pitch))


def _line(world, start, end, ignore):
    unreal = _ue()
    hit = unreal.SystemLibrary.line_trace_single_by_profile(
        world,
        start,
        end,
        unreal.Name(TRACE_PROFILE),
        False,
        ignore,
        unreal.DrawDebugTrace.NONE,
        True,
    )
    # A clear ray comes back as None from this binding. That is open space, not a failed trace.
    if hit is None:
        return {
            "blocking": False,
            "overlap": False,
            "distance": 0.0,
            "impact": (0.0, 0.0, 0.0),
            "normal": (0.0, 0.0, 1.0),
        }
    return _read_hit(hit)


def _stamp_solid(echo_map, impact, normal, walkable, protect, feet_z):
    """The solid is inside the surface. A walkable face keeps the air above it open."""
    nudge = max(echo_map.cell * 0.5, 2.0)
    echo_map.add_solid(
        impact[0] - normal[0] * nudge,
        impact[1] - normal[1] * nudge,
        impact[2] - normal[2] * nudge,
        protect,
        feet_z,
    )
    if walkable:
        return
    echo_map.add_solid(impact[0], impact[1], impact[2], protect, feet_z)


def _integrate(echo_map, eye, direction, reach, hit, protect, feet_z):
    if hit["overlap"]:
        return
    travel = reach
    if hit["blocking"]:
        travel = max(0.0, min(reach, hit["distance"]))
    step = echo_map.cell
    distance = step
    while distance < travel - 1.0:
        point = (
            eye[0] + direction[0] * distance,
            eye[1] + direction[1] * distance,
            eye[2] + direction[2] * distance,
        )
        echo_map.add_empty(point[0], point[1], point[2])
        echo_map.add_open_volume(point[0], point[1], point[2])
        distance += step
    if not hit["blocking"] or hit["distance"] < 5.0:
        return
    normal = hit["normal"]
    impact = hit["impact"]
    walkable = normal[2] >= WALKER.body.get("walk_nz", WALK_NORMAL_Z)
    _stamp_solid(echo_map, impact, normal, walkable, protect, feet_z)
    if walkable:
        echo_map.add_floor(impact[0], impact[1], impact[2])
        # A steep ray hits just ahead of the pawn. Fill that one cell so the
        # floor connects. Painting the impact height any farther writes the top
        # of a ramp back down the slope, and the pawn circles those false floors.
        back = min(echo_map.cell, travel)
        covered = echo_map.cell * 0.5
        while covered < back:
            dist = travel - covered
            if dist <= 1.0:
                break
            echo_map.add_floor(
                eye[0] + direction[0] * dist,
                eye[1] + direction[1] * dist,
                impact[2],
            )
            covered += echo_map.cell * 0.5
        return
    points = (
        impact,
        (
            impact[0] - normal[0] * 2.0,
            impact[1] - normal[1] * 2.0,
            impact[2] - normal[2] * 2.0,
        ),
    )
    for point in points:
        if protect is not None and echo_map.cell_of(point[0], point[1]) == protect:
            continue
        echo_map.add_wall(point[0], point[1], point[2])


def _angle_samples(low, high, step):
    intervals = max(1, int(round((high - low) / step)))
    return [low + (high - low) * index / intervals for index in range(intervals + 1)]


def _build_rays():
    """View directions in a forward cone. A tick's rays are spread through it.

    The lattice faces +X, which is yaw 0 and pitch 0. _aim_direction turns it
    to the camera, and a glance adds yaw on top of that.
    """
    lattice = []
    for yaw in _angle_samples(-CONE_YAW_HALF, CONE_YAW_HALF, CONE_STEP_DEG):
        for pitch in _angle_samples(-CONE_PITCH_DOWN, CONE_PITCH_UP, CONE_STEP_DEG):
            lattice.append(_direction(yaw, pitch))
    count = len(lattice)
    stride = max(1, count // RAYS_PER_TICK)
    while math.gcd(stride, count) != 1:
        stride += 1
    rays = []
    index = 0
    for _step in range(count):
        rays.append(lattice[index])
        index = (index + stride) % count
    return rays


def _aim_direction(local, yaw_deg, pitch_deg):
    """Turn a cone sample so it faces the camera. Pitch stays short of behind."""
    local_yaw = math.degrees(math.atan2(local[1], local[0]))
    flat = math.hypot(local[0], local[1])
    local_pitch = math.degrees(math.atan2(local[2], flat))
    pitched = max(-89.0, min(89.0, wrap_deg(pitch_deg) + local_pitch))
    return _direction(float(yaw_deg) + local_yaw, pitched)


def _glance_yaw(now):
    """Look to one side for a moment, then the other, then back along the path."""
    walker = WALKER
    if now < walker.glance_until:
        return walker.glance_yaw
    if walker.next_glance <= 0.0:
        walker.next_glance = now + GLANCE_PERIOD_S
        return 0.0
    if now < walker.next_glance:
        return 0.0
    walker.glance_side = -1 if walker.glance_side > 0 else 1
    walker.glance_yaw = GLANCE_YAW_DEG * walker.glance_side
    walker.glance_until = now + GLANCE_HOLD_S
    walker.next_glance = walker.glance_until + GLANCE_PERIOD_S
    return walker.glance_yaw


def _wall_in_front(hit, origin, forward, body, feet_z, ramp_cell, echo_map):
    """A wall the body will meet inside the brake reach.

    Walkable ground is not a wall. The linked ramp cell immediately ahead is
    the slope rising into the chest. A surface off to the side of the capsule
    is not this step.
    """
    if not hit["blocking"] or hit["overlap"]:
        return False
    if hit["distance"] > BRAKE_REACH_CM:
        return False
    if hit["normal"][2] >= body.get("walk_nz", WALK_NORMAL_Z):
        return False
    if hit["impact"][2] < feet_z + body["step"]:
        return False
    impact = hit["impact"]
    if ramp_cell is not None and echo_map.cell_of(impact[0], impact[1]) == ramp_cell:
        return False
    along = (impact[0] - origin[0]) * forward[0] + (impact[1] - origin[1]) * forward[1]
    side_x = (impact[0] - origin[0]) - forward[0] * along
    side_y = (impact[1] - origin[1]) - forward[1] * along
    if along <= 0.0:
        return False
    return math.hypot(side_x, side_y) <= body["radius"] + 8.0


def _sense(world, pawn, location, feet_z, yaw, pitch, now, ignore):
    walker = WALKER
    body = walker.body
    echo_map = walker.echo_map
    protect = echo_map.cell_of(location.x, location.y)
    echo_map.add_floor(location.x, location.y, feet_z)
    eye_z = location.z + body["half"] * 0.35
    eye = (location.x, location.y, eye_z)
    reach = body["radius"] * RANGE_CELLS
    if not walker.rays:
        walker.rays = _build_rays()
    look_yaw = float(yaw) + _glance_yaw(now)
    look_pitch = wrap_deg(float(pitch))
    for _index in range(RAYS_PER_TICK):
        local = walker.rays[walker.scan_index % len(walker.rays)]
        walker.scan_index += 1
        direction = _aim_direction(local, look_yaw, look_pitch)
        end = _vector(
            eye[0] + direction[0] * reach,
            eye[1] + direction[1] * reach,
            eye[2] + direction[2] * reach,
        )
        hit = _line(world, _vector(*eye), end, ignore)
        _integrate(echo_map, eye, direction, reach, hit, protect, feet_z)
        walker.rays_cast += 1

    blocked = False
    was_blocked = walker.contact_blocked
    contact_reach = body["radius"] + echo_map.cell
    for direction_index in range(8):
        direction = _direction(direction_index * 45.0, 0.0)
        start = (location.x, location.y, location.z)
        end = _vector(
            start[0] + direction[0] * contact_reach,
            start[1] + direction[1] * contact_reach,
            start[2],
        )
        hit = _line(world, _vector(*start), end, ignore)
        _integrate(echo_map, start, direction, contact_reach, hit, protect, feet_z)
    forward = _direction(yaw, 0.0)
    right = (-forward[1], forward[0])
    floor_here = echo_map.floor_near(protect, feet_z)
    if floor_here is None:
        floor_here = feet_z
    ahead_cell = echo_map.cell_of(
        location.x + forward[0] * echo_map.cell,
        location.y + forward[1] * echo_map.cell,
    )
    ramp_cell = ahead_cell if ahead_cell != protect and echo_map.linked_floor(ahead_cell, floor_here) is not None else None
    # Center and both shoulders. A single ray down the capsule middle misses a
    # wall the body is about to meet at an angle.
    span = body["radius"] * 0.85
    heights = (
        location.z,
        feet_z + max(20.0, body["step"] * 0.5),
        location.z + body["half"] * 0.55,
    )
    origin = (location.x, location.y)
    for side in (-span, 0.0, span):
        for height in heights:
            start = (
                location.x + right[0] * side,
                location.y + right[1] * side,
                height,
            )
            end = _vector(
                start[0] + forward[0] * BRAKE_REACH_CM,
                start[1] + forward[1] * BRAKE_REACH_CM,
                start[2],
            )
            hit = _line(world, _vector(*start), end, ignore)
            _integrate(echo_map, start, forward, BRAKE_REACH_CM, hit, protect, feet_z)
            if _wall_in_front(hit, origin, forward, body, feet_z, ramp_cell, echo_map):
                blocked = True
    walker.contact_blocked = blocked
    if blocked and not was_blocked:
        walker.force_plan = True


def _desired_point(x, y):
    walker = WALKER
    points = walker.path
    if not points:
        return None
    echo_map = walker.echo_map
    lookahead = echo_map.cell * LOOKAHEAD_CELLS
    if walker.path_index >= len(points):
        walker.path_index = len(points) - 1
    if walker.path_index < 0:
        walker.path_index = 0
    while walker.path_index < len(points) - 1:
        point = points[walker.path_index]
        if math.hypot(point[0] - x, point[1] - y) >= lookahead:
            break
        walker.path_index += 1
    return points[walker.path_index]


def _plan(x, y, feet_z, now):
    walker = WALKER
    echo_map = walker.echo_map
    if not walker.force_plan and now < walker.next_plan:
        return
    origin = echo_map.cell_of(x, y)
    if origin != walker.commit_cell:
        walker.avoid_goals = []
        walker.commit_cell = origin
        walker.idle_since = None
    # Frontiers are gone and the map has not grown. Another search would walk
    # every known cell on the editor thread, which is what freezes Play once
    # the level is scanned and the pawn switches to patrol.
    known_clear = walker.frontier_revision == echo_map.revision
    if known_clear and walker.path:
        turn_around = bool(walker.force_plan or walker.goal_cell == origin)
        walker.mode = "patrol"
        walker.force_plan = False
        walker.next_plan = now + PATROL_INTERVAL_S
        if turn_around and len(walker.path) > 1:
            walker.path = list(reversed(walker.path))
            walker.path_index = 0
            walker.goal_cell = echo_map.cell_of(walker.path[-1][0], walker.path[-1][1])
            walker.idle_since = None
        return
    path = choose_path(echo_map, origin, feet_z, walker.goal_cell, walker.avoid_goals)
    mode = "explore"
    if path is None:
        mode = "patrol"
        walker.frontier_revision = echo_map.revision
        path = plan_farthest(echo_map, origin, feet_z, set(walker.avoid_goals)) or [origin]
    else:
        walker.frontier_revision = -1
    if mode != walker.mode:
        walker.mode = mode
        _ue().log("Echo on %s is %s." % (walker.level_name, mode))
    walker.mode = mode
    cells = path or [origin]
    goal = cells[-1]
    same_goal = walker.goal_cell == goal
    walker.goal_cell = goal
    walker.path = [echo_map.center(cell) for cell in cells]
    # The same lookout can come back as a shorter path once the pawn has
    # moved. Keeping the old index walks off the end of that list and the
    # tick stops the explorer on open ground.
    if not same_goal or walker.path_index >= len(walker.path):
        walker.path_index = 0
        walker.idle_since = None
    walker.revision_planned = echo_map.revision
    walker.force_plan = False
    walker.next_plan = now + (PATROL_INTERVAL_S if mode == "patrol" else PLAN_INTERVAL_S)
    if now - walker.summary_at > 0.5:
        walker.summary = echo_map.counts(feet_z)
        walker.summary_at = now


def _facing_cell_clear(x, y, yaw, feet_z):
    """The next cell is clear when its floor links from the floor under the pawn.

    A ramp rises into the chest of the lower cell. That solid is the slope,
    so it must not cancel W. A cube still fails the link, because its wall
    stands above its own floor.
    """
    echo_map = WALKER.echo_map
    here = echo_map.cell_of(x, y)
    ahead_x = x + math.cos(math.radians(yaw)) * echo_map.cell
    ahead_y = y + math.sin(math.radians(yaw)) * echo_map.cell
    ahead = echo_map.cell_of(ahead_x, ahead_y)
    if ahead == here:
        return True
    floor = echo_map.floor_near(here, feet_z)
    if floor is None:
        floor = feet_z
    return echo_map.linked_floor(ahead, floor) is not None


def _steer(controller, pawn, location, feet_z, dt):
    walker = WALKER
    yaw = float(controller.get_control_rotation().yaw)
    point = _desired_point(location.x, location.y)
    if point is None:
        walker.hold_reason = "no path"
        _set_w(False)
        return
    goal = walker.path[-1]
    # Arrival is the goal cell itself. A distance check stops short of the
    # boundary, and the same cell stays the nearest frontier forever.
    here = walker.echo_map.cell_of(location.x, location.y)
    goal_cell = walker.echo_map.cell_of(goal[0], goal[1])
    arrived = here == goal_cell
    if arrived and walker.mode == "patrol":
        origin = walker.echo_map.cell_of(location.x, location.y)
        if not walker.avoid_goals or walker.avoid_goals[-1] != origin:
            walker.avoid_goals.append(origin)
            del walker.avoid_goals[:-8]
        # Ask for a new leg once. _plan turns the current path around instead
        # of searching the finished level again on the next tick.
        walker.force_plan = True
        walker.next_plan = 0.0
        walker.hold_reason = "patrol point reached"
        _set_w(False)
        return
    if arrived and walker.mode == "explore" and len(walker.path) <= 1:
        # The nearby floor is not connected yet. Keep scanning from this cell.
        walker.hold_reason = "waiting for a connected floor"
        _set_w(False)
        return
    if arrived and walker.mode == "explore":
        # This lookout is done. Pick the next unmapped edge on the following tick.
        walker.force_plan = True
    desired = math.degrees(math.atan2(point[1] - location.y, point[0] - location.x))
    error = wrap_deg(desired - yaw)
    step = max(-TURN_RATE_DEG_S * dt, min(TURN_RATE_DEG_S * dt, error))
    if abs(step) > 0.05:
        scale = 1.0
        try:
            scale = float(controller.get_deprecated_input_yaw_scale())
        except Exception:
            scale = 1.0
        if abs(scale) < 0.001:
            scale = 1.0
        controller.add_yaw_input(step / scale)
    clear = _facing_cell_clear(location.x, location.y, yaw, feet_z)
    if arrived:
        walker.hold_reason = "reached the next lookout"
    elif abs(error) > FULL_SPEED_TURN_DEG:
        walker.hold_reason = "turning %.0f degrees" % error
    elif walker.contact_blocked:
        walker.hold_reason = "wall ahead"
    elif not clear:
        walker.hold_reason = "next cell is not free"
    else:
        walker.hold_reason = "walking"
    walk = abs(error) <= FULL_SPEED_TURN_DEG and clear and not walker.contact_blocked and not arrived
    _set_w(walk)
    _watch_stuck(pawn, location, feet_z, yaw, walk)
    _watch_idle(here, arrived, walk)


def _watch_idle(here, arrived, walking):
    """A lookout that never takes a step is abandoned instead of yawing back and forth."""
    walker = WALKER
    if walking or arrived or len(walker.path) <= 1:
        walker.idle_since = None
        return
    now = time.monotonic()
    if walker.idle_since is None:
        walker.idle_since = now
        return
    if now - walker.idle_since < GOAL_HOLD_S:
        return
    if walker.goal_cell is not None and walker.goal_cell != here:
        walker.avoid_goals.append(walker.goal_cell)
        del walker.avoid_goals[:-8]
        walker.goal_cell = None
        walker.force_plan = True
        walker.hold_reason = "leaving a lookout that did not move"
    walker.idle_since = None


def _watch_stuck(pawn, location, feet_z, yaw, walking):
    walker = WALKER
    now = time.monotonic()
    if not walking:
        walker.stuck = []
        walker.stuck_strikes = 0
        return
    walker.stuck.append((now, location.x, location.y))
    while len(walker.stuck) >= 2 and now - walker.stuck[1][0] >= STUCK_WINDOW_S:
        walker.stuck.pop(0)
    started = walker.stuck[0]
    if now - started[0] < STUCK_WINDOW_S:
        return
    progress = math.hypot(location.x - started[1], location.y - started[2])
    if progress >= walker.echo_map.cell * 0.5:
        walker.stuck_strikes = 0
        return
    walker.stuck = []
    walker.stuck_strikes += 1
    _set_w(False)
    if not walker.contact_blocked and walker.stuck_strikes == 1:
        return
    ahead_x = location.x + math.cos(math.radians(yaw)) * walker.echo_map.cell
    ahead_y = location.y + math.sin(math.radians(yaw)) * walker.echo_map.cell
    here = walker.echo_map.cell_of(location.x, location.y)
    ahead = walker.echo_map.cell_of(ahead_x, ahead_y)
    if ahead != here:
        floor = walker.echo_map.floor_near(here, feet_z)
        if floor is None:
            floor = feet_z
        if walker.echo_map.linked_floor(ahead, floor) is not None:
            # Slow progress on a linked ramp is not a wall. Sealing it would
            # make the slope impassable on the next try.
            walker.force_plan = True
            walker.stuck_strikes = 0
            return
        blocked_z = feet_z + walker.body["step"] + 10.0
        walker.echo_map.add_wall(ahead_x, ahead_y, blocked_z)
        walker.echo_map.add_solid(ahead_x, ahead_y, blocked_z)
        _ue().log("Echo marked a blocked cell at (%.0f, %.0f) and is replanning." % (ahead_x, ahead_y))
    walker.force_plan = True
    walker.stuck_strikes = 0


def _tick(delta_time):
    try:
        _tick_body(delta_time)
    except Exception:
        text = traceback.format_exc()
        if text != WALKER.last_error:
            WALKER.last_error = text
            try:
                _ue().log_error("Echo tick failed:\n" + text)
            except Exception:
                pass
        _set_w(False)


def _tick_body(delta_time):
    if honor_clear():
        return
    walker = WALKER
    if not walker.running:
        return
    controller, pawn = _pawn()
    if pawn is None or controller is None:
        stop()
        return
    dt = float(delta_time or 0.016)
    if dt < 0.001:
        dt = 0.016
    if dt > 0.05:
        dt = 0.05
    location = pawn.get_actor_location()
    feet_z = float(location.z) - walker.body["half"]
    rotation = controller.get_control_rotation()
    yaw = float(rotation.yaw)
    pitch = float(rotation.pitch)
    now = time.monotonic()
    world = pawn.get_world()
    _sense(world, pawn, location, feet_z, yaw, pitch, now, [pawn])
    _plan(float(location.x), float(location.y), feet_z, now)
    _steer(controller, pawn, location, feet_z, dt)
    _remember_pawn(location, yaw)
    if now - walker.last_save > 3.0:
        _save_map()


def start():
    honor_clear()
    controller, pawn = _pawn()
    if pawn is None or controller is None:
        raise RuntimeError(
            "Play In Editor is not running on the open level. Call play_game, then get_player_state, then walk_around."
        )
    walker = WALKER
    if walker.running:
        return _describe("Already walking the open level.")
    try:
        import player_bridge

        player_bridge._stop_drive()
    except Exception:
        pass
    body = _measure(pawn)
    level_name = _level_name(pawn)
    if walker.echo_map is not None and walker.level_name and not os.path.isfile(_map_path(walker.level_name)):
        walker.echo_map = None
        walker.loaded_map = False
    same_map = (
        walker.echo_map is not None
        and walker.level_name == level_name
        and abs(walker.echo_map.cell - body["radius"]) < 1.0
    )
    if not same_map:
        walker.echo_map = EchoMap(body["radius"], body["step"], body["height"])
        walker.level_name = level_name
        walker.loaded_map = _load_map(walker.echo_map, level_name)
        walker.rays_cast = 0
        walker.summary = (0, 0, 0, 0, 0)
        if walker.loaded_map:
            _save_map()
    walker.body = body
    walker.reset_motion()
    walker.running = True
    walker.last_error = ""
    _ue().log("Echo brain is looking forward on %s." % level_name)
    walker.handle = _ue().register_slate_post_tick_callback(_tick)
    loaded = " Continued the saved map." if walker.loaded_map else ""
    return (
        "Walking the open level %s. Capsule radius %.0f cm. "
        "Eyes look forward in a wide cone, down toward the floor, and glance to either side. "
        "Space behind the pawn stays unseen until it turns.%s"
        % (level_name, body["radius"], loaded)
    )


def stop():
    walker = WALKER
    was_running = walker.running or walker.handle is not None or walker.holding
    walker.running = False
    handle = walker.handle
    walker.handle = None
    _release_w()
    if handle is not None:
        try:
            _ue().unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
    if walker.echo_map is not None:
        _save_map()
    if not was_running:
        return _describe("Echo is idle.")
    return _describe("Stopped walking the open level.")


def describe():
    return _describe("")


def _describe(prefix):
    walker = WALKER
    if walker.echo_map is None:
        return "Echo has not looked at the open level yet. Ask to walk around once Play is running."
    controller, pawn = _pawn()
    feet_z = None
    where = "Play is not running."
    if pawn is not None:
        location = pawn.get_actor_location()
        feet_z = float(location.z) - walker.body["half"]
        where = "at (%.0f, %.0f, %.0f) on %s" % (location.x, location.y, location.z, walker.level_name)
        walker.summary = walker.echo_map.counts(feet_z)
    free, walls, frontiers, solids, opens = walker.summary
    motion = "walking" if walker.holding else "looking"
    state = walker.mode if walker.running else "idle"
    reason = ""
    if walker.running and not walker.holding and walker.hold_reason:
        reason = " %s." % walker.hold_reason
    around = ""
    if pawn is not None:
        plus_x, minus_x, plus_y, minus_y, plus_z, minus_z, open_above = walker.echo_map.volume_bands(
            float(location.x), float(location.y), float(location.z)
        )
        around = (
            " Solids around the pawn +X %d, -X %d, +Y %d, -Y %d, +Z %d, -Z %d. Open volumes above %d."
            % (plus_x, minus_x, plus_y, minus_y, plus_z, minus_z, open_above)
        )
    text = (
        "%s Echo is %s and %s %s.%s Mapped %d standable cells, %d wall cells, %d frontiers, "
        "%d solid volumes, %d open volumes, %d rays.%s"
        % (prefix, state, motion, where, reason, free, walls, frontiers, solids, opens, walker.rays_cast, around)
    )
    return " ".join(text.split())


def self_test():
    global choose_path, plan_farthest
    echo_map = EchoMap(cell=40, step=45, body_height=180)
    for ix in range(8):
        for iy in range(8):
            echo_map.add_floor(ix * 40 + 20, iy * 40 + 20, 0)
    for iy in range(6):
        echo_map.add_wall(3 * 40 + 20, iy * 40 + 20, 100)
    assert echo_map.standable((0, 3), 0)
    assert not echo_map.standable((3, 3), 0)
    echo_map.add_empty(3 * 40 + 20, 3 * 40 + 20, 40)
    assert not echo_map.standable((3, 3), 0)
    assert echo_map.unknown((9, 0), 0)
    assert echo_map.is_frontier((7, 0), 0)
    assert not echo_map.is_frontier((3, 3), 0)
    path = plan_to(echo_map, (0, 3), (5, 3), 0)
    assert path is not None and path[0] == (0, 3) and path[-1] == (5, 3)
    assert (3, 3) not in path
    assert all(echo_map.standable(cell, 0) for cell in path)
    nearest = plan_nearest(echo_map, (1, 1), 0)
    assert nearest is not None and echo_map.is_frontier(nearest[-1], 0)
    assert all(cell[0] != 3 or cell[1] >= 6 for cell in nearest)
    lone = EchoMap(cell=40, step=45, body_height=180)
    lone.add_floor(20, 20, 0)
    assert plan_nearest(lone, (0, 0), 0) == [(0, 0)]
    lone.add_floor(60, 20, 0)
    grown = plan_nearest(lone, (0, 0), 0)
    assert grown == [(0, 0), (1, 0)]
    ramp = EchoMap(cell=40, step=45, body_height=180)
    for index, height in enumerate((0, 30, 60, 90)):
        ramp.add_floor(index * 40 + 20, 20, height)
    climbed = plan_to(ramp, (0, 0), (3, 0), 0)
    assert climbed == [(0, 0), (1, 0), (2, 0), (3, 0)]
    cube = EchoMap(cell=40, step=45, body_height=180)
    cube.add_floor(20, 20, 0)
    cube.add_floor(60, 20, 200)
    assert plan_to(cube, (0, 0), (1, 0), 0) is None
    echo_map.add_floor(20, -20, -200)
    assert echo_map.known_drop((0, -1), 0)
    assert not echo_map.unknown((0, -1), 0)
    restored = EchoMap(cell=40, step=45, body_height=180)
    assert restored.restore(echo_map.snapshot())
    assert not restored.standable((3, 3), 0)
    assert wrap_deg(270) == -90
    blocked = EchoMap(cell=40, step=45, body_height=180)
    blocked.add_floor(20, 20, 0)
    blocked.add_solid(20, 20, 100)
    assert not blocked.standable((0, 0), 0)
    blocked.add_open_volume(20, 20, 100)
    assert blocked.voxel_of(20, 20, 100) in blocked.solids
    assert blocked.voxel_of(20, 20, 100) not in blocked.opens
    ground = EchoMap(cell=40, step=45, body_height=180)
    ground.add_floor(20, 20, 0)
    ground.add_solid(20, 20, -20)
    assert ground.standable((0, 0), 0)
    ceiling = EchoMap(cell=40, step=45, body_height=180)
    ceiling.add_floor(20, 20, 0)
    ceiling.add_solid(20, 20, 400)
    assert ceiling.standable((0, 0), 0)
    protected = EchoMap(cell=40, step=45, body_height=180)
    assert protected.add_solid(20, 20, 100, protect=(0, 0), feet_z=0.0) is False
    assert protected.voxel_of(20, 20, 100) not in protected.solids
    assert protected.add_solid(20, 20, 400, protect=(0, 0), feet_z=0.0) is True
    _stamp_solid(ground, (20.0, 20.0, 0.0), (0.0, 0.0, 1.0), True, None, 0.0)
    assert ground.voxel_of(20, 20, -20) in ground.solids
    assert ground.voxel_of(20, 20, 10) not in ground.solids
    behind = EchoMap(cell=40, step=45, body_height=180)
    _stamp_solid(behind, (-80.0, 20.0, 100.0), (1.0, 0.0, 0.0), False, None, 0.0)
    assert behind.voxel_of(-100, 20, 100) in behind.solids
    assert behind.voxel_of(-80, 20, 100) in behind.solids
    assert behind.volume_bands(0, 0, 100)[1] > 0
    above = EchoMap(cell=40, step=45, body_height=180)
    _stamp_solid(above, (20.0, 20.0, 300.0), (0.0, 0.0, -1.0), False, None, 0.0)
    assert above.voxel_of(20, 20, 320) in above.solids
    assert above.voxel_of(20, 20, 300) in above.solids
    sky = EchoMap(cell=40, step=45, body_height=180)
    sky.add_open_volume(20, 20, 400)
    assert sky.volume_bands(0, 0, 100)[6] > 0
    def _ray_yaw(ray):
        return math.degrees(math.atan2(ray[1], ray[0]))

    def _ray_pitch(ray):
        return math.degrees(math.atan2(ray[2], math.hypot(ray[0], ray[1])))

    rays = _build_rays()
    assert len(rays) > 64
    for ray in rays:
        length = math.sqrt(sum(component * component for component in ray))
        assert abs(length - 1.0) < 1e-6
        assert ray[0] > 0.0
        assert abs(_ray_yaw(ray)) <= CONE_YAW_HALF + 0.1
        assert -CONE_PITCH_DOWN - 0.1 <= _ray_pitch(ray) <= CONE_PITCH_UP + 0.1
    assert any(abs(_ray_yaw(ray)) > CONE_YAW_HALF - CONE_STEP_DEG for ray in rays)
    assert any(_ray_pitch(ray) < -(CONE_PITCH_DOWN - CONE_STEP_DEG) for ray in rays)
    assert any(_ray_pitch(ray) > CONE_PITCH_UP - CONE_STEP_DEG for ray in rays)
    first_yaws = [_ray_yaw(ray) for ray in rays[:RAYS_PER_TICK]]
    assert max(first_yaws) - min(first_yaws) > CONE_YAW_HALF
    ahead = _aim_direction(_direction(0, 0), 0, 0)
    assert abs(ahead[0] - 1.0) < 1e-6
    side = _aim_direction(_direction(0, 0), 90, 0)
    assert abs(side[0]) < 1e-6 and abs(side[1] - 1.0) < 1e-6
    glanced = _aim_direction(_direction(0, 0), GLANCE_YAW_DEG, 0)
    assert abs(_ray_yaw(glanced) - GLANCE_YAW_DEG) < 0.1
    steep = _aim_direction(_direction(0, -CONE_PITCH_DOWN), 0, -40)
    assert steep[0] > 0.0
    assert _ray_pitch(steep) > -90.0
    WALKER.glance_side = 1
    WALKER.glance_yaw = 0.0
    WALKER.glance_until = 0.0
    WALKER.next_glance = 0.0
    assert _glance_yaw(10.0) == 0.0
    assert abs(WALKER.next_glance - (10.0 + GLANCE_PERIOD_S)) < 1e-6
    assert _glance_yaw(WALKER.next_glance) == -GLANCE_YAW_DEG
    assert _glance_yaw(WALKER.glance_until - 0.01) == -GLANCE_YAW_DEG
    assert _glance_yaw(WALKER.next_glance) == GLANCE_YAW_DEG
    brake_body = {"radius": 40.0, "step": 45.0, "walk_nz": 0.7}
    brake_forward = (1.0, 0.0, 0.0)
    brake_wall = {
        "blocking": True,
        "overlap": False,
        "distance": 150.0,
        "impact": (150.0, 0.0, 100.0),
        "normal": (-1.0, 0.0, 0.0),
    }
    assert _wall_in_front(brake_wall, (0.0, 0.0), brake_forward, brake_body, 0.0, None, None)
    far_wall = dict(brake_wall, distance=250.0, impact=(250.0, 0.0, 100.0))
    assert not _wall_in_front(far_wall, (0.0, 0.0), brake_forward, brake_body, 0.0, None, None)
    floor_hit = dict(brake_wall, normal=(0.0, 0.0, 1.0), impact=(150.0, 0.0, 0.0))
    assert not _wall_in_front(floor_hit, (0.0, 0.0), brake_forward, brake_body, 0.0, None, None)
    side_wall = dict(brake_wall, impact=(150.0, 120.0, 100.0), distance=190.0)
    assert not _wall_in_front(side_wall, (0.0, 0.0), brake_forward, brake_body, 0.0, None, None)
    lip = dict(brake_wall, impact=(80.0, 0.0, 20.0), distance=80.0)
    assert not _wall_in_front(lip, (0.0, 0.0), brake_forward, brake_body, 0.0, None, None)
    ramp_map = EchoMap(cell=40, step=45, body_height=180)
    ramp_hit = dict(brake_wall, impact=(60.0, 0.0, 80.0), distance=60.0)
    assert not _wall_in_front(ramp_hit, (20.0, 20.0), brake_forward, brake_body, 0.0, (1, 0), ramp_map)
    past_ramp = dict(brake_wall, impact=(200.0, 0.0, 100.0), distance=180.0)
    assert _wall_in_front(past_ramp, (20.0, 20.0), brake_forward, brake_body, 0.0, (1, 0), ramp_map)
    kept = EchoMap(cell=40, step=45, body_height=180)
    assert kept.restore(behind.snapshot())
    assert kept.voxel_of(-100, 20, 100) in kept.solids
    legacy = EchoMap(cell=40, step=45, body_height=180)
    assert legacy.restore({"cell": 40, "step": 45, "floors": [[0, 0, 0]], "walls": []})
    assert legacy.standable((0, 0), 0)
    assert len(legacy.solids) == 0
    saved_body = WALKER.body
    WALKER.body = {"walk_nz": 0.7}
    try:
        traced = EchoMap(cell=40, step=45, body_height=180)
        _integrate(
            traced,
            (0.0, 0.0, 100.0),
            (1.0, 0.0, 0.0),
            200.0,
            {
                "blocking": True,
                "overlap": False,
                "distance": 200.0,
                "impact": (200.0, 0.0, 100.0),
                "normal": (-1.0, 0.0, 0.0),
            },
            (0, 0),
            0.0,
        )
        assert traced.voxel_of(80, 0, 100) in traced.opens
        assert traced.voxel_of(220, 0, 100) in traced.solids
        traced.add_open_volume(220, 0, 100)
        assert traced.voxel_of(220, 0, 100) in traced.solids
        assert traced.voxel_of(220, 0, 100) not in traced.opens
        missed = EchoMap(cell=40, step=45, body_height=180)
        _integrate(
            missed,
            (0.0, 0.0, 100.0),
            (0.0, 0.0, 1.0),
            200.0,
            {
                "blocking": False,
                "overlap": False,
                "distance": 0.0,
                "impact": (0.0, 0.0, 0.0),
                "normal": (0.0, 0.0, 1.0),
            },
            None,
            0.0,
        )
        assert missed.voxel_of(0, 0, 180) in missed.opens
        assert len(missed.solids) == 0
    finally:
        WALKER.body = saved_body
    lane = EchoMap(cell=40, step=45, body_height=180)
    for ix in range(-2, 3):
        lane.add_floor(ix * 40 + 20, 20, 0)
    assert choose_path(lane, (0, 0), 0, None, ())[-1] == (1, 0)
    assert choose_path(lane, (0, 0), 0, (1, 0), ())[-1] == (1, 0)
    lane.add_solid(60, 20, 100)
    assert choose_path(lane, (0, 0), 0, (1, 0), ())[-1] == (-1, 0)
    fork = EchoMap(cell=40, step=45, body_height=180)
    fork.add_floor(20, 20, 0)
    fork.add_floor(60, 20, 30)
    fork.add_floor(20, 60, 30)
    assert choose_path(fork, (0, 0), 0, None, ())[-1] == (1, 0)
    assert choose_path(fork, (0, 0), 0, (1, 0), ())[-1] == (1, 0)
    fork.pawn = [20.0, 20.0, 90.0, 15.0]
    assert fork.snapshot()["pawn"] == [20.0, 20.0, 90.0, 15.0]
    stepped = EchoMap(cell=40, step=45, body_height=180)
    for index, height in enumerate((0, 50, 100, 150)):
        stepped.add_floor(index * 40 + 20, 20, height)
    assert plan_to(stepped, (0, 0), (3, 0), 0) == [(0, 0), (1, 0), (2, 0), (3, 0)]
    terrace = EchoMap(cell=40, step=45, body_height=180)
    terrace.add_floor(20, 20, 0)
    terrace.add_floor(60, 20, 90)
    assert terrace.unknown((1, 0), 0) is False
    assert terrace.is_frontier((0, 0), 0) is True
    assert terrace.unknown((0, 1), 0) is True
    saved_map = WALKER.echo_map
    saved_body = WALKER.body
    saved_holding = WALKER.holding
    saved_strikes = WALKER.stuck_strikes
    saved_stuck = list(WALKER.stuck)
    saved_contact = WALKER.contact_blocked
    try:
        slope = EchoMap(cell=40, step=45, body_height=180)
        slope.add_floor(20, 20, 0)
        slope.add_floor(60, 20, 40)
        slope.add_solid(60, 20, 50)
        WALKER.echo_map = slope
        assert _facing_cell_clear(20, 20, 0, 0) is True
        assert slope.wall_in_body((1, 0), 0) is True
        WALKER.body = {"step": 45.0}
        WALKER.holding = False
        WALKER.contact_blocked = False
        WALKER.stuck_strikes = 1
        WALKER.stuck = [(time.monotonic() - 2.0, 20.0, 20.0)]
        _watch_stuck(None, type("Point", (), {"x": 20.0, "y": 20.0})(), 0.0, 0.0, True)
        assert (1, 0) not in slope.walls
        shallow = EchoMap(cell=40, step=45, body_height=180)
        raw = (0.995, 0.0, 0.1)
        scale = math.sqrt(raw[0] * raw[0] + raw[2] * raw[2])
        direction = (raw[0] / scale, 0.0, raw[2] / scale)
        reach = 600.0
        eye = (0.0, 0.0, 50.0)
        impact = (
            eye[0] + direction[0] * reach,
            eye[1] + direction[1] * reach,
            eye[2] + direction[2] * reach,
        )
        _integrate(
            shallow,
            eye,
            direction,
            reach,
            {
                "blocking": True,
                "overlap": False,
                "distance": reach,
                "impact": impact,
                "normal": (0.0, 0.0, 1.0),
            },
            None,
            0.0,
        )
        assert shallow.floor_near(shallow.cell_of(impact[0], impact[1]), impact[2]) is not None
        back_x = impact[0] - direction[0] * (shallow.cell * 3)
        assert shallow.floor_near(shallow.cell_of(back_x, 0.0), impact[2]) is None
        ground = EchoMap(cell=40, step=45, body_height=180)
        ground.add_floor(20, 20, 0)
        ground.add_floor(20, 20, 40)
        ground.add_floor(20, 20, 120)
        assert ground.floors[(0, 0)] == [0.0]
        ground.add_floor(20, 20, 200)
        assert ground.floors[(0, 0)] == [0.0, 200.0]
        lifted = EchoMap(cell=40, step=45, body_height=180)
        lifted.floors[(0, 0)] = [120.0, 0.0, 40.0, 200.0]
        lifted.collapse_floors()
        assert lifted.floors[(0, 0)] == [0.0, 200.0]
        poisoned = EchoMap(cell=40, step=45, body_height=180)
        assert poisoned.restore({"cell": 40, "step": 45, "floors": [[0, 0, 200], [0, 0, 120], [0, 0, 0]], "walls": []})
        assert poisoned.floors[(0, 0)] == [0.0, 200.0]
        short = EchoMap(cell=40, step=45, body_height=180)
        for ix in range(3):
            short.add_floor(ix * 40 + 20, 20, 0)
        WALKER.echo_map = short
        WALKER.mode = "explore"
        WALKER.goal_cell = (2, 0)
        WALKER.path_index = 5
        WALKER.path = [short.center((0, 0))] * 6
        WALKER.force_plan = True
        WALKER.next_plan = 0
        WALKER.avoid_goals = []
        WALKER.commit_cell = (0, 0)
        WALKER.idle_since = None
        WALKER.summary_at = time.monotonic()
        _plan(20.0, 20.0, 0.0, time.monotonic())
        assert WALKER.path_index < len(WALKER.path)
        assert _desired_point(20.0, 20.0) is not None
        capped = EchoMap(cell=40, step=45, body_height=180)
        for index in range(MAX_OPEN_VOLUMES + 30):
            capped.add_open_volume(index * 40 + 20, 20, 100)
        assert len(capped.opens) == MAX_OPEN_VOLUMES
        counted = capped.counts(0)
        assert capped.counts(0) == counted
        route = EchoMap(cell=40, step=45, body_height=180)
        for index in range(4):
            route.add_floor(index * 40 + 20, 20, 0)
        assert plan_farthest(route, (0, 0), 0, set())[-1] == (3, 0)
        saved_frontier = WALKER.frontier_revision
        saved_goal = WALKER.goal_cell
        saved_mode = WALKER.mode
        saved_path = list(WALKER.path)
        saved_next = WALKER.next_plan
        saved_force = WALKER.force_plan
        real_choose = choose_path
        real_farthest = plan_farthest
        def _fail_search(*_args, **_kwargs):
            raise AssertionError("patrol searched the finished level")
        choose_path = _fail_search
        plan_farthest = _fail_search
        try:
            WALKER.echo_map = route
            WALKER.mode = "patrol"
            WALKER.frontier_revision = route.revision
            WALKER.summary = (4, 0, 0, 0, 0)
            WALKER.summary_at = time.monotonic()
            WALKER.path = [route.center((0, 0)), route.center((1, 0)), route.center((3, 0))]
            WALKER.path_index = 2
            WALKER.goal_cell = (3, 0)
            WALKER.force_plan = True
            WALKER.next_plan = 0
            WALKER.commit_cell = (3, 0)
            WALKER.avoid_goals = []
            WALKER.idle_since = None
            WALKER.body = {"step": 45.0, "half": 90.0, "radius": 40.0, "walk_nz": 0.7}
            _plan(route.center((3, 0))[0], route.center((3, 0))[1], 0.0, time.monotonic())
            assert WALKER.goal_cell == (0, 0)
            assert WALKER.force_plan is False
            assert WALKER.path_index == 0
            later = time.monotonic()
            _plan(route.center((3, 0))[0], route.center((3, 0))[1], 0.0, later)
            assert WALKER.goal_cell == (0, 0)
        finally:
            choose_path = real_choose
            plan_farthest = real_farthest
            WALKER.frontier_revision = saved_frontier
            WALKER.goal_cell = saved_goal
            WALKER.mode = saved_mode
            WALKER.path = saved_path
            WALKER.next_plan = saved_next
            WALKER.force_plan = saved_force
        saved_level = WALKER.level_name
        saved_running = WALKER.running
        saved_loaded = WALKER.loaded_map
        saved_handle = WALKER.handle
        import shutil
        import tempfile

        blank = tempfile.mkdtemp()
        try:
            remembered = EchoMap(cell=40, step=45, body_height=180)
            remembered.add_floor(20, 20, 0)
            WALKER.echo_map = remembered
            WALKER.level_name = "Lvl_Test"
            WALKER.running = False
            WALKER.holding = False
            WALKER.handle = None
            WALKER.loaded_map = True
            saved_path = _map_path("Lvl_Test", blank)
            with open(saved_path, "w", encoding="utf-8") as handle:
                handle.write("{}")
            with open(os.path.join(blank, ".clear"), "w", encoding="utf-8") as handle:
                handle.write("clear\n")
            assert honor_clear(blank) is True
            assert WALKER.echo_map is None
            assert WALKER.level_name == ""
            assert WALKER.loaded_map is False
            assert not os.path.isfile(saved_path)
            assert not os.path.isfile(os.path.join(blank, ".clear"))
            assert honor_clear(blank) is False
        finally:
            WALKER.level_name = saved_level
            WALKER.running = saved_running
            WALKER.loaded_map = saved_loaded
            WALKER.handle = saved_handle
            WALKER.holding = saved_holding
            shutil.rmtree(blank, ignore_errors=True)
    finally:
        WALKER.echo_map = saved_map
        WALKER.body = saved_body
        WALKER.holding = saved_holding
        WALKER.stuck_strikes = saved_strikes
        WALKER.stuck = saved_stuck
        WALKER.contact_blocked = saved_contact
    print("echo map self-test ok")


if __name__ == "__main__":
    self_test()
