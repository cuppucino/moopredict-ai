import sys
import os
from loguru import logger

# Add project root to path so we can import services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from services._legacy.market_research import research_service

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_earnings_news.py SYMBOL")
        sys.exit(1)
        
    symbol = sys.argv[1].upper()
    print(f"🚀 Researching Catalyst for {symbol}...")
    
    try:
        report = research_service.perform_deep_dive(symbol)
        print("\n" + report)
    except Exception as e:
        logger.error(f"Error researching {symbol}: {e}")
        print(f"❌ Failed to research {symbol}. Check logs.")

if __name__ == "__main__":
    main()
