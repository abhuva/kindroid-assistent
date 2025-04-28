import os
import sys
import json
import time
import logging
import subprocess
import requests
import threading
import yaml
import schedule
from dotenv import load_dotenv
from datetime import datetime
import socket
import atexit
import select
import queue
import errno

# Add external directory to Python path
external_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "external", "servers", "src", "filesystem")
sys.path.append(external_dir)

from mcp_client import MCPClient
from mcp_server import MCPServer

# --- Logging Configuration ---
LOG_FILENAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log.txt")

# Configure logging to write to both console and file
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILENAME, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Create a logger instance
logger = logging.getLogger('kindroid')

# --- Configuration Loading ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILENAME = os.path.join(SCRIPT_DIR, "kindroid.env")
CONFIG_FILENAME = os.path.join(SCRIPT_DIR, "config", "config.yaml")
PROMPT_FILENAME = os.path.join(SCRIPT_DIR, "prompts", "initial_prompt.md")
GEMINI_SYSTEM_PROMPT = os.path.join(SCRIPT_DIR, "prompts", "gemini_system.md")

# Load API keys from .env file
if not os.path.exists(ENV_FILENAME):
    logger.error(f"Error: Environment file '{ENV_FILENAME}' not found.")
    logger.error("Please create it with KINDROID_API_KEY, KINDROID_AI_ID, and GEMINI_API_KEY.")
    sys.exit(1)

load_dotenv(dotenv_path=ENV_FILENAME)

# Credentials
KINDROID_API_KEY = os.getenv("KINDROID_API_KEY")
KINDROID_AI_ID = os.getenv("KINDROID_AI_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Validate Credentials ---
if not KINDROID_API_KEY:
    logger.error("Error: KINDROID_API_KEY not found in environment variables.")
    sys.exit(1)
if not KINDROID_AI_ID:
    logger.error("Error: KINDROID_AI_ID not found in environment variables.")
    sys.exit(1)
if not GEMINI_API_KEY:
    logger.error("Error: GEMINI_API_KEY not found in environment variables.")
    sys.exit(1)
# --- End Credential Validation ---

# Load configuration from YAML file
if not os.path.exists(CONFIG_FILENAME):
    logger.error(f"Error: Configuration file '{CONFIG_FILENAME}' not found.")
    logger.error("Please create it with your configuration settings.")
    sys.exit(1)

try:
    with open(CONFIG_FILENAME, 'r') as file:
        config = yaml.safe_load(file)
except Exception as e:
    logger.error(f"Error loading configuration file: {e}")
    sys.exit(1)

# --- Get Run Interval (Minutes) ---
DEFAULT_INTERVAL_MINUTES = 180
try:
    RUN_INTERVAL_MINUTES = config.get('scheduling', {}).get('interval_minutes', DEFAULT_INTERVAL_MINUTES)
    if RUN_INTERVAL_MINUTES <= 0:
        logger.warning(f"Warning: interval_minutes must be positive. Using default ({DEFAULT_INTERVAL_MINUTES} minutes).")
        RUN_INTERVAL_MINUTES = DEFAULT_INTERVAL_MINUTES
except (ValueError, TypeError):
    logger.warning(f"Warning: Invalid interval_minutes. Using default ({DEFAULT_INTERVAL_MINUTES} minutes).")
    RUN_INTERVAL_MINUTES = DEFAULT_INTERVAL_MINUTES

# --- Get Response Limits ---
DEFAULT_MAX_TOKENS = 1000
DEFAULT_MAX_CHARS = 4000
try:
    GEMINI_MAX_OUTPUT_TOKENS = config.get('response_limits', {}).get('max_tokens', DEFAULT_MAX_TOKENS)
    if GEMINI_MAX_OUTPUT_TOKENS <= 0:
        logger.warning(f"Warning: max_tokens must be positive. Using default ({DEFAULT_MAX_TOKENS}).")
        GEMINI_MAX_OUTPUT_TOKENS = DEFAULT_MAX_TOKENS
except (ValueError, TypeError):
    logger.warning(f"Warning: Invalid max_tokens. Using default ({DEFAULT_MAX_TOKENS}).")
    GEMINI_MAX_OUTPUT_TOKENS = DEFAULT_MAX_TOKENS

try:
    MAX_RESPONSE_CHARS = config.get('response_limits', {}).get('max_chars', DEFAULT_MAX_CHARS)
    if MAX_RESPONSE_CHARS <= 0:
        logger.warning(f"Warning: max_chars must be positive. Using default ({DEFAULT_MAX_CHARS}).")
        MAX_RESPONSE_CHARS = DEFAULT_MAX_CHARS
except (ValueError, TypeError):
    logger.warning(f"Warning: Invalid max_chars. Using default ({DEFAULT_MAX_CHARS}).")
    MAX_RESPONSE_CHARS = DEFAULT_MAX_CHARS

# --- API Config ---
GEMINI_MODEL = "gemini-1.5-flash"
KINDROID_BASE_URL = "https://api.kindroid.ai/v1"
KINDROID_ENDPOINT = "/send-message"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

def is_server_running(process):
    """Check if the MCP server is running by checking if the process is alive."""
    if process is None:
        logger.warning("Server process is None")
        return False
    
    # Check if the process is still running
    if process.poll() is not None:
        logger.warning(f"Server process has terminated with exit code {process.poll()}")
        return False
    
    logger.info("Server process is running")
    return True

def find_npx():
    """Find the NPX executable on the system"""
    # Common locations for Node.js installation on Windows
    possible_paths = [
        os.path.expanduser("~\\AppData\\Roaming\\npm\\npx.cmd"),
        os.path.expanduser("~\\AppData\\Roaming\\npm\\npx"),
        "C:\\Program Files\\nodejs\\npx.cmd",
        "C:\\Program Files (x86)\\nodejs\\npx.cmd",
        "C:\\Program Files\\nodejs\\npx",
        "C:\\Program Files (x86)\\nodejs\\npx"
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
            
    return None

def start_mcp_server(config):
    """Start the MCP filesystem server if it's not already running."""
    server_config = config.get('mcp_servers', {}).get('filesystem', {})
    
    logger.info("Starting MCP filesystem server...")
    
    try:
        # Find NPX executable
        npx_path = find_npx()
        if not npx_path:
            logger.error("Could not find NPX executable. Please ensure Node.js is installed.")
            return False
            
        logger.info(f"Found NPX at: {npx_path}")
            
        # Get all allowed directories from config
        allowed_dirs = []
        for dir_path in server_config.get('allowed_directories', []):
            # Replace ${workspaceFolder} with actual workspace path
            abs_path = os.path.abspath(dir_path.replace("${workspaceFolder}", os.getcwd()))
            allowed_dirs.append(abs_path)
            # Create directory if it doesn't exist
            os.makedirs(abs_path, exist_ok=True)
        
        # Build NPX command
        cmd = [
            npx_path,
            "-y",
            "@modelcontextprotocol/server-filesystem"
        ]
        
        # Add allowed directories
        cmd.extend(allowed_dirs)
        
        # Log the command for debugging
        logger.info(f"Running command: {' '.join(cmd)}")
        
        # Set up environment with UTF-8 encoding
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        if os.name == 'nt':  # Windows
            env['PYTHONLEGACYWINDOWSSTDIO'] = '0'
        
        # Start the server process with pipe communication
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,  # Use text mode consistently
            bufsize=1,  # Line buffered
            env=env,
            cwd=os.getcwd()  # Explicitly set working directory
        )
        
        # Function to read process output
        def read_output(pipe, prefix):
            while True:
                try:
                    line = pipe.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        if line:
                            logger.info(f"{prefix}: {line}")
                except Exception as e:
                    logger.error(f"Error reading {prefix}: {e}")
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
        
        # Start output reading threads
        stdout_thread = threading.Thread(target=read_output, args=(process.stdout, "NPX"))
        stderr_thread = threading.Thread(target=read_output, args=(process.stderr, "NPX Error"))
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()
        
        # Wait for NPX to start downloading and installing the package
        logger.info("Waiting for NPX to initialize (15 seconds)...")
        time.sleep(15)
        
        # Check if process is still running
        if process.poll() is not None:
            logger.error("NPX process terminated unexpectedly")
            return False
        
        # Wait for the server to start (up to 60 seconds)
        logger.info("Waiting for server to start (up to 60 seconds)...")
        for i in range(60):
            if is_server_running(process):
                logger.info("MCP filesystem server started successfully.")
                return True
            if process.poll() is not None:
                logger.error("NPX process terminated unexpectedly while waiting for server")
                return False
            logger.info(f"Waiting for server... ({i+1}/60)")
            time.sleep(1)
            
        # If we get here, the server didn't start
        logger.error("Failed to start MCP filesystem server: timeout after 60 seconds")
        process.terminate()  # Kill the process
        return False
        
    except Exception as e:
        logger.error(f"Failed to start MCP filesystem server: {e}")
        return False

def stop_mcp_server():
    """Stop the MCP filesystem server."""
    try:
        if mcp_manager and mcp_manager.server_process:
            logger.info("Stopping MCP filesystem server...")
            try:
                mcp_manager.server_process.terminate()
                # Wait for the process to terminate
                mcp_manager.server_process.wait(timeout=5)
                logger.info("MCP filesystem server stopped.")
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                logger.warning("MCP server didn't terminate gracefully, forcing kill...")
                mcp_manager.server_process.kill()
                logger.info("MCP filesystem server killed.")
            except Exception as e:
                logger.error(f"Error stopping MCP server: {e}")
    except Exception as e:
        logger.error(f"Error stopping MCP filesystem server: {e}")

# Register the stop function to run at exit
atexit.register(stop_mcp_server)

# --- MCP Client Initialization ---
class MCPManager:
    def __init__(self, config):
        self.config = config
        
        # Get server configuration
        server_config = config.get('mcp_servers', {}).get('filesystem', {})
        allowed_dirs = server_config.get('allowed_directories', [])
        
        # Process allowed directories
        processed_dirs = []
        for dir_path in allowed_dirs:
            # Replace ${workspaceFolder} with actual workspace path
            abs_path = os.path.abspath(dir_path.replace("${workspaceFolder}", os.getcwd()))
            processed_dirs.append(abs_path)
            # Create directory if it doesn't exist
            os.makedirs(abs_path, exist_ok=True)
            
        # If no directories configured, use current directory
        if not processed_dirs:
            processed_dirs = [os.getcwd()]
            
        logger.info(f"Initializing MCP server with directories: {processed_dirs}")
        
        # Create server instance with processed directories
        self.server = MCPServer(processed_dirs)
        
        # Try to start the server with retries
        max_retries = 3
        for i in range(max_retries):
            try:
                if self.server.start():
                    logger.info("MCP server started successfully")
                    return
                logger.warning(f"Failed to start server, attempt {i+1} of {max_retries}")
                time.sleep(5)  # Wait before retry
            except Exception as e:
                logger.error(f"Error starting server (attempt {i+1}): {e}")
                if i < max_retries - 1:  # Don't sleep on last attempt
                    time.sleep(5)
                    
        raise Exception("Failed to start MCP server after multiple attempts")
            
    def execute_tool(self, tool_name, params):
        """Execute a tool through the server"""
        try:
            return self.server.execute_tool(tool_name, params)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            # Try to restart server on failure
            try:
                logger.info("Attempting to restart server...")
                self.server.stop()
                if self.server.start():
                    logger.info("Server restarted, retrying tool execution")
                    return self.server.execute_tool(tool_name, params)
            except Exception as restart_error:
                logger.error(f"Failed to restart server: {restart_error}")
            return None
        
    def __del__(self):
        """Cleanup when manager is destroyed"""
        if hasattr(self, 'server'):
            self.server.stop()

# Initialize MCP Manager
try:
    mcp_manager = MCPManager(config)
except Exception as e:
    logger.error(f"Failed to initialize MCP Manager: {e}")
    mcp_manager = None

def read_prompt_file(filename):
    """Reads the content of the specified file."""
    if not os.path.exists(filename):
        logger.error(f"Error: Prompt file '{filename}' not found.")
        return None
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        if not content.strip():
            logger.warning(f"Warning: Prompt file '{filename}' is empty.")
        return content
    except Exception as e:
        logger.error(f"Error reading file '{filename}': {e}")
        return None

def send_message_to_kindroid(api_key, ai_id, message, call_description="Kindroid"):
    """Sends the message to the Kindroid API using MCP client and returns the response text."""
    if message is None:
        logger.warning(f"Warning: Attempting to send None message to {call_description}. Skipping.")
        return None
    if not message.strip():
        logger.warning(f"Warning: Sending empty message to {call_description}.")

    logger.info(f"\nSending message to {call_description} AI (ID: {ai_id})... (Length: {len(message)} chars)")
    logger.info(f"Message Content: {message}")

    try:
        url = KINDROID_BASE_URL + KINDROID_ENDPOINT
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "ai_id": ai_id,
            "message": message
        }

        response = requests.post(url, headers=headers, json=payload, timeout=180)

        if response.status_code == 200:
            logger.info(f"{call_description} message sent successfully. Received response.")
            raw_text = response.text
            logger.info(f"Received Text: {raw_text}")
            return raw_text
        else:
            logger.error(f"\nError from {call_description} API:")
            logger.error(f"Status Code: {response.status_code}")
            logger.error(f"Response Body: {response.text}")
            return None

    except Exception as e:
        logger.error(f"\nAn unexpected error occurred during {call_description} request: {e}")
        return None

def send_message_to_gemini(api_key, model, message, max_tokens):
    """Sends the message to the Google Gemini API and returns the response text."""
    if message is None:
        logger.warning("Warning: Attempting to send None message to Gemini. Skipping.")
        return None
    if not message.strip():
        logger.warning("Warning: Sending empty message to Gemini.")

    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    headers = {
        "Content-Type": "application/json"
    }
    
    # Read the system prompt
    system_prompt = read_prompt_file(GEMINI_SYSTEM_PROMPT)
    if not system_prompt:
        logger.error("Error: Could not read Gemini system prompt.")
        return None

    payload = {
        "contents": [{
            "parts": [
                {"text": system_prompt},
                {"text": message}
            ]
        }],
        "generationConfig": {
            "maxOutputTokens": max_tokens
        }
    }

    logger.info(f"\nSending message to Google Gemini ({model}) with max_tokens={max_tokens}... (Length: {len(message)} chars)")
    logger.info(f"Message Content: {message}")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=180)

        if response.status_code == 200:
            logger.info("Gemini message sent successfully. Received response.")
            try:
                response_data = response.json()
                candidates = response_data.get('candidates', [])
                if not candidates:
                    prompt_feedback = response_data.get('promptFeedback')
                    if prompt_feedback and prompt_feedback.get('blockReason'):
                        block_reason = prompt_feedback.get('blockReason')
                        logger.error(f"Error: Gemini request blocked. Reason: {block_reason}")
                        return f"Error: Gemini request blocked due to {block_reason}"
                    else:
                        logger.error("Error: Gemini response missing 'candidates'.")
                        return None

                content = candidates[0].get('content', {})
                parts = content.get('parts', [])
                if parts:
                    generated_text = parts[0].get('text')
                    if generated_text is not None:
                        finish_reason = candidates[0].get('finishReason')
                        if finish_reason and finish_reason not in ["STOP", "MAX_TOKENS"]:
                            logger.warning(f"Warning: Gemini generation finished unexpectedly. Reason: {finish_reason}")
                        elif finish_reason == "MAX_TOKENS":
                            logger.info(f"Info: Gemini response may have been truncated by the model due to maxOutputTokens ({max_tokens}).")

                        logger.info(f"Received Text: {generated_text}")
                        return generated_text

                logger.error("Error: Could not parse expected text from Gemini response.")
                return None
            except json.JSONDecodeError:
                logger.error(f"Error: Gemini response is not valid JSON.")
                logger.error(f"Raw Gemini response: {response.text}")
                return None
            except Exception as e:
                logger.error(f"Error parsing Gemini response: {e}")
                return None
        else:
            logger.error(f"\nError from Gemini API:")
            logger.error(f"Status Code: {response.status_code}")
            try:
                error_details = response.json()
                logger.error(f"Response Body: {json.dumps(error_details, indent=2)}")
            except json.JSONDecodeError:
                logger.error(f"Response Body: {response.text}")
            return None

    except requests.exceptions.Timeout:
        logger.error("\nError: Request to Gemini API timed out.")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"\nError during Gemini API request: {e}")
        return None
    except Exception as e:
        logger.error(f"\nAn unexpected error occurred during Gemini request: {e}")
        return None

def process_gemini_response(response):
    """Process Gemini's response and execute any tool calls"""
    if not response:
        return None
        
    try:
        # First try to find a JSON block in the response
        json_start = response.find('{')
        json_end = response.rfind('}') + 1
        
        if json_start >= 0 and json_end > json_start:
            # Extract the JSON string
            json_str = response[json_start:json_end]
            try:
                tool_call = json.loads(json_str)
                # Check if this is a valid tool call
                if isinstance(tool_call, dict) and tool_call.get('tool') == 'write_file':
                    # Convert to MCP filesystem format
                    mcp_params = {
                        'type': 'request',
                        'tool': 'write_file',
                        'path': tool_call.get('path'),
                        'content': tool_call.get('content')
                    }
                    logger.info(f"Executing filesystem write: {tool_call.get('path')}")
                    result = mcp_manager.execute_tool('write_file', mcp_params)
                    
                    if result is not None:
                        return f"Operation completed successfully: File '{tool_call.get('path')}' was created."
                    else:
                        return "Operation failed. Please check the logs for details."
            except json.JSONDecodeError:
                logger.error("Error parsing tool call JSON")
                return None
        
        # If no tool call found or not properly formatted, return the response as is
        return response
    except Exception as e:
        logger.error(f"Error processing Gemini response: {e}")
        return None

def run_api_chain():
    """Reads prompt, calls Kindroid, then Gemini, then Kindroid again."""
    logger.info(f"\n--- Running API Chain @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    # 1. Read the initial prompt message
    logger.info(f"Step 1: Reading initial prompt from: {PROMPT_FILENAME}")
    initial_prompt_message = read_prompt_file(PROMPT_FILENAME)
    if initial_prompt_message is None:
        logger.error("Error: Could not read prompt file. Skipping API calls for this run.")
        return
    if not initial_prompt_message.strip():
        logger.warning("Warning: Prompt file is empty. Proceeding with empty message.")

    # 2. Send initial prompt to Kindroid
    logger.info("\nStep 2: Sending initial prompt to Kindroid...")
    kindroid_response_1 = send_message_to_kindroid(
        KINDROID_API_KEY,
        KINDROID_AI_ID,
        initial_prompt_message,
        call_description="Kindroid (Initial)"
    )

    if kindroid_response_1 is None:
        logger.error("Error: Failed to get initial response from Kindroid. Skipping rest of chain for this run.")
        return

    # 3. Send Kindroid's response to Gemini
    logger.info("\nStep 3: Sending Kindroid's response to Gemini...")
    gemini_response = send_message_to_gemini(
        GEMINI_API_KEY,
        GEMINI_MODEL,
        kindroid_response_1,
        GEMINI_MAX_OUTPUT_TOKENS
    )

    if gemini_response is None:
        logger.error("Error: Failed to get response from Gemini. Skipping final Kindroid call for this run.")
        return

    # 4. Process Gemini's response and execute any tool calls
    logger.info("\nStep 4: Processing Gemini's response...")
    processed_response = process_gemini_response(gemini_response)
    
    if processed_response is None:
        logger.error("Error: Failed to process Gemini's response. Skipping final Kindroid call.")
        return

    # 5. Truncate response if necessary
    if len(processed_response) > MAX_RESPONSE_CHARS:
        logger.info(f"Truncating response from {len(processed_response)} to {MAX_RESPONSE_CHARS} characters")
        processed_response = processed_response[:MAX_RESPONSE_CHARS]

    # 6. Send processed response back to Kindroid
    logger.info("\nStep 5: Sending processed response back to Kindroid...")
    kindroid_response_2 = send_message_to_kindroid(
        KINDROID_API_KEY,
        KINDROID_AI_ID,
        f"[System Assistant] {processed_response}",
        call_description="Kindroid (Final)"
    )

    if kindroid_response_2 is None:
        logger.error("Error: Failed to get final response from Kindroid.")
    else:
        logger.info("\nStep 6: Received final response from Kindroid.")

    logger.info(f"\n--- API Chain Run Finished @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

# --- Main Execution & Scheduling ---

if __name__ == "__main__":
    logger.info("Script starting...")
    # Run the chain once immediately on startup
    logger.info("Performing initial run...")
    run_api_chain()

    # Schedule the job to run every X minutes
    logger.info(f"\nScheduling job to run every {RUN_INTERVAL_MINUTES} minutes.")
    schedule.every(RUN_INTERVAL_MINUTES).minutes.do(run_api_chain)

    # Keep the script running to allow the scheduler to work
    logger.info("Starting scheduler loop. Press Ctrl+C to exit.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("\nScheduler stopped by user.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\nAn unexpected error occurred in the scheduler loop: {e}")
        sys.exit(1)