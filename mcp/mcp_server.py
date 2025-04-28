import os
import sys
import json
import time
import logging
import subprocess
import threading
import queue
import shutil
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class MCPServer:
    """Handles the MCP server process and communication"""
    def __init__(self, allowed_dirs: list[str]):
        self.allowed_dirs = allowed_dirs
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self._response_queue = queue.Queue()
        self._lock = threading.Lock()
        self._server_ready = threading.Event()
        
    def _find_executable(self, name: str) -> Optional[str]:
        """Find an executable in PATH or common locations"""
        if os.name == 'nt':
            # Add .cmd extension for Windows npm executables
            names = [f"{name}.exe", f"{name}.cmd", name]
            
            # Get npm prefix
            try:
                npm_prefix = subprocess.check_output(['npm', 'config', 'get', 'prefix'], text=True).strip()
                logger.info(f"NPM prefix: {npm_prefix}")
            except Exception as e:
                logger.warning(f"Could not get npm prefix: {e}")
                npm_prefix = None
            
            # Common locations
            locations = [
                os.path.expanduser('~\\scoop\\shims'),
                os.path.expanduser('~\\scoop\\apps\\nodejs\\current'),
                os.path.expanduser('~\\scoop\\persist\\nodejs\\bin'),
                os.path.expanduser('~\\AppData\\Roaming\\npm'),
                'C:\\Program Files\\nodejs',
                os.path.expanduser('~\\AppData\\Local\\npm'),
                os.path.expanduser('~\\AppData\\Local\\npm-cache'),
            ]
            
            # Add npm prefix locations if available
            if npm_prefix:
                locations.extend([
                    npm_prefix,
                    os.path.join(npm_prefix, 'node_modules', '.bin'),
                    os.path.join(npm_prefix, 'node_modules', 'npm', 'bin')
                ])
            
            # Check PATH first
            for exe_name in names:
                exe_path = shutil.which(exe_name)
                if exe_path:
                    logger.info(f"Found {exe_name} in PATH: {exe_path}")
                    return exe_path
            
            # Check all possible locations
            for location in locations:
                for exe_name in names:
                    path = os.path.join(location, exe_name)
                    logger.debug(f"Checking for {exe_name} in {location}")
                    if os.path.isfile(path):
                        logger.info(f"Found {exe_name} at: {path}")
                        return path
            
            logger.error(f"Could not find {name} in any location. Searched in: {', '.join(locations)}")
            return None
        else:
            return shutil.which(name)
            
    def start(self) -> bool:
        """Start the MCP server process"""
        if self.process and self.process.poll() is None:
            return True
            
        # Set up environment with proper paths and encoding
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['NODE_NO_WARNINGS'] = '1'
        
        if os.name == 'nt':
            env['PYTHONLEGACYWINDOWSSTDIO'] = '0'
            
            # Find node executable
            node_exe = self._find_executable('node')
            if not node_exe:
                logger.error("Could not find node executable")
                return False
            
            # Create a simple Node.js script for MCP server
            script_content = """
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
"""
            
            # Write the script to a temporary file
            script_path = os.path.join(os.getcwd(), 'mcp_server.js')
            with open(script_path, 'w') as f:
                f.write(script_content)
                
            # Run the server directly with node
            cmd = [
                node_exe,
                script_path
            ]
        else:
            cmd = [
                'node',
                script_path
            ]
        
        # Add allowed directories
        cmd.extend([os.path.normpath(dir_path) for dir_path in self.allowed_dirs])
            
        logger.info(f"Starting server with command: {' '.join(cmd)}")
        logger.info(f"Environment PATH: {env.get('PATH')}")
        logger.info(f"Working directory: {os.getcwd()}")
        
        try:
            # Create data directories if they don't exist
            for dir_path in self.allowed_dirs:
                try:
                    os.makedirs(dir_path, exist_ok=True)
                    logger.info(f"Ensured directory exists: {dir_path}")
                except PermissionError as e:
                    logger.error(f"Permission error creating directory {dir_path}: {e}")
                    return False
                except Exception as e:
                    logger.error(f"Error creating directory {dir_path}: {e}")
                    return False
            
            # Start server process with proper buffering
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,  # Use text mode
                    bufsize=1,  # Line buffered
                    env=env,
                    cwd=os.getcwd()
                )
            except PermissionError as e:
                logger.error(f"Permission error starting server: {e}")
                return False
            except Exception as e:
                logger.error(f"Error starting server: {e}")
                return False
                
            logger.info(f"Server process started with PID: {self.process.pid}")
            
            # Start output monitoring threads
            def monitor_output(pipe, is_stderr=False):
                try:
                    for line in pipe:
                        line = line.strip()
                        if not line:
                            continue
                            
                        if is_stderr:
                            logger.error(f"Server stderr: {line}")
                        else:
                            logger.info(f"Server stdout: {line}")
                            
                        # Consider the server ready when it outputs the "running on stdio" message
                        if "running on stdio" in line.lower():
                            logger.info("Server indicated ready state")
                            self._server_ready.set()
                            
                        try:
                            # Try to parse as JSON
                            data = json.loads(line)
                            if isinstance(data, dict):
                                if data.get('id') is not None:
                                    self._response_queue.put(data)
                        except json.JSONDecodeError:
                            # Not JSON, treat as regular output
                            if "Error:" in line or "error:" in line.lower():
                                logger.error(f"Server error: {line}")
                            elif "Warning:" in line or "warning:" in line.lower():
                                logger.warning(f"Server warning: {line}")
                            else:
                                logger.debug(f"Server output: {line}")
                except Exception as e:
                    logger.error(f"Error monitoring {'stderr' if is_stderr else 'stdout'}: {e}")
                    
            threading.Thread(target=monitor_output, args=(self.process.stdout,), daemon=True).start()
            threading.Thread(target=monitor_output, args=(self.process.stderr, True), daemon=True).start()
            
            # Wait for server to be ready
            try:
                ready = self._server_ready.wait(timeout=30)  # 30 second timeout
                if not ready:
                    logger.error("Server failed to indicate ready state within timeout")
                    self.stop()
                    return False
            except Exception as e:
                logger.error(f"Error waiting for server ready state: {e}")
                self.stop()
                return False
                
            # Test connection
            if not self.test_connection():
                logger.error("Server connection test failed")
                self.stop()
                return False
                
            logger.info("Server started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error during server startup: {e}")
            if self.process:
                self.stop()
            return False
    
    def stop(self) -> None:
        """Stop the MCP server process"""
        if self.process:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                logger.info("Server stopped")
            except Exception as e:
                logger.error(f"Error stopping server: {e}")
            finally:
                self.process = None
    
    def execute_tool(self, tool_name: str, params: Dict[str, Any], timeout: int = 30) -> Optional[Dict[str, Any]]:
        """Execute a tool through the MCP server"""
        if not self.process or self.process.poll() is not None:
            logger.error("Server is not running")
            return None

        # Clear any pending responses
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except queue.Empty:
                break

        # Use timestamp as request ID
        request_id = str(int(time.time() * 1000))
        
        # Prepare request
        request = {
            "type": "request",
            "id": request_id,
            "tool": tool_name
        }
        request.update(params)  # Add all params to request
        
        try:
            # Send request
            request_str = json.dumps(request) + "\n"
            logger.info(f"Sending request: {request}")
            
            with self._lock:
                self.process.stdin.write(request_str)
                self.process.stdin.flush()
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    response = self._response_queue.get(timeout=1.0)
                    logger.info(f"Got response: {response}")
                    
                    # Accept response if it matches our request
                    if isinstance(response, dict):
                        if response.get("type") == "error":
                            logger.error(f"Server error: {response.get('error')}")
                            return None
                            
                        if response.get("type") == "response":
                            return response.get("result")
                            
                except queue.Empty:
                    if self.process.poll() is not None:
                        logger.error(f"Server process terminated. Exit code: {self.process.poll()}")
                        return None
                    continue
            
            logger.error(f"Timeout waiting for response")
            return None
            
        except Exception as e:
            logger.error(f"Error executing {tool_name}: {e}")
            return None
            
    def test_connection(self) -> bool:
        """Test if the server is responsive by sending a ping request."""
        try:
            logger.info("Testing server connection...")
            
            # Check if process is still running
            if not self.process or self.process.poll() is not None:
                logger.error("Server process is not running")
                return False
                
            # Clear any pending responses
            while not self._response_queue.empty():
                self._response_queue.get_nowait()
                
            # Send test request
            test_request = {
                "id": "test-connection",
                "method": "ping",
                "params": {}
            }
            
            logger.debug(f"Sending test request: {test_request}")
            self.process.stdin.write(json.dumps(test_request) + "\n")
            self.process.stdin.flush()
            
            # Wait for response with timeout
            try:
                response = self._response_queue.get(timeout=10)
                logger.debug(f"Received test response: {response}")
                
                if response.get("id") == "test-connection":
                    if "error" in response:
                        logger.error(f"Server returned error: {response['error']}")
                        return False
                    logger.info("Server connection test successful")
                    return True
                else:
                    logger.warning(f"Unexpected response ID: {response.get('id')}")
                    return False
                    
            except queue.Empty:
                logger.error("Timeout waiting for test response")
                return False
                
        except Exception as e:
            logger.error(f"Error testing connection: {e}")
            return False
            
    def __del__(self):
        """Cleanup when object is destroyed"""
        self.stop()

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an incoming request from a client.
        
        Args:
            request: The request dictionary from the client
            
        Returns:
            A response dictionary to send back to the client
        """
        try:
            # Validate request format
            if not isinstance(request, dict):
                raise ValueError("Request must be a dictionary")
            
            if "type" not in request:
                raise ValueError("Request must have a 'type' field")
            
            request_type = request["type"]
            
            # Handle different request types
            if request_type == "ping":
                return {"type": "pong"}
            else:
                logger.warning(f"Unknown request type: {request_type}")
                return {
                    "type": "error",
                    "error": f"Unknown request type: {request_type}"
                }
                
        except Exception as e:
            logger.error(f"Error handling request: {e}")
            return {
                "type": "error",
                "error": str(e)
            }

    def process_request(self):
        """Process a single request from stdin and write response to stdout."""
        try:
            # Read request line
            request_line = sys.stdin.readline()
            if not request_line:
                logger.error("Received empty request line")
                return
                
            # Parse request JSON
            try:
                request = json.loads(request_line)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse request JSON: {e}")
                response = {
                    "type": "error",
                    "error": f"Invalid JSON: {str(e)}"
                }
            else:
                # Handle the request
                response = self.handle_request(request)
            
            # Write response
            response_json = json.dumps(response)
            sys.stdout.write(response_json + "\n")
            sys.stdout.flush()
            
        except Exception as e:
            logger.error(f"Error processing request: {e}")
            try:
                error_response = {
                    "type": "error",
                    "error": f"Internal error: {str(e)}"
                }
                sys.stdout.write(json.dumps(error_response) + "\n")
                sys.stdout.flush()
            except:
                logger.error("Failed to write error response")

    def run(self):
        """Run the server main loop, processing requests until stopped."""
        logger.info("Starting MCP server main loop")
        try:
            while True:
                self.process_request()
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, stopping server")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Perform cleanup tasks before stopping the server"""
        self.stop()
        logger.info("Server cleanup completed")

def main():
    """Main entry point for the MCP server."""
    import argparse
    parser = argparse.ArgumentParser(description='Model Context Protocol Server')
    parser.add_argument('--allowed-dirs', nargs='+', help='List of allowed directories')
    parser.add_argument('--log-level', default='INFO', help='Logging level')
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Start server
    server = MCPServer(allowed_dirs=args.allowed_dirs or [])
    server.start()
    server.run()

if __name__ == '__main__':
    main() 