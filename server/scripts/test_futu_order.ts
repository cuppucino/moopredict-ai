import { futu_service } from "../services/futuService";
import { logger } from "../core/Logger";
import { config } from "../core/Config";

/**
 * standalone test script to verify FutuOpenD connectivity and paper trading.
 * 
 * Usage: npx tsx server/scripts/test_futu_order.ts
 */

async function run_test() {
  logger.info("--- Starting FutuOpenD Integration Test ---");
  logger.info(`Mode: ${config.FUTU_TRADE_MODE}`);
  logger.info(`Host: ${config.FUTU_HOST}:${config.FUTU_PORT}`);

  try {
    // 1. Wait for connection (or fail fast)
    logger.info("[Test] Checking connection...");
    // The service connects on constructor — wait for login handshake
    await new Promise(resolve => setTimeout(resolve, 18000));

    // 2. Fetch Balance
    logger.info("[Test] Fetching account balance...");
    const balance = await futu_service.get_account_balance();
    logger.info("[Test] Balance retrieved:", balance);

    if (balance.total_assets === 0 && config.FUTU_TRADE_MODE === 'PAPER') {
       logger.warn("[Test] Total assets is 0. This is expected if FutuOpenD is not running or no paper account is found.");
    }

    // 3. Fetch Positions
    logger.info("[Test] Fetching current positions...");
    const positions = await futu_service.get_real_positions();
    logger.info(`[Test] Found ${positions.length} active positions.`);

    // 4. Place Paper Order (only in PAPER mode for safety)
    if (config.FUTU_TRADE_MODE === 'PAPER') {
      const SYMBOL = "00700"; // Tencent — HK stock, we have LV1 authority
      logger.info(`[Test] Placing paper BUY order for 100 shares of ${SYMBOL} (1 board lot)...`);

      const order_res = await futu_service.place_order(SYMBOL, "BUY", 100);
      
      if (order_res.success) {
        logger.info(`[Test] ✅ SUCCESS: Order placed. ID: ${order_res.order_id}`);
      } else {
        logger.error(`[Test] ❌ FAILED: ${order_res.error}`);
      }
    } else {
      logger.warn("[Test] Skipping order placement because mode is LIVE. Safety first!");
    }

    logger.info("--- Test Sequence Complete ---");
  } catch (error: any) {
    logger.error("[Test] CRITICAL FAILURE:", { error: error.message });
  } finally {
    process.exit(0);
  }
}

run_test();
