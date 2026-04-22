import winston from 'winston';
import path from 'path';
import fs from 'fs';

const LOGS_DIR = path.join(process.cwd(), 'logs');

// Ensure logs directory exists
if (!fs.existsSync(LOGS_DIR)) {
  fs.mkdirSync(LOGS_DIR, { recursive: true });
}

// Custom format for structured JSON
const jsonFormat = winston.format.combine(
  winston.format.timestamp(),
  winston.format.json()
);

// Custom format for console output (pretty-print)
const consoleFormat = winston.format.combine(
  winston.format.colorize(),
  winston.format.timestamp({ format: 'HH:mm:ss' }),
  winston.format.printf(({ timestamp, level, message, agent, correlation_id, ...meta }) => {
    const agentTag = agent ? `[${agent}] ` : '';
    const correlationTag = correlation_id ? `(${correlation_id}) ` : '';
    const metaStr = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : '';
    return `${timestamp} ${level}: ${agentTag}${correlationTag}${message}${metaStr}`;
  })
);

export const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: jsonFormat,
  transports: [
    // Standard error file
    new winston.transports.File({ 
      filename: path.join(LOGS_DIR, 'error.log'), 
      level: 'error' 
    }),
    // Combined log file
    new winston.transports.File({ 
      filename: path.join(LOGS_DIR, 'combined.log') 
    }),
    // Pretty-print console
    new winston.transports.Console({
      format: consoleFormat
    })
  ]
});

/**
 * Creates a child logger for a specific agent or service.
 */
export const createChildLogger = (agentName: string) => {
  return logger.child({ agent: agentName });
};
