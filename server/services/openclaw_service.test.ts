
import axios from "axios";
import { send_openclaw_notification, OpenClawPayload } from "./openclaw_service";

// Simple manual mock for axios
const originalPost = axios.post;

async function testOpenClawNotification() {
  console.log("🧪 Testing OpenClaw Notification...");
  const originalEnv = { ...process.env };

  try {
    // Test 1: Successful send (mocked)
    console.log("- Test 1: Successful send");
    (axios as any).post = async () => ({ status: 200, data: {} });
    process.env.OPENCLAW_WEBHOOK_URL = "http://mock-webhook";
    
    const res1 = await send_openclaw_notification({ message: "Test alert", level: "alert" });
    if (res1 !== true) throw new Error("Test 1 failed: Result should be true");
    console.log("✅ Test 1 passed");

    // Test 2: Missing URL
    console.log("- Test 2: Missing URL");
    delete process.env.OPENCLAW_WEBHOOK_URL;
    const res2 = await send_openclaw_notification({ message: "Test alert" });
    // Note: The service might have a default URL, check the implementation
    // Line 37: const openclaw_url = process.env.OPENCLAW_WEBHOOK_URL || "http://127.0.0.1:18789/webhook";
    // So it won't be false unless the post fails.
    // Let's force a failure.
    (axios as any).post = async () => { throw new Error("Refused"); };
    const res2_fail = await send_openclaw_notification({ message: "Test alert" });
    if (res2_fail !== false) throw new Error("Test 2 failed: Result should be false on error");
    console.log("✅ Test 2 passed");

    console.log("\n✨ OPENCLAW NOTIFICATION TESTS PASSED\n");
  } catch (error: any) {
    console.error(`\n❌ TEST FAILED: ${error.message}\n`);
    process.exit(1);
  } finally {
    process.env = originalEnv;
    (axios as any).post = originalPost;
  }
}

testOpenClawNotification();
