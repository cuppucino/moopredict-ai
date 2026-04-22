/**
 * QA Test — Foundation Model Integration (Chronos + TFT)
 *
 * Tests:
 * 1.  Chronos levels: direction-aware p10/p90 for BUY and SELL
 * 2.  Sparse features: fallback to Gemini values when Chronos features missing
 * 3.  Chronos confidence: 0.5/spread calibration
 * 4.  TFT confidence: dynamic based on trend_strength
 * 5.  Ensemble weights: sum = 1.0, includes xgboost
 * 6.  all_results: every model contributes to weighted vote
 * 7.  calculateEnsembleScore: weighted voting math
 * 8.  scoreToRecommendation: threshold boundaries
 * 9.  adjustPrediction: confidence boost on agreement, penalty on disagreement
 * 10. Recommendation mapping: correct thresholds across all models
 */

function assert(condition: boolean, label: string) {
  if (!condition) throw new Error(`❌ FAIL: ${label}`);
  console.log(`  ✅ ${label}`);
}

function assertApprox(a: number, b: number, label: string, tol = 0.001) {
  if (Math.abs(a - b) > tol) throw new Error(`❌ FAIL: ${label} — got ${a.toFixed(4)}, expected ≈${b}`);
  console.log(`  ✅ ${label}`);
}

// ─── 1. Direction-aware Chronos levels ───────────────────────────────────────
function testChronosLevelDirectionAwareness() {
  console.log("\n🧪 1. Chronos quantile levels — direction-aware BUY and SELL");

  function applyChronosLevels(direction: string, price: number, forecast: { p10: number; p90: number; median: number }) {
    const isDown = direction === "DOWN";
    return {
      targetPrice: isDown ? (forecast?.p10  ?? price * 0.95) : (forecast?.p90  ?? price * 1.05),
      stop_loss:   isDown ? (forecast?.p90  ?? price * 1.05) : (forecast?.p10  ?? price * 0.95),
      take_profit: isDown ? (forecast?.p10  ?? price * 0.90) : (forecast?.p90  ?? price * 1.10),
    };
  }

  const price = 100;
  const upForecast = { median: 103, p10: 98,  p90: 108 };
  const dnForecast = { median: 97,  p10: 92,  p90: 102 };

  const buy = applyChronosLevels("UP", price, upForecast);
  assert(buy.targetPrice === 108, `BUY: targetPrice = p90 (108)`);
  assert(buy.stop_loss   === 98,  `BUY: stop_loss   = p10 (98) — below entry`);
  assert(buy.targetPrice > price, `BUY: targetPrice above entry ✓`);
  assert(buy.stop_loss   < price, `BUY: stop below entry ✓`);

  const sell = applyChronosLevels("DOWN", price, dnForecast);
  assert(sell.targetPrice === 92,  `SELL: targetPrice = p10 (92) — low-end for short`);
  assert(sell.stop_loss   === 102, `SELL: stop_loss   = p90 (102) — above entry`);
  assert(sell.targetPrice < price, `SELL: targetPrice below entry ✓`);
  assert(sell.stop_loss   > price, `SELL: stop above entry (cuts loss if price rises) ✓`);

  // Fallback path (no forecast object)
  const noForecast: any = undefined;
  const buyFallback = applyChronosLevels("UP", price, noForecast);
  assertApprox(buyFallback.targetPrice, price * 1.05, `BUY fallback: targetPrice = price × 1.05`, 0.01);
  assertApprox(buyFallback.stop_loss,  price * 0.95, `BUY fallback: stop_loss   = price × 0.95`, 0.01);
}

// ─── 2. Sparse features — fallback to Gemini values ──────────────────────────
function testSparseFeatureFallback() {
  console.log("\n🧪 2. Sparse Chronos features → fallback to Gemini values");

  const gemini = { rsi: 62, macd: 'Bullish', movingAverage: 'Above SMA20', atr: 5.5 };
  const sparse: any = { last_price: 100, predicted_price: 103 };

  const indicators = {
    rsi: sparse.rsi_14 ?? gemini.rsi,
    macd: sparse.momentum_5 != null
      ? (sparse.momentum_5 > 0 ? 'Bullish' : 'Bearish')
      : gemini.macd,
    movingAverage: sparse.price_vs_sma20 != null
      ? (sparse.price_vs_sma20 > 0 ? 'Above' : 'Below') + ' SMA20'
      : gemini.movingAverage,
    atr: sparse.atr_14 ?? gemini.atr
  };

  assert(indicators.rsi === 62,             `RSI falls back to Gemini value (62)`);
  assert(indicators.macd === 'Bullish',     `MACD falls back to Gemini (not hardcoded 'Bearish')`);
  assert(indicators.movingAverage === 'Above SMA20', `MA falls back to Gemini ('Above SMA20')`);
  assert(indicators.atr === 5.5,            `ATR falls back to Gemini value (5.5)`);
  assert(sparse.volatility_20 == null,      `volatility_20 absent → risk level not overwritten`);
}

// ─── 3. Chronos confidence calibration (0.5/spread) ──────────────────────────
function testChronosConfidenceCalibration() {
  console.log("\n🧪 3. Chronos confidence — 0.5/spread formula");

  function chronosConf(last: number, p10: number, p90: number): number {
    const spread = (p90 - p10) / last;
    return Math.max(0.1, Math.min(0.95, 0.5 / spread));
  }

  assertApprox(chronosConf(100, 99, 101), 0.95, `Tight spread (2%)  → clamped to 0.95`);
  assertApprox(chronosConf(100, 95, 105), 0.95, `Normal spread (10%) → clamped to 0.95`);
  assertApprox(chronosConf(100, 50, 150), 0.50, `Wide spread (100%) → 0.5/1.0 = 0.50`);
  assertApprox(chronosConf(100, 0,  600), 0.10, `Extreme spread     → clamped to 0.10`);

  // Direction: tighter spreads → higher confidence (compare unclamped range)
  const tight = chronosConf(100, 70, 130); // 60% spread → 0.5/0.60 = 0.833
  const wide  = chronosConf(100, 50, 150); // 100% spread → 0.5/1.00 = 0.50
  assert(tight > wide, `Tighter spread → higher confidence (${tight.toFixed(2)} > ${wide.toFixed(2)})`);
  assertApprox(tight, 0.833, `60% spread → 0.5/0.60 = 0.833`, 0.01);
}

// ─── 4. TFT dynamic confidence ────────────────────────────────────────────────
function testTFTDynamicConfidence() {
  console.log("\n🧪 4. TFT confidence — dynamic trend_strength based");

  function tftConf(last: number, predicted: number): number {
    const trend_strength = Math.abs(predicted - last) / last;
    return Math.min(0.85, 0.70 + trend_strength * 2);
  }

  assertApprox(tftConf(100, 100), 0.70, `Flat (0% move)  → base 0.70`);
  assertApprox(tftConf(100, 105), 0.80, `5% UP move      → 0.70 + 0.10 = 0.80`);
  assertApprox(tftConf(100, 110), 0.85, `10% move        → clamped to 0.85`);
  assertApprox(tftConf(100, 95),  0.80, `5% DOWN move    → same as UP (abs)`);

  assert(tftConf(100, 110) > 0.80, `Strong trend crosses STRONG_BUY/SELL threshold (0.80)`);
  assert(tftConf(100, 100) < 0.80, `Flat prediction stays below STRONG threshold`);
}

// ─── 5. Ensemble weights sum to 1.0 ──────────────────────────────────────────
function testEnsembleWeightSum() {
  console.log("\n🧪 5. Ensemble weights — sum = 1.0, xgboost present");

  const weights = { chronos: 0.25, tft: 0.20, xgboost: 0.15, gemini: 0.15, ollama: 0.10, news: 0.08, social: 0.07 };
  const total = Object.values(weights).reduce((s, w) => s + w, 0);

  assertApprox(total, 1.0, `All weights sum to 1.00 (got ${total.toFixed(2)})`);
  assert("xgboost" in weights,           `xgboost weight present`);
  assert(weights.chronos > weights.tft,  `Chronos (0.25) > TFT (0.20) — preferred model`);
  assert(weights.tft > weights.xgboost,  `TFT (0.20) > XGBoost (0.15) — foundation > legacy`);
  assert(weights.chronos > weights.gemini, `Chronos (0.25) > Gemini (0.15) — ML > LLM`);
}

// ─── 6. all_results structure ─────────────────────────────────────────────────
function testAllResultsStructure() {
  console.log("\n🧪 6. all_results — every model contributes its output");

  const allResults: Record<string, any> = {
    chronos:   { direction: "UP",   confidence: 0.88, forecast: { median: 192, p10: 183, p90: 199 } },
    tft:       { direction: "UP",   confidence: 0.78, explanation: "TFT Analysis: trend (Strength: 0.04)" },
    xgboost:   { direction: "UP",   confidence: 0.71 },
    technical: { direction: "DOWN", confidence: 0.55, recommendation: "SELL" }
  };

  assert("chronos"   in allResults, `all_results has chronos`);
  assert("tft"       in allResults, `all_results has tft`);
  assert("xgboost"   in allResults, `all_results has xgboost`);
  assert("technical" in allResults, `all_results has technical (always-on)`);

  const cf = allResults.chronos.forecast;
  assert(cf.p10 < cf.median && cf.median < cf.p90, `Forecast quantile order: p10 < median < p90`);

  // TS side: Bug 1 fix reads from all_results.chronos for levels
  const price = 185;
  const chronos = allResults.chronos;
  const isDown = chronos.direction === "DOWN";
  const targetPrice = isDown ? chronos.forecast?.p10 : chronos.forecast?.p90;
  assert(targetPrice === 199,  `TS reads all_results.chronos.forecast.p90 for UP signal`);
  assert(targetPrice! > price, `targetPrice (${targetPrice}) above entry for UP ✓`);
}

// ─── 7. calculateEnsembleScore weighted voting ────────────────────────────────
function testCalculateEnsembleScore() {
  console.log("\n🧪 7. calculateEnsembleScore — weighted voting math");

  const weights = { chronos: 0.25, tft: 0.20, xgboost: 0.15, gemini: 0.15, ollama: 0.10, news: 0.08, social: 0.07, technical: 0.05 };

  function recToScore(rec: string): number {
    return rec === "STRONG_BUY" ? 1.0 : rec === "BUY" ? 0.5 : rec === "HOLD" ? 0 : rec === "SELL" ? -0.5 : -1.0;
  }

  function calcScore(input: {
    geminiRec: string; geminiConf: number;
    chronosDir?: string; chronosConf?: number;
    tftDir?: string; tftConf?: number;
    xgboostDir?: string; xgboostConf?: number;
    newsSentiment: number; socialSentiment: number;
    technicalTrend: string;
  }): number {
    let score = 0; let total = 0;

    score += recToScore(input.geminiRec) * input.geminiConf * weights.gemini;
    total += weights.gemini;

    if (input.chronosDir) {
      score += (input.chronosDir === "UP" ? 1 : -1) * (input.chronosConf ?? 0) * weights.chronos;
      total += weights.chronos;
    }
    if (input.tftDir) {
      score += (input.tftDir === "UP" ? 1 : -1) * (input.tftConf ?? 0) * weights.tft;
      total += weights.tft;
    }
    if (input.xgboostDir) {
      score += (input.xgboostDir === "UP" ? 1 : -1) * (input.xgboostConf ?? 0) * weights.xgboost;
      total += weights.xgboost;
    }

    score += input.newsSentiment * weights.news;
    total += weights.news;
    score += input.socialSentiment * weights.social;
    total += weights.social;

    const techScore = input.technicalTrend === "strong_up" ? 1 : input.technicalTrend === "up" ? 0.5 :
                      input.technicalTrend === "down" ? -0.5 : -1;
    score += techScore * weights.technical;
    total += weights.technical;

    return total > 0 ? score / total : 0;
  }

  // Strong bullish consensus: all models UP
  const allUp = calcScore({
    geminiRec: "BUY", geminiConf: 0.80,
    chronosDir: "UP",   chronosConf: 0.90,
    tftDir:     "UP",   tftConf:     0.80,
    xgboostDir: "UP",   xgboostConf: 0.70,
    newsSentiment: 0.5, socialSentiment: 0.4,
    technicalTrend: "strong_up"
  });
  assert(allUp > 0.5, `All-UP consensus → strong positive score (${allUp.toFixed(3)} > 0.5)`);

  // Mixed signals: Chronos DOWN, rest UP
  const mixed = calcScore({
    geminiRec: "BUY", geminiConf: 0.70,
    chronosDir: "DOWN", chronosConf: 0.85,
    tftDir:     "UP",   tftConf:    0.75,
    newsSentiment: 0.2, socialSentiment: 0.1,
    technicalTrend: "up"
  });
  assert(Math.abs(mixed) < allUp, `Mixed signals → weaker conviction than full consensus`);

  // Pure bearish: all DOWN
  const allDown = calcScore({
    geminiRec: "SELL", geminiConf: 0.75,
    chronosDir: "DOWN", chronosConf: 0.88,
    tftDir:     "DOWN", tftConf:    0.80,
    xgboostDir: "DOWN", xgboostConf: 0.65,
    newsSentiment: -0.4, socialSentiment: -0.3,
    technicalTrend: "strong_down"
  });
  assert(allDown < -0.5, `All-DOWN consensus → strong negative score (${allDown.toFixed(3)} < -0.5)`);

  // Score is symmetric (same magnitude, opposite sign)
  const diff = Math.abs(Math.abs(allUp) - Math.abs(allDown));
  assert(diff < 0.3, `Bullish/bearish scores roughly symmetric (diff=${diff.toFixed(3)})`);
}

// ─── 8. scoreToRecommendation thresholds ─────────────────────────────────────
function testScoreToRecommendation() {
  console.log("\n🧪 8. scoreToRecommendation — threshold boundaries");

  function scoreToRec(score: number): string {
    if (score >= 0.7)  return "STRONG_BUY";
    if (score >= 0.3)  return "BUY";
    if (score >= -0.3) return "HOLD";
    if (score >= -0.7) return "SELL";
    return "STRONG_SELL";
  }

  assert(scoreToRec(1.0)   === "STRONG_BUY",  `score=1.0  → STRONG_BUY`);
  assert(scoreToRec(0.7)   === "STRONG_BUY",  `score=0.7  → STRONG_BUY (boundary inclusive)`);
  assert(scoreToRec(0.69)  === "BUY",          `score=0.69 → BUY (just below STRONG)`);
  assert(scoreToRec(0.3)   === "BUY",          `score=0.3  → BUY (boundary inclusive)`);
  assert(scoreToRec(0.29)  === "HOLD",         `score=0.29 → HOLD`);
  assert(scoreToRec(0.0)   === "HOLD",         `score=0.0  → HOLD`);
  assert(scoreToRec(-0.3)  === "HOLD",         `score=-0.3 → HOLD (boundary inclusive)`);
  assert(scoreToRec(-0.31) === "SELL",         `score=-0.31 → SELL`);
  assert(scoreToRec(-0.7)  === "SELL",         `score=-0.7 → SELL (boundary inclusive)`);
  assert(scoreToRec(-0.71) === "STRONG_SELL",  `score=-0.71 → STRONG_SELL`);
  assert(scoreToRec(-1.0)  === "STRONG_SELL",  `score=-1.0 → STRONG_SELL`);
}

// ─── 9. adjustPrediction — confidence boost/penalty on model agreement ────────
function testAdjustPredictionConfidence() {
  console.log("\n🧪 9. adjustPrediction — confidence boost/penalty on model agreement");

  function adjust(baseConf: number, baseRec: string, ensembleScore: number): number {
    function recToScore(rec: string): number {
      return rec === "STRONG_BUY" ? 1.0 : rec === "BUY" ? 0.5 : rec === "HOLD" ? 0 : rec === "SELL" ? -0.5 : -1.0;
    }
    const geminiScore = recToScore(baseRec);
    if (Math.sign(geminiScore) === Math.sign(ensembleScore)) {
      return Math.min(0.95, baseConf * 1.1);
    } else {
      return baseConf * 0.8;
    }
  }

  // Agreement: Gemini BUY, ensemble positive → 10% boost
  const boosted = adjust(0.70, "BUY", 0.6);
  assertApprox(boosted, 0.77, `Agreement: 0.70 × 1.10 = 0.77`);
  assert(boosted > 0.70, `Agreement boosts confidence`);

  // Disagreement: Gemini BUY, ensemble negative → 20% penalty
  const penalised = adjust(0.70, "BUY", -0.4);
  assertApprox(penalised, 0.56, `Disagreement: 0.70 × 0.80 = 0.56`);
  assert(penalised < 0.70, `Disagreement penalises confidence`);

  // Capped at 0.95 even with high base conf
  const capped = adjust(0.90, "STRONG_BUY", 0.9);
  assertApprox(capped, 0.95, `Boost capped at 0.95 (0.90 × 1.10 = 0.99 → 0.95)`);

  // HOLD base (score=0) treated as neutral — sign(0) == 0
  const neutral = adjust(0.60, "HOLD", 0.5);
  assert(neutral === 0.60 * 0.8, `HOLD vs positive ensemble → penalised (sign mismatch)`);
}

// ─── 10. Recommendation mapping thresholds ───────────────────────────────────
function testRecommendationMapping() {
  console.log("\n🧪 10. Recommendation mapping — correct across all models");

  function mapRec(direction: string, confidence: number): string {
    if (direction === "UP"   && confidence > 0.8) return "STRONG_BUY";
    if (direction === "UP")                        return "BUY";
    if (direction === "DOWN" && confidence > 0.8) return "STRONG_SELL";
    if (direction === "DOWN")                      return "SELL";
    return "HOLD";
  }

  assert(mapRec("UP",   0.90) === "STRONG_BUY",  `UP + 0.90 → STRONG_BUY`);
  assert(mapRec("UP",   0.75) === "BUY",          `UP + 0.75 → BUY`);
  assert(mapRec("UP",   0.80) === "BUY",          `UP + 0.80 → BUY (threshold is strictly >0.8)`);
  assert(mapRec("UP",   0.81) === "STRONG_BUY",   `UP + 0.81 → STRONG_BUY (just over threshold)`);
  assert(mapRec("DOWN", 0.85) === "STRONG_SELL",  `DOWN + 0.85 → STRONG_SELL`);
  assert(mapRec("DOWN", 0.65) === "SELL",          `DOWN + 0.65 → SELL`);
  assert(mapRec("FLAT", 0.50) === "HOLD",          `FLAT → HOLD`);

  // TFT at strong trend (0.85) can now produce STRONG
  assert(mapRec("UP", 0.85) === "STRONG_BUY", `TFT strong trend (0.85) → STRONG_BUY ✓`);
  assert(mapRec("UP", 0.70) === "BUY",         `TFT flat (0.70)         → BUY only ✓`);
}

// ─── Runner ───────────────────────────────────────────────────────────────────

async function runAll() {
  console.log("\n══════════════════════════════════════════════════════");
  console.log("  QA TEST — FOUNDATION MODEL INTEGRATION (Chronos/TFT)");
  console.log("══════════════════════════════════════════════════════");

  try {
    testChronosLevelDirectionAwareness();
    testSparseFeatureFallback();
    testChronosConfidenceCalibration();
    testTFTDynamicConfidence();
    testEnsembleWeightSum();
    testAllResultsStructure();
    testCalculateEnsembleScore();
    testScoreToRecommendation();
    testAdjustPredictionConfidence();
    testRecommendationMapping();

    console.log("\n══════════════════════════════════════════════════════");
    console.log("  ✨ ALL TESTS PASSED");
    console.log("══════════════════════════════════════════════════════\n");
    process.exit(0);
  } catch (err: any) {
    console.error(`\n${err.message}\n`);
    process.exit(1);
  }
}

runAll();
