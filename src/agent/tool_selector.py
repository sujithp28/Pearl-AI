from dataclasses import dataclass


@dataclass(slots=True)
class ToolCall:
    """
    Represents a selected tool and its arguments.
    """

    tool_name: str
    args: tuple
    kwargs: dict


class ToolSelector:
    """
    Rule-based tool selector.

    Later this class will be replaced by the LLM.
    """

    def select(self, prompt: str) -> ToolCall | None:
        prompt = prompt.lower()

        if "read" in prompt and "readme" in prompt:
            return ToolCall(
                tool_name="read_file",
                args=("README.md",),
                kwargs={},
            )

        if "pwd" in prompt:
            return ToolCall(
                tool_name="pwd",
                args=(),
                kwargs={},
            )

        return None