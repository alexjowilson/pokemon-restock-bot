async def check_product(product: dict) -> dict:
    """
    Demo monitor: always returns in_stock=True once.
    Replace this later with Walmart/Target scraping logic.
    """
    print("inside check_product()")
    return {
        "in_stock": True,
        "price": None,
        "url": product["url"],
        "name": product["name"],
        "retailer": product.get("retailer", "demo"),
        "image_url": None,
    }
