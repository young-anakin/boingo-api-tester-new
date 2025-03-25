import redis
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    r = redis.Redis(host='localhost', port=6379, db=0)
    r.ping()
    logger.info("Successfully connected to Redis")
except redis.ConnectionError as e:
    logger.error(f"Failed to connect to Redis: {str(e)}") 