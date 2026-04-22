import { EventEmitter } from 'events';
import { v4 as uuidv4 } from 'uuid';
import { logger } from './Logger';

/**
 * Enterprise Event Bus for MooPredict-AI 4-Agent Pipeline.
 * 
 * Includes:
 * 1. Typed events (via PipelineEvents interface)
 * 2. Automatic correlation ID generation & propagation
 * 3. Structured logging per event
 * 4. Dead letter detection (logs unhandled events)
 * 5. Event history ring buffer (last 100 events)
 */

export interface BaseEvent {
  correlation_id: string; // Tracing ID across agents
  timestamp: string | Date; // ISO format or Date object
}

export interface PipelineEvents {
  // Team A (Intelligence) → Team B (Analyst)
  'intel:news_batch': BaseEvent & {
    headlines: string[];
    source?: string;
  };

  'intel:social_batch': BaseEvent & {
    items: any[];
    source?: string;
  };

  // Team B (Analyst) → Team C (Strategist)
  'analyst:opportunities': BaseEvent & {
    opportunities: any[];
    source: string;
  };

  // Team C (Strategist) → Team D (Executor)
  'strategy:trade_plan': BaseEvent & {
    symbol: string;
    action: 'BUY' | 'SELL';
    entry_price: number;
    best_target: number;
    safe_target: number;
    stop_loss: number;
    max_hold_days: number;
    confidence: number;
    reasoning: string;
    catalyst_id: number | null;
  };

  // Team D (Executor) → Team D (Auditor) / Herald
  'executor:result': BaseEvent & {
    symbol: string;
    action: string;
    qty: number;
    price: number;
    success: boolean;
  };

  'executor:position_update': BaseEvent & {
    symbol: string;
    pnl: number;
    qty: number;
    entry_price: number;
    exit_price: number;
    event: 'STOP_LOSS' | 'TAKE_PROFIT' | 'TIME_EXPIRATION' | 'MANUAL';
  };

  // Team E (Reporter) Triggers
  'system:daily_report': BaseEvent;
  'system:weekly_report': BaseEvent;

  // General System Events
  'system:health': BaseEvent & {
    agent: string;
    status: 'healthy' | 'degraded' | 'down';
    details: Record<string, unknown>;
  };

  'system:hourly_flush': BaseEvent;
  
  'system:announcement': BaseEvent & {
    message: string;
    level: 'info' | 'important';
  };
}

type EventName = keyof PipelineEvents;

export class EventBus extends EventEmitter {
  private history: Array<{ name: EventName; payload: any }> = [];
  private readonly MAX_HISTORY = 100;

  constructor() {
    super();
    this.setMaxListeners(50);
  }

  /**
   * Emit a typed event with a correlation ID.
   * If correlation_id is not provided, a new UUID v4 is generated.
   */
  public publish<T extends EventName>(name: T, payload: Omit<PipelineEvents[T], keyof BaseEvent> & Partial<BaseEvent>): string {
    const correlation_id = payload.correlation_id || uuidv4();
    const timestamp = payload.timestamp || new Date().toISOString();
    const fullPayload = { ...payload, correlation_id, timestamp } as PipelineEvents[T];

    // Log the event for traceability
    logger.info(`[EventBus] Publish: ${name}`, {
      event: name,
      correlation_id,
      symbol: (payload as any).symbol || 'N/A'
    });

    // Check for listeners (Dead Letter detection)
    if (this.listenerCount(name) === 0) {
      logger.warn(`[EventBus] Dead Letter: No listeners for event "${name}"`, { event: name, correlation_id });
    }

    // Add to history ring buffer
    this.history.push({ name, payload: fullPayload });
    if (this.history.length > this.MAX_HISTORY) {
      this.history.shift();
    }

    // Emit the event
    this.emit(name, fullPayload);

    return correlation_id;
  }

  /**
   * Typed subscription helper
   */
  public subscribe<T extends EventName>(name: T, handler: (payload: PipelineEvents[T]) => void): void {
    this.on(name, handler);
  }

  /**
   * Get event history for debugging
   */
  public getHistory() {
    return [...this.history];
  }
}

export const eventBus = new EventBus();
