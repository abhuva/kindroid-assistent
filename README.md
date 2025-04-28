# Kindroid

A Python application that integrates Kindroid AI with Google's Gemini model, featuring secure filesystem operations through the Model Context Protocol (MCP).

## Prerequisites

1. Python 3.8 or higher
2. Node.js and NPM (for MCP server)
3. Git (for cloning the repository)

## Setup

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/kindroid.git
   cd kindroid
   ```

2. Install Python dependencies:
   ```bash
   pip install requests python-dotenv schedule pyyaml
   ```

3. Install MCP server globally:
   ```bash
   npm install -g @modelcontextprotocol/server-filesystem
   ```

4. Create required directories:
   ```bash
   mkdir -p data config prompts
   ```

5. Set up configuration files:
   - Copy `kindroid.env.example` to `kindroid.env`
   - Update `config/config.yaml` (or copy from example if not present)
   - Ensure prompt files exist in the `prompts` directory

## Configuration

### Environment Variables (kindroid.env)

This file contains your API keys and credentials. It is excluded from version control for security reasons.

```ini
# Kindroid Credentials
KINDROID_API_KEY="your_kindroid_api_key_here"
KINDROID_AI_ID="your_kindroid_ai_id_here"

# Google Gemini Credentials
GEMINI_API_KEY="your_gemini_api_key_here"
```

### Configuration Settings (config/config.yaml)

```yaml
# MCP Server Configuration
mcp_servers:
  filesystem:
    enabled: true
    allowed_directories:
      - "${workspaceFolder}/data"  # Working directory for LLM-generated content
    timeout_seconds: 30
    retry_attempts: 3

# Scheduling Configuration
scheduling:
  interval_minutes: 180  # Run every 3 hours

# Response Limits
response_limits:
  max_chars: 4000
  max_tokens: 1000

# Logging Configuration
logging:
  level: "INFO"
  file: "log.txt"
  max_size_mb: 10
  backup_count: 5
```

### Required Prompt Files

The application requires two prompt files in the `prompts` directory:

1. `initial_prompt.md`: Contains the initial message for starting conversations
2. `gemini_system.md`: Contains system instructions for the Gemini model

## Directory Structure

```
kindroid/
├── config/
│   └── config.yaml         # Configuration settings
├── prompts/
│   ├── initial_prompt.md   # Initial conversation prompt
│   └── gemini_system.md    # Gemini system instructions
├── data/                   # Working directory for generated content
├── kindroid.py            # Main application script
├── kindroid.env           # API keys and credentials
├── kindroid.env.example   # Template for environment variables
└── README.md             # This file
```

## Usage

1. Ensure all configuration files are properly set up
2. Run the script:
   ```bash
   python kindroid.py
   ```

The script will:
1. Start the MCP filesystem server
2. Read the initial prompt from `prompts/initial_prompt.md`
3. Send it to Kindroid AI
4. Process the response with Gemini
5. Send the processed response back to Kindroid AI
6. Repeat this process at the configured interval

## Troubleshooting

Common issues and solutions:

1. **MCP Server Issues**:
   - Ensure Node.js and NPM are installed
   - Check if the MCP server package is installed globally
   - Verify the `data` directory exists and has proper permissions

2. **Configuration Issues**:
   - Verify all API keys are correctly set in `kindroid.env`
   - Check if all required prompt files exist
   - Ensure `config.yaml` has the correct structure

3. **Permission Issues**:
   - Make sure the application has write access to the `data` directory
   - Check if log file location is writable

## License

[Your License Here] 