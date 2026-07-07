import os

# mcpserver.main reads these at import time to build its module-level FastMCP
# singleton, and it calls load_dotenv() (which does not override already-set
# variables) before doing so. Without pinning them here, whatever the
# developer's real .env happens to have (e.g. MCP_OAUTH_ENABLED=true, set
# while testing against a live Keycloak) leaks into every test run and
# breaks assumptions like "OAuth is off unless a test explicitly enables it".
os.environ["MCP_OAUTH_ENABLED"] = "false"
os.environ["MCP_ALLOWED_HOSTS"] = ""
os.environ["MCP_ALLOWED_ORIGINS"] = ""
