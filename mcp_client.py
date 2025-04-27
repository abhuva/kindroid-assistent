import json
import logging
import time
import threading

logger = logging.getLogger('kindroid')

class MCPClient:
    def __init__(self, process):
        self.process = process
        self.request_id = 0
        
    def execute_tool(self, tool_name, params):
        """Execute a tool using the MCP protocol over process pipes"""
        try:
            # Increment request ID
            self.request_id += 1
            
            # Create MCP request
            request = {
                "type": "request",
                "id": str(self.request_id),
                "tool": tool_name,
                "params": params
            }
            
            # Send request to process stdin
            json_request = json.dumps(request)
            self.process.stdin.write(json_request + "\n")
            self.process.stdin.flush()
            
            # Read response from process stdout with timeout
            # Create a flag to indicate when we've received output
            output_received = threading.Event()
            response_data = [None]  # Use a list to store the output (to modify in the thread)
            
            # Function to read output in a separate thread
            def read_output():
                try:
                    line = self.process.stdout.readline()
                    if line:
                        response_data[0] = line
                        output_received.set()
                except Exception as e:
                    logger.error(f"Error reading server response: {e}")
            
            # Start a thread to read the output
            output_thread = threading.Thread(target=read_output)
            output_thread.daemon = True
            output_thread.start()
            
            # Wait for output with timeout
            if output_received.wait(timeout=5):
                response = response_data[0]
            else:
                logger.error(f"Timeout waiting for response from {tool_name}")
                return None
            
            try:
                result = json.loads(response)
                if result.get("type") == "error":
                    logger.error(f"Error executing {tool_name}: {result.get('error')}")
                    return None
                return result.get("result")
            except json.JSONDecodeError:
                logger.error(f"Error parsing response for {tool_name}: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error executing {tool_name}: {e}")
            return None 