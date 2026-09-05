import os

# Ensure all automated test runs use the deterministic fake provider
os.environ["LLM_PROVIDER"] = "fake"
