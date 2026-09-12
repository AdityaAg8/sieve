"""Fixture: a FastMCP server whose tools are decorated functions with typed parameters."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("files")


@mcp.tool()
def read_file(path: str, limit: int = 10) -> str:
    """Reads a file from disk.

    Longer prose that must not land in the description's first paragraph.
    """
    return path[:limit]


@mcp.tool(name="list_dir", description="Lists a directory.")
async def list_directory(path: str, recursive: bool = False, depth: float = 1.0) -> list[str]:
    """Docstring loses to the explicit description kwarg."""
    return [path] if recursive else [str(depth)]


@mcp.tool()
def search(query: str, tags: list[str], options: dict[str, str], strict: bool = True) -> str:
    """Searches the index."""
    return query + str(tags) + str(options) + str(strict)


def not_a_tool(path: str) -> str:
    """No decorator, so not a tool."""
    return path
