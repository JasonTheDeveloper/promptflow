from promptflow.core import tool
from promptflow.connections import CustomConnection


@tool(
    name="tool_func",
    description="This is tool_func tool",
)
def tool_func(connection: CustomConnection, input_text: str) -> str:
    # Replace with your tool code.
    # Usually connection contains configs to connect to an API.
    # Use CustomConnection is a dict. You can use it like: connection.api_key, connection.api_base
    # Not all tools need a connection. You can remove it if you don't need it.
    return "Hello " + input_text