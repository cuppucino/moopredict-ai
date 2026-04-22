import { Request, Response, NextFunction } from 'express';
import dotenv from 'dotenv';
dotenv.config();

const API_KEY = process.env.INTERNAL_API_KEY || "REMOVED_PRIVATE_VALUE";

/**
 * Enterprise Auth Middleware
 * Validates the X-API-Key header to prevent unauthorized access.
 */
export const validate_api_key = (req: Request, res: Response, next: NextFunction) => {
  const user_key = req.headers['x-api-key'];

  // Skip auth for internal / loopback or health checks if needed
  // For now, all mutation/prediction routes will require it
  if (!user_key || user_key !== API_KEY) {
    console.warn(`[SECURITY] Unauthorized access attempt from ${req.ip} to ${req.originalUrl}`);
    return res.status(401).json({ 
      error: "Unauthorized", 
      message: "A valid X-API-Key header is required for this operation." 
    });
  }

  next();
};
