import { eventBus } from '../core/EventBus';
import { CircuitBreaker, CircuitState } from '../core/CircuitBreaker';
import { logger } from '../core/Logger';

/**
 * Enterprise Core Infrastructure Test
 * 
 * Validates:
 * 1. EventBus: Event delivery and correlation_id propagation.
 * 2. CircuitBreaker: State transitions (CLOSED -> OPEN -> HALF_OPEN).
 */

async function testEventBus() {
  console.log("\n🧪 Testing EventBus...");
  let received = false;
  const test_id = 'test-id-123';

  eventBus.subscribe('intel:news_batch', (payload) => {
    if (payload.correlation_id === test_id) {
      received = true;
    }
  });

  eventBus.publish('intel:news_batch', {
    correlation_id: test_id,
    headlines: ['Test Headline'],
    source: 'Test Source'
  });

  // Small delay for async emit
  await new Promise(r => setTimeout(r, 100));

  if (received) {
    console.log("✅ EventBus: Event received with correct correlation_id.");
  } else {
    throw new Error("❌ EventBus: Event not received or ID mismatch.");
  }
}

async function testCircuitBreaker() {
  console.log("\n🧪 Testing CircuitBreaker...");
  const breaker = new CircuitBreaker({ 
    name: 'TestBreaker', 
    failureThreshold: 2, 
    resetTimeoutMs: 500 
  });

  // 1. Initial State: CLOSED
  if (breaker.getStatus().state !== CircuitState.CLOSED) throw new Error("Should start CLOSED");

  // 2. First failure
  try { await breaker.execute(() => Promise.reject(new Error("Fail 1"))); } catch (e) {}
  if (breaker.getStatus().state !== CircuitState.CLOSED) throw new Error("Should stay CLOSED after 1 fail");

  // 3. Second failure -> OPEN
  try { await breaker.execute(() => Promise.reject(new Error("Fail 2"))); } catch (e) {}
  if (breaker.getStatus().state !== CircuitState.OPEN) throw new Error("Should transition to OPEN after 2 fails");

  // 4. Execution in OPEN state should fail fast
  try {
    await breaker.execute(() => Promise.resolve("Success?"));
    throw new Error("Should have failed fast in OPEN state");
  } catch (e: any) {
    if (!e.message.includes("Circuit is OPEN")) throw e;
  }

  // 5. Wait for reset timeout -> HALF_OPEN
  console.log("...waiting for reset timeout...");
  await new Promise(r => setTimeout(r, 600));
  
  // The execute call triggers checkState
  try { await breaker.execute(() => Promise.resolve("Success!")); } catch (e) {}
  if (breaker.getStatus().state !== CircuitState.CLOSED) throw new Error("Should transition back to CLOSED on success");

  console.log("✅ CircuitBreaker: All state transitions verified.");
}

async function runAll() {
  try {
    await testEventBus();
    await testCircuitBreaker();
    console.log("\n✨ CORE INFRASTRUCTURE TESTS PASSED\n");
    process.exit(0);
  } catch (error: any) {
    console.error(`\n❌ TEST FAILED: ${error.message}\n`);
    process.exit(1);
  }
}

runAll();
