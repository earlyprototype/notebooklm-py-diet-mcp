"""Verify the installed diet server through its stdio entry point."""

import shutil
import sysconfig
from importlib.metadata import distribution

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_installed_diet_launcher_lists_research_tool(tmp_path):
    scripts = distribution("notebooklm-py-diet-mcp").entry_points.select(group="console_scripts")
    sdk_scripts = distribution("notebooklm-py").entry_points.select(group="console_scripts")
    assert scripts.names.isdisjoint(sdk_scripts.names)

    command = shutil.which("notebooklm-diet-mcp", path=sysconfig.get_path("scripts"))
    assert command, "Install this package to create the diet server launcher."
    params = StdioServerParameters(
        command=command,
        args=[],
        env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "NOTEBOOKLM_HOME": str(tmp_path / ".notebooklm")},
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
    assert "research_and_import" in {tool.name for tool in tools.tools}
