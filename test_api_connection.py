"""
Test Cerebras API connection
Verifies that the API key and endpoint are correctly configured
"""
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def test_api_connection():
    """Test Cerebras API connection"""
    
    logger.info("=" * 70)
    logger.info("Testing Cerebras API Connection")
    logger.info("=" * 70)
    
    try:
        from config import CEREBRAS_API_KEY, CEREBRAS_BASE_URL, CEREBRAS_MODEL
        from anthropic import Anthropic
        
        logger.info(f"API Key: {CEREBRAS_API_KEY[:20]}..." if len(CEREBRAS_API_KEY) > 20 else CEREBRAS_API_KEY)
        logger.info(f"Base URL: {CEREBRAS_BASE_URL}")
        logger.info(f"Model: {CEREBRAS_MODEL}")
        
        logger.info("\nInitializing Anthropic client...")
        client = Anthropic(
            api_key=CEREBRAS_API_KEY,
            base_url=CEREBRAS_BASE_URL,
            timeout=30
        )
        logger.info("✓ Client initialized")
        
        logger.info("\nSending test message to Cerebras API...")
        response = client.messages.create(
            model=CEREBRAS_MODEL,
            max_tokens=100,
            messages=[
                {
                    "role": "user",
                    "content": "Say 'API connection successful!' in one line only."
                }
            ]
        )
        
        logger.info("✓ API response received!")
        logger.info(f"Response: {response.content[0].text}")
        logger.info("\n" + "=" * 70)
        logger.info("✓ Cerebras API connection test PASSED")
        logger.info("=" * 70)
        return 0
        
    except Exception as e:
        logger.error(f"✗ API connection test FAILED: {e}", exc_info=True)
        logger.info("\n" + "=" * 70)
        logger.error("Troubleshooting tips:")
        logger.error("1. Verify CEREBRAS_API_KEY is set correctly in .env")
        logger.error("2. Check if your API key is still valid")
        logger.error("3. Verify internet connection")
        logger.error("4. Check Cerebras API status at https://www.cerebras.ai/")
        logger.info("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(test_api_connection())
