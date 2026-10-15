"""Key and mouse-button tools for the possessed player."""

import toolset_registry
import unreal

import player_key_input


@unreal.uclass()
class PlayerKeyTools(unreal.ToolsetDefinition):
    """Press keys in the playing game.

    Use these calls when the user names a key, a mouse button, or text to type.
    Names are Unreal key names: W, A, S, D, SpaceBar, LeftShift, E, One, Up, F1,
    LeftMouseButton. A plus sign holds a chord, such as LeftShift+E.
    Walking with player_bridge.PlayerTools move_player still works while keys are held.
    """

    @toolset_registry.tool_call
    @staticmethod
    def press_player_key(key: str, seconds: float = 0.12) -> str:
        """Press a key or mouse button in the playing game.

        Args:
            key: Key name or chord. Examples: E, SpaceBar, W, LeftShift+E, LeftMouseButton.
            seconds: How long to hold it. 0 holds until release_player_key. Maximum 30.

        Returns:
            The key that was sent to the playing game.
        """
        return player_key_input.press(key, seconds)

    @toolset_registry.tool_call
    @staticmethod
    def release_player_key(key: str) -> str:
        """Release one injected key or chord. Play keeps running.

        Args:
            key: The same name passed to press_player_key.

        Returns:
            Confirmation that the key was released.
        """
        return player_key_input.release(key)

    @toolset_registry.tool_call
    @staticmethod
    def release_player_keys() -> str:
        """Release every injected key. Does not stop move_player.

        Returns:
            Confirmation that injected keys were released.
        """
        return player_key_input.release_all()

    @toolset_registry.tool_call
    @staticmethod
    def type_player_text(text: str) -> str:
        """Type text into the playing game, one character at a time.

        Args:
            text: Up to 80 characters. Uses the current keyboard layout.

        Returns:
            How many characters were queued.
        """
        return player_key_input.type_text(text)

    @toolset_registry.tool_call
    @staticmethod
    def get_player_keys() -> str:
        """Report which injected keys the pawn currently has down.

        Returns:
            Held keys, and whether the player controller sees each one.
        """
        return player_key_input.describe()


def register():
    """Register the key toolset once and log that it can build a key."""
    if not unreal.ToolsetRegistry.is_toolset_class_registered(PlayerKeyTools):
        unreal.ToolsetRegistry.register_toolset_class(PlayerKeyTools)
    try:
        sample = player_key_input.make_key("W")
        unreal.log("PlayerKeyTools ready. Key sample " + str(sample))
    except Exception as exc:
        unreal.log_error("PlayerKeyTools key init failed: " + str(exc))
