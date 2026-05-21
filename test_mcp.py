import asyncio
import json
from mcp.client.sse import sse_client
from mcp import ClientSession

MCP_URL = "http://localhost:8001/mcp"


async def call(session: ClientSession, tool: str, args: dict, timeout: float = 8.0):
    return await asyncio.wait_for(session.call_tool(tool, args), timeout=timeout)


async def main():
    async with sse_client(MCP_URL) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            print("=== tools ===")
            tools = await session.list_tools()
            for t in tools.tools:
                print(" ", t.name, "-", t.description[:65])

            print()
            print("=== list_items ===")
            res = await call(session, "list_items", {})
            print(res.content[0].text)

            print()
            print("=== list_items (min_price=1.0) ===")
            res = await call(session, "list_items", {"min_price": 1.0})
            print(res.content[0].text)

            print()
            print("=== get_item (id=2) ===")
            res = await call(session, "get_item", {"item_id": "2"})
            print(res.content[0].text)

            print()
            print("=== place_order (item_id=3, quantity=2) ===")
            res = await call(session, "place_order", {"item_id": "3", "quantity": 2})
            order = json.loads(res.content[0].text)
            print(res.content[0].text)

            print()
            print("=== list_orders ===")
            res = await call(session, "list_orders", {})
            print(res.content[0].text)

            print()
            order_id = str(order.get("id", "1"))
            print(f"=== get_order (id={order_id}) ===")
            res = await call(session, "get_order", {"order_id": order_id})
            print(res.content[0].text)

            print()
            print("=== create_item ===")
            res = await call(session, "create_item", {"name": "Mango", "price": 2.25, "stock": 40})
            print(res.content[0].text)


asyncio.run(main())
