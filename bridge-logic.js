// bridge-logic.js
// This file is injected into OpenClaw's getReplyFromConfig function.
// It can be used to intercept messages or add custom logic.

try {
    const text = ctx.message?.text || "";
    
    // Example: Forward !commands to the MooPredict webhook
    if (text.startsWith("!")) {
        const webhook_url = process.env.OPENCLAW_WEBHOOK_URL || "http://host.docker.internal:3001/webhook";
        
        // Use fetch (available in modern Node.js/OpenClaw environment)
        fetch(webhook_url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                chat_id: ctx.chat?.id,
                user_id: ctx.from?.id,
                username: ctx.from?.username
            })
        }).catch(err => console.error("Failed to forward command to MooPredict:", err));
        
        // Optionally return early if we don't want OpenClaw to process this further
        // return { text: "Command received, processing..." };
    }
} catch (e) {
    console.error("Error in bridge-logic.js:", e);
}
