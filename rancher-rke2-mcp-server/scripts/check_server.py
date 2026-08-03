from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def check(url: str, token: str, config_path: str | None) -> None:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}
    ) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            tools = await client.list_tools()
            names = [tool.name for tool in tools.tools]
            capability_result = await client.call_tool("get_capabilities", {})
            output = {
                "url": url,
                "tools": names,
                "capabilities": capability_result.structured_content,
            }
            if config_path:
                yaml_text = Path(config_path).read_text(encoding="utf-8")
                validation_result = await client.call_tool(
                    "validate_config",
                    {"config": yaml_text},
                )
                validation = validation_result.structured_content
                output["validation"] = validation
                if validation and validation.get("ok"):
                    digest = validation["data"]["config_digest"]
                    plan_result = await client.call_tool(
                        "build_plan",
                        {
                            "config_digest": digest,
                            "target_components": ["all"],
                        },
                    )
                    plan = plan_result.structured_content
                    output["plan"] = plan
                    if plan and plan.get("ok"):
                        plan_id = plan["data"]["plan"]["plan_id"]
                        get_result = await client.call_tool(
                            "get_plan",
                            {"plan_id": plan_id},
                        )
                        output["get_plan"] = get_result.structured_content
            print(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--token-file",
        default=os.environ.get(
            "RANCHER_RKE2_MCP_TOKEN_FILE",
            "/run/secrets/mcp_bearer_token",
        ),
    )
    parser.add_argument("--config")
    args = parser.parse_args()
    token = Path(args.token_file).read_text(encoding="utf-8").rstrip("\r\n")
    if not token:
        parser.error("--token-file must point to a non-empty token file")
    asyncio.run(check(args.url, token, args.config))


if __name__ == "__main__":
    main()
