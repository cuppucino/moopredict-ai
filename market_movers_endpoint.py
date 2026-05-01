from services.market_movers import market_movers_service
from loguru import logger

async def get_market_movers(limit: int = 10):
    """
    Wrapper function for the market movers endpoint.
    This matches the interface requested by the user.
    """
    try:
        return await market_movers_service.get_movers(limit)
    except Exception as e:
        logger.error(f"Error fetching market movers: {e}")
        return {
            "gainers": [],
            "losers": [],
            "most_active": [],
            "error": str(e)
        }
