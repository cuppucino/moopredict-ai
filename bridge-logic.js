  // MooPredict-AI Bridge Logic
  try {
    const isAntigravity = process.env.ANTiGRAVITY_MODE === 'true';
    if (isAntigravity) {
      const prompt = ctx.message.text || '';
      console.log(`[Bridge] Intercepting prompt for MooPredict: ${prompt.substring(0, 50)}...`);
      
      // Standard local bridge to Ollama or MooPredict Webhook
      const response = await fetch(process.env.OPENCLAW_WEBHOOK_URL || 'http://host.docker.internal:3001/webhook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'ai_request',
          text: prompt,
          user: ctx.from?.username || 'unknown',
          token: process.env.OPENCLAW_GATEWAY_TOKEN
        })
      });

      if (response.ok) {
        const data = await response.json();
        if (data.reply) {
          return { content: data.reply };
        }
      }
    }
  } catch (bridgeError) {
    console.error('[Bridge] Failed to route to MooPredict:', bridgeError.message);
  }
  // Fallback to original logic if bridge fails or is disabled
