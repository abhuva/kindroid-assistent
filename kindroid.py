import requests
import os
import sys
import json
from dotenv import load_dotenv
import schedule
import time
import yaml

# --- Configuration Loading ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILENAME = os.path.join(SCRIPT_DIR, "kindroid.env")
CONFIG_FILENAME = os.path.join(SCRIPT_DIR, "config.yaml")
PROMPT_FILENAME = os.path.join(SCRIPT_DIR, "prompt.md")

# Load API keys from .env file
if not os.path.exists(ENV_FILENAME):
    print(f"Error: Environment file '{ENV_FILENAME}' not found.")
    print("Please create it with KINDROID_API_KEY, KINDROID_AI_ID, and GEMINI_API_KEY.")
    sys.exit(1)

load_dotenv(dotenv_path=ENV_FILENAME)

# Credentials
KINDROID_API_KEY = os.getenv("KINDROID_API_KEY")
KINDROID_AI_ID = os.getenv("KINDROID_AI_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Validate Credentials ---
if not KINDROID_API_KEY:
    print("Error: KINDROID_API_KEY not found in environment variables.")
    sys.exit(1)
if not KINDROID_AI_ID:
    print("Error: KINDROID_AI_ID not found in environment variables.")
    sys.exit(1)
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY not found in environment variables.")
    sys.exit(1)
# --- End Credential Validation ---

# Load configuration from YAML file
if not os.path.exists(CONFIG_FILENAME):
    print(f"Error: Configuration file '{CONFIG_FILENAME}' not found.")
    print("Please create it with your configuration settings.")
    sys.exit(1)

try:
    with open(CONFIG_FILENAME, 'r') as file:
        config = yaml.safe_load(file)
except Exception as e:
    print(f"Error loading configuration file: {e}")
    sys.exit(1)

# --- Get Run Interval (Minutes) ---
DEFAULT_INTERVAL_MINUTES = 180
try:
    RUN_INTERVAL_MINUTES = config.get('run_interval_minutes', DEFAULT_INTERVAL_MINUTES)
    if RUN_INTERVAL_MINUTES <= 0:
        print(f"Warning: run_interval_minutes must be positive. Using default ({DEFAULT_INTERVAL_MINUTES} minutes).")
        RUN_INTERVAL_MINUTES = DEFAULT_INTERVAL_MINUTES
except (ValueError, TypeError):
    print(f"Warning: Invalid run_interval_minutes. Using default ({DEFAULT_INTERVAL_MINUTES} minutes).")
    RUN_INTERVAL_MINUTES = DEFAULT_INTERVAL_MINUTES

# --- Get Response Limits ---
DEFAULT_MAX_TOKENS = 1000
DEFAULT_MAX_CHARS = 3500
try:
    GEMINI_MAX_OUTPUT_TOKENS = config.get('gemini', {}).get('max_output_tokens', DEFAULT_MAX_TOKENS)
    if GEMINI_MAX_OUTPUT_TOKENS <= 0:
        print(f"Warning: gemini.max_output_tokens must be positive. Using default ({DEFAULT_MAX_TOKENS}).")
        GEMINI_MAX_OUTPUT_TOKENS = DEFAULT_MAX_TOKENS
except (ValueError, TypeError):
    print(f"Warning: Invalid gemini.max_output_tokens. Using default ({DEFAULT_MAX_TOKENS}).")
    GEMINI_MAX_OUTPUT_TOKENS = DEFAULT_MAX_TOKENS

try:
    MAX_RESPONSE_CHARS = config.get('gemini', {}).get('max_response_chars', DEFAULT_MAX_CHARS)
    if MAX_RESPONSE_CHARS <= 0:
        print(f"Warning: gemini.max_response_chars must be positive. Using default ({DEFAULT_MAX_CHARS}).")
        MAX_RESPONSE_CHARS = DEFAULT_MAX_CHARS
except (ValueError, TypeError):
    print(f"Warning: Invalid gemini.max_response_chars. Using default ({DEFAULT_MAX_CHARS}).")
    MAX_RESPONSE_CHARS = DEFAULT_MAX_CHARS

# --- API Config ---
GEMINI_MODEL = "gemini-1.5-flash"
KINDROID_BASE_URL = "https://api.kindroid.ai/v1"
KINDROID_ENDPOINT = "/send-message" # Assuming this is correct
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# --- End Configuration ---


# --- Functions ---

def read_prompt_file(filename):
    """Reads the content of the specified file."""
    if not os.path.exists(filename):
        print(f"Error: Prompt file '{filename}' not found.")
        return None
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        if not content.strip():
            print(f"Warning: Prompt file '{filename}' is empty.")
            # Decide if an empty prompt should proceed or stop
            # return None # Option: stop if empty
        return content
    except Exception as e:
        print(f"Error reading file '{filename}': {e}")
        return None

def send_message_to_kindroid(api_key, ai_id, message, call_description="Kindroid"):
    """Sends the message to the Kindroid API and returns the response text."""
    if message is None: # Check for None explicitly
        print(f"Warning: Attempting to send None message to {call_description}. Skipping.")
        return None
    # Allow sending empty messages if intended, but log it
    if not message.strip():
        print(f"Warning: Sending empty message to {call_description}.")

    url = KINDROID_BASE_URL + KINDROID_ENDPOINT
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "ai_id": ai_id,
        "message": message
    }

    print(f"\nSending message to {call_description} AI (ID: {ai_id})... (Length: {len(message)} chars)")
    print(f"  Message Content (first 100 chars): {message[:100]}{'...' if len(message) > 100 else ''}") # Log message start

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=180) # 3 min timeout

        if response.status_code == 200:
            print(f"{call_description} message sent successfully. Received response.")
            try:
                response_data = response.json()
                # --- IMPORTANT: Adjust this based on Kindroid's actual response structure ---
                kindroid_reply = response_data.get('message')
                # --- End Adjustment Section ---

                if kindroid_reply is not None:
                    print(f"  Received Text (first 100 chars): {kindroid_reply[:100]}{'...' if len(kindroid_reply) > 100 else ''}") # Log response start
                    return kindroid_reply
                else:
                    print(f"Error: Could not find 'message' key in {call_description} JSON response.")
                    print(f"Raw {call_description} response JSON: {json.dumps(response_data, indent=2)}")
                    return None # Indicate failure to extract message
            except json.JSONDecodeError:
                # If response isn't JSON, maybe the raw text is the reply?
                print(f"Warning: {call_description} response was not valid JSON. Returning raw text.")
                raw_text = response.text
                print(f"  Received Text (first 100 chars): {raw_text[:100]}{'...' if len(raw_text) > 100 else ''}") # Log response start
                return raw_text
            except Exception as e:
                print(f"Error parsing {call_description} response: {e}")
                print(f"Raw {call_description} response: {response.text}")
                return None
        else:
            print(f"\nError from {call_description} API:")
            print(f"Status Code: {response.status_code}")
            try:
                error_details = response.json()
                print(f"Response Body: {json.dumps(error_details, indent=2)}")
            except json.JSONDecodeError:
                print(f"Response Body: {response.text}")
            return None
    except requests.exceptions.Timeout:
        print(f"\nError: Request to {call_description} API timed out.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"\nError during {call_description} API request: {e}")
        return None
    except Exception as e:
        print(f"\nAn unexpected error occurred during {call_description} request: {e}")
        return None


def send_message_to_gemini(api_key, model, message, max_tokens):
    """Sends the message to the Google Gemini API and returns the response text."""
    if message is None: # Check for None explicitly
        print("Warning: Attempting to send None message to Gemini. Skipping.")
        return None
    if not message.strip():
        print("Warning: Sending empty message to Gemini.")
        # Decide if you want to proceed or return None here
        # return None # Option: skip if message is only whitespace

    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [{
            "parts": [{"text": message}]
        }],
        "generationConfig": {
          "maxOutputTokens": max_tokens
          # "temperature": 0.7, # Optional: Adjust creativity
        }
    }

    print(f"\nSending message to Google Gemini ({model}) with max_tokens={max_tokens}... (Length: {len(message)} chars)")
    print(f"  Message Content (first 100 chars): {message[:100]}{'...' if len(message) > 100 else ''}") # Log message start

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=180) # 3 min timeout

        if response.status_code == 200:
            print("Gemini message sent successfully. Received response.")
            try:
                response_data = response.json()
                # Check for safety ratings / blocks first
                candidates = response_data.get('candidates', [])
                if not candidates:
                    prompt_feedback = response_data.get('promptFeedback')
                    if prompt_feedback and prompt_feedback.get('blockReason'):
                        block_reason = prompt_feedback.get('blockReason')
                        print(f"Error: Gemini request blocked. Reason: {block_reason}")
                        # Consider logging safety ratings: print(f"Safety Ratings: {prompt_feedback.get('safetyRatings')}")
                        return f"Error: Gemini request blocked due to {block_reason}" # Return error message
                    else:
                        print("Error: Gemini response missing 'candidates'.")
                        print(f"Raw Gemini response JSON: {json.dumps(response_data, indent=2)}")
                        return None # Indicate failure

                # Safely extract text if candidates exist
                content = candidates[0].get('content', {})
                parts = content.get('parts', [])
                if parts:
                    generated_text = parts[0].get('text')
                    if generated_text is not None:
                         finish_reason = candidates[0].get('finishReason')
                         if finish_reason and finish_reason not in ["STOP", "MAX_TOKENS"]:
                             print(f"Warning: Gemini generation finished unexpectedly. Reason: {finish_reason}")
                         elif finish_reason == "MAX_TOKENS":
                             print(f"Info: Gemini response may have been truncated by the model due to maxOutputTokens ({max_tokens}).")

                         print(f"  Received Text (first 100 chars): {generated_text[:100]}{'...' if len(generated_text) > 100 else ''}") # Log response start
                         return generated_text

                # If extraction fails
                print("Error: Could not parse expected text from Gemini response.")
                print(f"Raw Gemini response JSON: {json.dumps(response_data, indent=2)}")
                return None
            except json.JSONDecodeError:
                print(f"Error: Gemini response is not valid JSON.")
                print(f"Raw Gemini response: {response.text}")
                return None
            except Exception as e: # Catch potential index errors or other parsing issues
                print(f"Error parsing Gemini response: {e}")
                # Check if response_data was assigned before trying to dump it
                try:
                    print(f"Raw Gemini response JSON: {json.dumps(response_data, indent=2)}")
                except NameError:
                     print(f"Raw Gemini response: {response.text}") # Fallback if response_data wasn't created
                return None
        else:
            print(f"\nError from Gemini API:")
            print(f"Status Code: {response.status_code}")
            try:
                error_details = response.json()
                print(f"Response Body: {json.dumps(error_details, indent=2)}")
            except json.JSONDecodeError:
                print(f"Response Body: {response.text}")
            return None

    except requests.exceptions.Timeout:
        print("\nError: Request to Gemini API timed out.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"\nError during Gemini API request: {e}")
        return None
    except Exception as e:
        print(f"\nAn unexpected error occurred during Gemini request: {e}")
        return None


# --- Core Logic Function ---
def run_api_chain():
    """Reads prompt, calls Kindroid, then Gemini, then Kindroid again."""
    print(f"\n--- Running API Chain @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---")

    # 1. Read the initial prompt message
    print(f"Step 1: Reading initial prompt from: {PROMPT_FILENAME}")
    initial_prompt_message = read_prompt_file(PROMPT_FILENAME)
    if initial_prompt_message is None: # Check for None explicitly
        print("Error: Could not read prompt file. Skipping API calls for this run.")
        return # Stop execution for this run
    if not initial_prompt_message.strip():
        print("Warning: Prompt file is empty. Proceeding with empty message.")
        # Or decide to return here if an empty prompt shouldn't trigger the chain

    # 2. Send initial prompt to Kindroid
    print("\nStep 2: Sending initial prompt to Kindroid...")
    kindroid_response_1 = send_message_to_kindroid(
        KINDROID_API_KEY,
        KINDROID_AI_ID,
        initial_prompt_message,
        call_description="Kindroid (Initial)"
    )

    if kindroid_response_1 is None:
        print("Error: Failed to get initial response from Kindroid. Skipping rest of chain for this run.")
        return # Stop execution for this run

    # 3. Send Kindroid's response to Gemini
    print("\nStep 3: Sending Kindroid's response to Gemini...")
    gemini_response = send_message_to_gemini(
        GEMINI_API_KEY,
        GEMINI_MODEL,
        kindroid_response_1,
        GEMINI_MAX_OUTPUT_TOKENS # Pass the max tokens limit
    )

    if gemini_response is None:
        print("Error: Failed to get response from Gemini. Skipping final Kindroid call for this run.")
        return # Stop execution for this run

    # Check if Gemini returned an error message (like blocked content)
    if gemini_response.startswith("Error: Gemini request blocked"):
        print(f"Gemini response indicates an error: '{gemini_response}'. Skipping final Kindroid call.")
        return # Stop execution for this run

    # 4. Truncate Gemini's response if necessary
    print(f"\nStep 4: Processing Gemini's response (Original length: {len(gemini_response)} chars)...")
    truncated_gemini_response = gemini_response
    if len(gemini_response) > MAX_RESPONSE_CHARS:
        print(f"  Truncating Gemini response to {MAX_RESPONSE_CHARS} characters.")
        truncated_gemini_response = gemini_response[:MAX_RESPONSE_CHARS]
        print(f"  Truncated length: {len(truncated_gemini_response)} chars.")
    else:
        print("  Gemini response within character limit. No truncation needed.")


    # 5. Send truncated Gemini response back to Kindroid
    print("\nStep 5: Sending processed Gemini response back to Kindroid...")
    kindroid_response_2 = send_message_to_kindroid(
        KINDROID_API_KEY,
        KINDROID_AI_ID,
        "SYSTEM ANSWER: "+truncated_gemini_response,
        call_description="Kindroid (Final)"
    )

    if kindroid_response_2 is None:
        print("Error: Failed to get final response from Kindroid.")
    else:
        # You might not need to *do* anything with the final response,
        # but logging it is good practice.
        print("\nStep 6: Received final response from Kindroid.")
        # The send_message_to_kindroid function already logs the start of the received text.

    print(f"\n--- API Chain Run Finished @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---")


# --- Main Execution & Scheduling ---

if __name__ == "__main__":
    print("Script starting...")
    # Run the chain once immediately on startup
    print("Performing initial run...")
    run_api_chain()

    # Schedule the job to run every X minutes
    print(f"\nScheduling job to run every {RUN_INTERVAL_MINUTES} minutes.")
    schedule.every(RUN_INTERVAL_MINUTES).minutes.do(run_api_chain)

    # Keep the script running to allow the scheduler to work
    print("Starting scheduler loop. Press Ctrl+C to exit.")
    try:
        while True:
            schedule.run_pending()
            # Sleep for a reasonable interval to avoid busy-waiting
            # Checking every 60 seconds is usually fine for minute-based schedules
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nScheduler stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nAn unexpected error occurred in the scheduler loop: {e}")
        sys.exit(1) # Exit with error status