import json
import logging
import time
import threading
import os
import sys
import queue
import subprocess
from typing import Optional, Dict

logger = logging.getLogger('kindroid')

class MCPClient:
    def __init__(self, server_cmd):
        """Initialize MCP client with server command"""
        self.server_cmd = server_cmd
        self.process = None
        self.request_id = 0
        self._lock = threading.Lock()
        self._response_queue = queue.Queue()
        self._output_thread = None
        self._error_thread = None
        
    def _process_server_output(self, line: str):
        """Process a line of server output."""
        if not line:
            return
            
        # Skip debug messages
        if line.startswith('[debug]'):
            logger.debug(f"Server debug: {line}")
            return
            
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                logger.warning(f"Received non-dict JSON: {data}")
                return
                
            msg_type = data.get('type')
            if msg_type == 'error':
                logger.error(f"Server error: {data.get('error')}")
                self._response_queue.put(data)
            elif msg_type == 'response':
                logger.info(f"Server response: {data}")
                self._response_queue.put(data)
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError:
            logger.debug(f"Non-JSON output: {line}")
            
    def _monitor_output(self, stream, name):
        """Monitor output stream and process lines."""
        try:
            for line in iter(stream.readline, b''):
                line = line.decode().strip()
                if line:
                    logger.debug(f"{name} output: {line}")
                    self._process_server_output(line)
        except Exception as e:
            logger.error(f"Error monitoring {name}: {e}")
        finally:
            stream.close()

    def start(self):
        """Start the MCP server process."""
        try:
            logger.info(f"Starting server with command: {self.server_cmd}")
            self.process = subprocess.Popen(
                self.server_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True
            )
            
            # Start output monitoring threads
            self._output_thread = threading.Thread(
                target=self._monitor_output,
                args=(self.process.stdout, "stdout"),
                daemon=True
            )
            self._error_thread = threading.Thread(
                target=self._monitor_output,
                args=(self.process.stderr, "stderr"),
                daemon=True
            )
            self._output_thread.start()
            self._error_thread.start()
            
            # Wait for server to indicate it's ready
            start_time = time.time()
            while time.time() - start_time < 30:
                if self.process.poll() is not None:
                    raise Exception(f"Server process terminated with code {self.process.poll()}")
                    
                # Test connection
                result = self.execute_tool("test")
                if result is not None:
                    logger.info("Server started successfully")
                    return True
                    
                time.sleep(1)
                
            raise Exception("Timeout waiting for server to start")
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            self.stop()
            return False

    def execute_tool(self, tool_name: str, **kwargs) -> Optional[Dict]:
        """Execute a tool on the server and return the response."""
        if not self.is_connected():
            logger.error("Not connected to server")
            return None
        
        try:
            with self._lock:
                self.request_id += 1
                request = {
                    'type': 'request',
                    'id': str(self.request_id),
                    'tool': tool_name,
                    'params': kwargs
                }
                request_str = json.dumps(request) + '\n'
                self.process.stdin.write(request_str.encode())
                self.process.stdin.flush()
            
            # Wait for response with timeout
            try:
                response = self._response_queue.get(timeout=30)
                if response.get('type') == 'error':
                    logger.error(f"Tool execution failed: {response.get('error')}")
                    return None
                return response.get('result')
            except queue.Empty:
                logger.error("Timeout waiting for tool response")
                return None
            
        except Exception as e:
            logger.error(f"Error executing {tool_name}: {e}")
            return None

    def is_connected(self):
        """Check if the client is connected to the server."""
        return self.process is not None and self.process.poll() is None

    def stop(self):
        """Stop the MCP server process and clean up resources."""
        try:
            # Clear response queue
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                except queue.Empty:
                    break

            if self.process:
                # Send terminate signal
                self.process.terminate()
                
                # Wait for process to end (with timeout)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Server process did not terminate, forcing kill")
                    self.process.kill()
                    self.process.wait()

                # Close pipes
                if self.process.stdin:
                    self.process.stdin.close()
                if self.process.stdout:
                    self.process.stdout.close()
                if self.process.stderr:
                    self.process.stderr.close()

                self.process = None

            # Wait for monitoring threads to finish
            if self._output_thread and self._output_thread.is_alive():
                self._output_thread.join(timeout=2)
            if self._error_thread and self._error_thread.is_alive():
                self._error_thread.join(timeout=2)

            self._output_thread = None
            self._error_thread = None
            
            logger.info("Server stopped and resources cleaned up")
            
        except Exception as e:
            logger.error(f"Error stopping server: {e}")
            # Ensure process is marked as stopped even if cleanup fails
            self.process = None
            self._output_thread = None 
            self._error_thread = None

    def __del__(self):
        """Ensure the server is stopped when the client is deleted."""
        self.stop()

    def send_request(self, request, timeout=30):
        """Send a request to the server and wait for response.
        
        Args:
            request (dict): The request to send
            timeout (int): Maximum time to wait for response in seconds
            
        Returns:
            dict: The response from the server
            
        Raises:
            ConnectionError: If server is not running
            TimeoutError: If response not received within timeout
            ValueError: If invalid request or response
        """
        if not self.is_connected():
            raise ConnectionError("Server is not running")
            
        try:
            # Convert request to JSON and send
            request_json = json.dumps(request)
            self.process.stdin.write(f"{request_json}\n".encode())
            self.process.stdin.flush()
            
            # Wait for response
            try:
                response = self._response_queue.get(timeout=timeout)
                
                # Parse response
                if isinstance(response, str):
                    try:
                        response = json.loads(response)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"Invalid JSON response: {e}")
                        
                if not isinstance(response, dict):
                    raise ValueError(f"Expected dict response, got {type(response)}")
                    
                return response
                
            except queue.Empty:
                raise TimeoutError(f"No response received within {timeout} seconds")
                
        except Exception as e:
            logger.error(f"Error sending request: {e}")
            raise 

    def test_connection(self, timeout=5):
        """Test the connection to the server by sending a ping request.
        
        Args:
            timeout (int): Maximum time to wait for response in seconds
            
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            request = {
                "type": "ping"
            }
            response = self.send_request(request, timeout=timeout)
            return response.get("type") == "pong"
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False 