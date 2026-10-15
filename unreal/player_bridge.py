"""Drive the locally controlled pawn while Play In Editor is running.

Movement is applied every editor frame, so a request lasts for the given
number of seconds instead of a single simulation tick.
"""

import math
import time

import toolset_registry
import unreal

_DRIVE = {
    "forward": 0.0,
    "strafe": 0.0,
    "until": None,
    "handle": None,
    "keys": [],
}


def _level_editor():
    editor = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if editor is None:
        raise RuntimeError("Level editor is not available.")
    return editor


def _playing():
    try:
        return bool(_level_editor().is_in_play_in_editor())
    except Exception:
        return False


def _pawn():
    """Return (controller, pawn) for the local player in the play world."""
    if not _playing():
        return None, None
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = editor.get_game_world() if editor else None
    if world is None:
        return None, None
    controller = unreal.GameplayStatics.get_player_controller(world, 0)
    pawn = controller.get_controlled_pawn() if controller else None
    return controller, pawn


def _camera_axes(controller, pawn):
    # Yaw 0 faces +X and positive yaw turns toward +Y. Right is yaw + 90.
    yaw = controller.get_control_rotation().yaw if controller else pawn.get_actor_rotation().yaw
    radians = math.radians(float(yaw))
    forward_x = math.cos(radians)
    forward_y = math.sin(radians)
    return (
        unreal.Vector(forward_x, forward_y, 0.0),
        unreal.Vector(-forward_y, forward_x, 0.0),
    )


def _clamp_axis(value):
    return max(-1.0, min(1.0, float(value)))


def _desired_move_keys():
    # The third-person character walks from W A S D. Those keys are camera-relative.
    names = []
    if _DRIVE["forward"] > 0.001:
        names.append("W")
    elif _DRIVE["forward"] < -0.001:
        names.append("S")
    if _DRIVE["strafe"] > 0.001:
        names.append("D")
    elif _DRIVE["strafe"] < -0.001:
        names.append("A")
    return names


def _stop_drive():
    handle = _DRIVE.get("handle")
    names = list(_DRIVE.get("keys") or [])
    _DRIVE["forward"] = 0.0
    _DRIVE["strafe"] = 0.0
    _DRIVE["until"] = None
    _DRIVE["handle"] = None
    _DRIVE["keys"] = []
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
    if not names:
        return
    import player_key_input

    for name in names:
        try:
            player_key_input.release(name)
        except Exception:
            pass


def _hold_move_keys():
    import player_key_input

    desired = _desired_move_keys()
    current = list(_DRIVE.get("keys") or [])
    if current == desired:
        return
    for name in current:
        if name not in desired:
            try:
                player_key_input.release(name)
            except Exception:
                pass
    until = _DRIVE["until"]
    seconds = 0.0 if until is None else max(0.05, until - time.monotonic())
    held = []
    for name in desired:
        if name not in current:
            player_key_input.press(name, seconds)
        held.append(name)
    _DRIVE["keys"] = held


def _tick(_delta_time):
    until = _DRIVE["until"]
    if until is not None and time.monotonic() >= until:
        _stop_drive()
        return
    _controller, pawn = _pawn()
    if pawn is None:
        _stop_drive()
        return
    _hold_move_keys()


def _ensure_tick():
    if _DRIVE["handle"] is None:
        _DRIVE["handle"] = unreal.register_slate_post_tick_callback(_tick)


def _require_pawn():
    controller, pawn = _pawn()
    if pawn is None:
        raise RuntimeError(
            "Play In Editor is not running, or no pawn is possessed. Call play_game first."
        )
    return controller, pawn


def _format_state():
    controller, pawn = _pawn()
    if pawn is None:
        return "Not playing. Call play_game before driving the player."
    location = pawn.get_actor_location()
    velocity = pawn.get_velocity()
    speed = math.sqrt(velocity.x * velocity.x + velocity.y * velocity.y + velocity.z * velocity.z)
    yaw = controller.get_control_rotation().yaw if controller else pawn.get_actor_rotation().yaw
    holding = abs(_DRIVE["forward"]) > 0.001 or abs(_DRIVE["strafe"]) > 0.001
    move = (
        f" moving forward {_DRIVE['forward']:.2f} strafe {_DRIVE['strafe']:.2f}"
        if holding
        else " standing"
    )
    return (
        f"Playing as {pawn.get_name()} at ({location.x:.0f}, {location.y:.0f}, {location.z:.0f}) cm, "
        f"speed {speed:.0f} cm/s, yaw {yaw:.0f}.{move}."
    )


@unreal.uclass()
class PlayerTools(unreal.ToolsetDefinition):
    """Drive the possessed player while the editor is playing.

    look_player turns the camera. move_player holds W, A, S, or D, which this
    character already treats as camera-relative. Do not move the pawn by
    setting its actor transform.
    """

    @toolset_registry.tool_call
    @staticmethod
    def get_player_state() -> str:
        """Report whether Play is running and where the possessed pawn is.

        Returns:
            Playing status, location in centimeters, speed, and any held move.
        """
        return _format_state()

    @toolset_registry.tool_call
    @staticmethod
    def play_game() -> str:
        """Start Play In Editor in the active level viewport.

        Returns:
            Whether play was requested or was already running.
        """
        editor = _level_editor()
        if editor.is_in_play_in_editor():
            return _format_state()
        editor.editor_request_begin_play()
        return "Play requested. Call get_player_state before the next move."

    @toolset_registry.tool_call
    @staticmethod
    def stop_game() -> str:
        """Stop Play In Editor and release any held movement.

        Returns:
            Confirmation that play was stopped.
        """
        _stop_drive()
        editor = _level_editor()
        if editor.is_in_play_in_editor():
            editor.editor_request_end_play()
            return "Play stopped."
        return "Play was already stopped."

    @toolset_registry.tool_call
    @staticmethod
    def move_player(forward: float, strafe: float, seconds: float = 1.5) -> str:
        """Hold camera-relative walk input on the possessed pawn.

        Args:
            forward: -1 back through 1 forward, relative to the camera yaw.
            strafe: -1 left through 1 right, relative to the camera yaw.
            seconds: How long to hold the input. 0 holds until stop_player. Maximum 30.

        Returns:
            The input that is now being held.
        """
        _require_pawn()
        fwd = _clamp_axis(forward)
        side = _clamp_axis(strafe)
        duration = float(seconds)
        if duration < 0.0:
            duration = 0.0
        if duration > 30.0:
            duration = 30.0
        if abs(fwd) < 0.001 and abs(side) < 0.001:
            _stop_drive()
            return "Player stopped."
        _DRIVE["forward"] = fwd
        _DRIVE["strafe"] = side
        _DRIVE["until"] = None if duration == 0.0 else time.monotonic() + duration
        _ensure_tick()
        held = "until stop_player" if duration == 0.0 else f"for {duration:.1f}s"
        return f"Moving forward {fwd:.2f} strafe {side:.2f} {held}."

    @toolset_registry.tool_call
    @staticmethod
    def stop_player() -> str:
        """Release held walk input. Play keeps running.

        Returns:
            Confirmation that movement input was cleared.
        """
        _stop_drive()
        return "Player movement released."

    @toolset_registry.tool_call
    @staticmethod
    def jump_player() -> str:
        """Make the possessed character jump once.

        Returns:
            Confirmation that jump was pressed.
        """
        _controller, pawn = _require_pawn()
        if not isinstance(pawn, unreal.Character):
            raise RuntimeError(f"{pawn.get_name()} is not a Character and cannot jump.")
        pawn.jump()
        return f"{pawn.get_name()} jumped."

    @toolset_registry.tool_call
    @staticmethod
    def look_player(yaw_degrees: float, pitch_degrees: float) -> str:
        """Turn the player view. Positive yaw turns right. Positive pitch looks up.

        Args:
            yaw_degrees: Degrees added to the control yaw.
            pitch_degrees: Degrees added to the control pitch, clamped to -80..80.

        Returns:
            The resulting control yaw and pitch.
        """
        controller, _pawn_actor = _require_pawn()
        if controller is None:
            raise RuntimeError("The possessed pawn has no player controller.")
        # Same path as the mouse. ControlRotation is not a property of the pawn,
        # and writing it with SetObjectProperties is rejected.
        before = controller.get_control_rotation()
        pitch = max(-80.0, min(80.0, float(before.pitch) + float(pitch_degrees)))
        yaw_delta = float(yaw_degrees)
        pitch_delta = pitch - float(before.pitch)

        def _scaled(delta, scale):
            try:
                value = float(scale)
            except Exception:
                value = 1.0
            if abs(value) < 0.001:
                value = 1.0
            return delta / value

        controller.add_yaw_input(_scaled(yaw_delta, controller.get_deprecated_input_yaw_scale()))
        controller.add_pitch_input(_scaled(pitch_delta, controller.get_deprecated_input_pitch_scale()))
        yaw = float(before.yaw) + yaw_delta
        return f"Looking yaw {yaw:.0f} pitch {pitch:.0f}."


def register():
    """Register the toolset with the editor if it is not already registered."""
    if not unreal.ToolsetRegistry.is_toolset_class_registered(PlayerTools):
        unreal.ToolsetRegistry.register_toolset_class(PlayerTools)
    unreal.log("PlayerTools ready: " + PlayerTools.get_player_state())
