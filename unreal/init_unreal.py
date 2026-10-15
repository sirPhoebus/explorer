"""Project Python startup. Registers the Play-mode player toolset."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import math

import player_bridge
import player_keys
import unreal


def _camera_axes(controller, pawn):
    yaw = controller.get_control_rotation().yaw if controller else pawn.get_actor_rotation().yaw
    radians = math.radians(float(yaw))
    forward_x = math.cos(radians)
    forward_y = math.sin(radians)
    return (
        unreal.Vector(forward_x, forward_y, 0.0),
        unreal.Vector(-forward_y, forward_x, 0.0),
    )


# The editor keeps the already-imported module. Replace the helper in place.
player_bridge._camera_axes = _camera_axes
try:
    player_bridge._stop_drive()
except Exception:
    pass

player_bridge.register()
player_keys.register()

import importlib

import player_echo
import player_echo_brain

try:
    player_echo_brain.stop()
except Exception:
    pass
importlib.reload(player_echo_brain)
player_echo.register()
