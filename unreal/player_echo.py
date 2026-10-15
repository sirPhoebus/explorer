"""Eyes for the possessed player on the level open in this Unreal project.

walk_around traces that level, maps where the pawn can stand, and walks
without stepping into walls. It does not load a separate game or map.
"""

import toolset_registry
import unreal

import player_echo_brain


@unreal.uclass()
class EchoTools(unreal.ToolsetDefinition):
    """Walk the possessed player around the level that is open in the editor.

    The pawn traces the open level as it moves. Unknown space is not walked.
    Walls are remembered, and a later clear trace does not erase them.
    Use walk_around when the user says walk around, explore, wander, or map
    the level. Use stop_walk_around when they say stop. Do not hold W yourself
    for that request, and do not move the pawn by setting its transform.
    """

    @toolset_registry.tool_call
    @staticmethod
    def walk_around() -> str:
        """Walk the open level, mapping it and stopping before walls.

        Play In Editor must already be running on the level that is open.
        Call play_game, then get_player_state, and call this only after a pawn
        is possessed. The walk continues until stop_walk_around.

        Returns:
            The open level being walked, and that eyes are tracing it.
        """
        return player_echo_brain.start()

    @toolset_registry.tool_call
    @staticmethod
    def stop_walk_around() -> str:
        """Stop the mapping walk and release movement. Play keeps running.

        Returns:
            How much of the open level has been mapped.
        """
        return player_echo_brain.stop()

    @toolset_registry.tool_call
    @staticmethod
    def get_echo_map() -> str:
        """Report the map built from traces of the open level.

        Returns:
            Standable cells, wall cells, remaining frontiers, and whether the pawn is walking.
        """
        return player_echo_brain.describe()


def register():
    """Register the echo toolset once."""
    if not unreal.ToolsetRegistry.is_toolset_class_registered(EchoTools):
        unreal.ToolsetRegistry.register_toolset_class(EchoTools)
    unreal.log("EchoTools ready for the open level.")
