
const fs = require('fs').promises;
const path = require('path');
const readline = require('readline');

// Get allowed directories from command line arguments (skip node and script path)
const allowedDirs = process.argv.slice(2);
const dataDir = allowedDirs[0];

// Create readline interface for stdio communication
const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false
});

console.log("Secure MCP Filesystem Server running on stdio");
console.log("Data directory:", JSON.stringify(dataDir));

// Helper function to safely write files
async function writeFile(filePath, content) {
    try {
        const fullPath = path.join(dataDir, filePath);
        console.error(`Writing file: ${fullPath}`);
        await fs.writeFile(fullPath, content, 'utf8');
        console.error(`File written successfully: ${fullPath}`);
        return true;
    } catch (error) {
        console.error(`Error writing file: ${error.message}`);
        throw error;
    }
}

// Handle incoming requests
rl.on('line', async (line) => {
    console.error(`Received request: ${line}`);
    try {
        const request = JSON.parse(line);
        console.error(`Parsed request:`, request);
        
        // Handle ping request
        if (request.method === 'ping') {
            console.log(JSON.stringify({ id: request.id, type: 'response', result: { success: true } }));
            return;
        }
        
        // Handle filesystem requests
        if (request.type === 'request' && request.tool === 'write_file') {
            try {
                await writeFile(request.path, request.content);
                console.log(JSON.stringify({ id: request.id, type: 'response', result: { success: true } }));
            } catch (error) {
                console.error(`Operation failed: ${error.message}`);
                console.log(JSON.stringify({ id: request.id, type: 'error', error: error.message }));
            }
        } else if (request.type === 'request' && request.tool === 'read_file') {
            try {
                const content = await fs.readFile(path.join(dataDir, request.path), 'utf8');
                console.log(JSON.stringify({ id: request.id, type: 'response', result: { content } }));
            } catch (error) {
                console.error(`Operation failed: ${error.message}`);
                console.log(JSON.stringify({ id: request.id, type: 'error', error: error.message }));
            }
        } else if (request.type === 'request' && request.tool === 'list_directory') {
            try {
                const files = await fs.readdir(path.join(dataDir, request.path || '.'));
                console.log(JSON.stringify({ id: request.id, type: 'response', result: { files } }));
            } catch (error) {
                console.error(`Operation failed: ${error.message}`);
                console.log(JSON.stringify({ id: request.id, type: 'error', error: error.message }));
            }
        }
    } catch (error) {
        console.error(`Request processing failed: ${error.message}`);
        console.log(JSON.stringify({ type: 'error', error: error.message }));
    }
});

// Handle process termination
process.on('SIGINT', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
