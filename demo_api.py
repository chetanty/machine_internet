"""Tiny local REST API for testing the MCP stack end-to-end."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Demo Store", description="A tiny store API for testing Machine Internet.")

_items: dict = {
    "1": {"id": "1", "name": "Apple",  "price": 1.50, "stock": 100},
    "2": {"id": "2", "name": "Banana", "price": 0.75, "stock": 250},
    "3": {"id": "3", "name": "Cherry", "price": 3.00, "stock": 50},
}
_orders: dict = {}
_next_order = 1


class ItemIn(BaseModel):
    name: str
    price: float
    stock: int = 0


class OrderIn(BaseModel):
    item_id: str
    quantity: int


@app.get("/items", summary="List all items")
def list_items(min_price: Optional[float] = None):
    items = list(_items.values())
    if min_price is not None:
        items = [i for i in items if i["price"] >= min_price]
    return items


@app.get("/items/{item_id}", summary="Get item by ID")
def get_item(item_id: str):
    item = _items.get(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    return item


@app.post("/items", summary="Create a new item")
def create_item(item: ItemIn):
    new_id = str(max(int(k) for k in _items) + 1)
    _items[new_id] = {"id": new_id, **item.model_dump()}
    return _items[new_id]


@app.post("/orders", summary="Place an order")
def place_order(order: OrderIn):
    global _next_order
    item = _items.get(order.item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if item["stock"] < order.quantity:
        raise HTTPException(400, "Insufficient stock")
    item["stock"] -= order.quantity
    o = {"id": str(_next_order), "item_id": order.item_id,
         "quantity": order.quantity, "total": item["price"] * order.quantity}
    _orders[str(_next_order)] = o
    _next_order += 1
    return o


@app.get("/orders/{order_id}", summary="Get order by ID")
def get_order(order_id: str):
    o = _orders.get(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    return o


@app.get("/orders", summary="List all orders")
def list_orders():
    return list(_orders.values())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
