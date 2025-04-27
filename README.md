# Kindroid

A Python application that integrates Kindroid AI with Google's Gemini model.

## Setup

1. Clone this repository
2. Install the required dependencies:
   ```
   pip install requests python-dotenv schedule pyyaml
   ```
3. Create your environment file:
   - Copy `kindroid.env.example` to `kindroid.env`
   - Fill in your API keys and credentials

4. Configure your settings in `config.yaml`:
   - Adjust the run interval
   - Set response limits for Gemini

## Configuration

### Environment Variables (kindroid.env)

This file contains your API keys and credentials. It is excluded from version control for security reasons.

```
# Kindroid Credentials
KINDROID_API_KEY="your_kindroid_api_key_here"
KINDROID_AI_ID="your_kindroid_ai_id_here"
BACKUP_KINDROID_AI_ID="your_backup_kindroid_ai_id_here"

# Google Gemini Credentials
GEMINI_API_KEY="your_gemini_api_key_here"
```

### Configuration Settings (config.yaml)

This file contains non-sensitive configuration settings that can be shared publicly.

```yaml
# Kindroid Configuration Settings

# Limits for Gemini Response
gemini:
  max_output_tokens: 1000
  max_response_chars: 3500

# Run Interval (in minutes)
run_interval_minutes: 90  # Example: 3 hours = 180 minutes
```

## Usage

Run the script with:

```
python kindroid.py
```

The script will:
1. Read your prompt from `prompt.md`
2. Send it to Kindroid AI
3. Process the response with Gemini
4. Send the processed response back to Kindroid AI
5. Repeat this process at the configured interval 