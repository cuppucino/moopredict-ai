import rateLimit from 'express-rate-limit';

/**
 * Global Rate Limiter
 * Limits overall abuse to the API.
 */
export const global_limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 5000, // Increased for development (from 200)
  standardHeaders: true,
  legacyHeaders: false,
  message: {
    error: "Too Many Requests",
    message: "Rate limit exceeded. Please try again later."
  }
});

/**
 * Prediction Rate Limiter
 * Stricter limit specifically for Gemini AI predictions to protect billing and tokens.
 */
export const prediction_limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 10, // Limit each IP to 10 predictions per 15 minutes
  standardHeaders: true, // Return rate limit info in the `RateLimit-*` headers
  legacyHeaders: false,
  message: {
    error: "AI Quota Exceeded",
    message: "You have reached the limit for AI deep analysis. Please wait 15 minutes or upgrade your plan."
  }
});
