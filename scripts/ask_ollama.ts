import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import * as readline from 'readline';

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://localhost:11434';
const DEFAULT_MODEL = 'qwen2.5-coder:7b';

// Chat history for context memory
const messages: { role: string, content: string }[] = [];

async function chatWithOllama(prompt: string, streamToStdout = true) {
    messages.push({ role: 'user', content: prompt });

    try {
        const response = await fetch(`${OLLAMA_URL}/api/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: DEFAULT_MODEL,
                messages: messages,
                stream: true,
            }),
        });

        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        if (!response.body) throw new Error('Response body is null');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullResponse = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n').filter(line => line.trim() !== '');
            for (const line of lines) {
                try {
                    const parsed = JSON.parse(line);
                    if (parsed.message?.content) {
                        if (streamToStdout) process.stdout.write(parsed.message.content);
                        fullResponse += parsed.message.content;
                    }
                } catch (e) {
                    // Ignore incomplete JSON lines
                }
            }
        }
        
        if (streamToStdout) console.log();
        messages.push({ role: 'assistant', content: fullResponse });
    } catch (error: any) {
        console.error('\n[Error] Failed to communicate with Ollama:', error.message);
        messages.pop(); // Remove the user message that failed to keep history clean
    }
}

async function startInteractive() {
    console.log(`\x1b[36m🤖 Interactive Ollama CLI mode (${DEFAULT_MODEL})\x1b[0m`);
    console.log(`Commands available:`);
    console.log(`  \x1b[33m/add <path>\x1b[0m   - Read a file and add its contents into memory (e.g., /add src/app.ts)`);
    console.log(`  \x1b[33m/clear     \x1b[0m   - Wipe conversation memory to start fresh`);
    console.log(`  \x1b[33m/exit      \x1b[0m   - Close the CLI`);
    console.log(`--------------------------------------------------\n`);

    const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
        prompt: `\x1b[32mYou>\x1b[0m `
    });

    rl.prompt();

    rl.on('line', async (line) => {
        const input = line.trim();
        
        if (!input) {
            rl.prompt();
            return;
        }

        // Handle commands
        if (input === '/exit' || input === '/quit') {
            process.exit(0);
        } else if (input === '/clear') {
            messages.length = 0;
            console.log(`\x1b[36m[System] Memory wiped.\x1b[0m\n`);
            rl.prompt();
        } else if (input.startsWith('/add ')) {
            const filePath = input.slice(5).trim();
            try {
                const absolutePath = resolve(process.cwd(), filePath);
                if (existsSync(absolutePath)) {
                    const content = readFileSync(absolutePath, 'utf-8');
                    messages.push({
                        role: 'user', 
                        content: `Please read and remember the following file '${filePath}':\n\n${content}`
                    });
                    messages.push({
                        role: 'assistant',
                        content: `I've read ${filePath} and I am holding it in my memory. I'm ready for your questions about it.`
                    });
                    console.log(`\x1b[36m[System] Successfully ingested ${filePath} into memory.\x1b[0m\n`);
                } else {
                    console.log(`\x1b[31m[Error] File not found: ${filePath}\x1b[0m\n`);
                }
            } catch (err: any) {
                console.log(`\x1b[31m[Error] Failed to read ${filePath}: ${err.message}\x1b[0m\n`);
            }
            rl.prompt();
        } else {
            // Act as normal chat message
            process.stdout.write(`\x1b[35mOllama>\x1b[0m `);
            await chatWithOllama(input);
            console.log(); // Add a blank line for readability
            rl.prompt();
        }
    }).on('close', () => {
        process.exit(0);
    });
}

async function main() {
    const args = process.argv.slice(2);
    
    // If running without arguments, drop into interactive REPL interface
    if (args.length === 0) {
        await startInteractive();
        return;
    }

    // Direct, single-pass instruction mode (same as before)
    const instruction = args[0];
    const filePaths = args.slice(1);
    
    let combinedPrompt = `${instruction}\n\n`;

    for (const filePath of filePaths) {
        try {
            const absolutePath = resolve(process.cwd(), filePath);
            if (existsSync(absolutePath)) {
                const content = readFileSync(absolutePath, 'utf-8');
                combinedPrompt += `=== FILE: ${filePath} ===\n${content}\n\n`;
            } else {
                console.warn(`[Warning] File not found: ${filePath}`);
            }
        } catch (error) {
            console.error(`[Error] Failed to read ${filePath}:`, error);
        }
    }

    console.log(`🤖 Asking Ollama (${DEFAULT_MODEL})...\n`);
    await chatWithOllama(combinedPrompt);
}

main().catch((error) => {
    console.error("Unhandled error:", error);
    process.exit(1);
});
