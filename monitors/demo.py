async def check_product(product: dict) -> dict:
    """Demo monitor: reports whatever `demo_in_stock` says in config.yaml.

    Flip it between true and false (and restart) to watch the alert pipeline work.
    """
    return {
        "in_stock": bool(product.get("demo_in_stock", True)),
        "price": product.get("demo_price"),
        "image_url": None,
    }
