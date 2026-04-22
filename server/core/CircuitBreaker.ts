import { logger } from './Logger';

/**
 * Enterprise Circuit Breaker for Resilience.
 * 
 * States:
 * CLOSED (Normal) → OPEN (Failing) → HALF_OPEN (Probing)
 * 
 * Prevents cascading failures when external services (Futu, Ollama, APIs) are down.
 */

export enum CircuitState {
  CLOSED = 'CLOSED',
  OPEN = 'OPEN',
  HALF_OPEN = 'HALF_OPEN'
}

export interface CircuitOptions {
  failureThreshold: number; // Consecutive failures to open circuit
  resetTimeoutMs: number;  // Time to wait before half-open probe
  name: string;            // Name for logging
}

const DEFAULT_OPTIONS: CircuitOptions = {
  failureThreshold: 3,
  resetTimeoutMs: 60 * 1000,
  name: 'DefaultBreaker'
};

export class CircuitBreaker {
  private state: CircuitState = CircuitState.CLOSED;
  private failureCount = 0;
  private lastError: Error | null = null;
  private nextProbeTime = 0;
  private options: CircuitOptions;

  constructor(options: Partial<CircuitOptions> = {}) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
  }

  /**
   * Execute an async function with circuit breaker protection.
   */
  public async execute<T>(fn: () => Promise<T>): Promise<T> {
    this.checkState();

    if (this.state === CircuitState.OPEN) {
      throw new Error(`[CircuitBreaker] Circuit is OPEN for "${this.options.name}". Last error: ${this.lastError?.message || 'N/A'}`);
    }

    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (error: any) {
      this.onFailure(error);
      throw error;
    }
  }

  /**
   * Internal state management.
   */
  private checkState(): void {
    if (this.state === CircuitState.OPEN && Date.now() >= this.nextProbeTime) {
      this.setState(CircuitState.HALF_OPEN);
    }
  }

  private setState(newState: CircuitState): void {
    if (this.state !== newState) {
      logger.warn(`[CircuitBreaker] ${this.options.name} State Change: ${this.state} → ${newState}`);
      this.state = newState;
    }
  }

  private onSuccess(): void {
    if (this.state === CircuitState.HALF_OPEN || this.state === CircuitState.OPEN) {
      this.setState(CircuitState.CLOSED);
    }
    this.failureCount = 0;
    this.lastError = null;
  }

  private onFailure(error: Error): void {
    this.failureCount++;
    this.lastError = error;
    
    logger.error(`[CircuitBreaker] ${this.options.name} Failure (${this.failureCount}/${this.options.failureThreshold}): ${error.message}`);

    if (this.state === CircuitState.CLOSED && this.failureCount >= this.options.failureThreshold) {
      this.openCircuit();
    } else if (this.state === CircuitState.HALF_OPEN) {
      // Re-open if probe fails
      this.openCircuit();
    }
  }

  private openCircuit(): void {
    this.setState(CircuitState.OPEN);
    this.nextProbeTime = Date.now() + this.options.resetTimeoutMs;
  }

  /**
   * Public observability
   */
  public getStatus() {
    return {
      state: this.state,
      failures: this.failureCount,
      lastError: this.lastError?.message || null,
      nextProbe: this.state === CircuitState.OPEN ? new Date(this.nextProbeTime).toISOString() : null
    };
  }

  /**
   * Manually reset circuit to CLOSED state. Use via admin API endpoint.
   */
  public reset(): void {
    this.state = CircuitState.CLOSED;
    this.failureCount = 0;
    this.lastError = null;
    this.nextProbeTime = 0;
    logger.info(`[CircuitBreaker] ${this.options.name} manually reset to CLOSED`);
  }
}
