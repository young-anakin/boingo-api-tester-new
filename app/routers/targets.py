# app/routers/targets.py
from fastapi import APIRouter, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
import httpx
import json
import logging
import redis
from datetime import datetime
from celery import chain
from celery.result import AsyncResult
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Imports from your project
from ..models.models import (
    ScrapingTargetCreate, ScrapingTargetUpdate,
    ScrapingTargetDelete, ScrapingTargetPause
)
from ..core.config import BOINGO_API_URL
from .auth import security
from ..core.celery_app import celery_app  # Import the shared Celery app

# Initialize Redis client for storing task metadata
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0)
    redis_client.ping()
    logger.info("Successfully connected to Redis for task metadata storage")
except redis.ConnectionError as e:
    logger.error(f"Failed to connect to Redis: {str(e)}")
    raise

router = APIRouter(
    prefix="/scraping-target",
    tags=["Scraping Target API"],
    responses={404: {"description": "Not found"}},
)

# Simplified response model for API responses
class ScrapingResultResponse(BaseModel):
    id: str
    target_id: str
    status: str
    website_url: str
    num_listings: Optional[int] = None
    error: Optional[str] = None

@router.get("")
async def get_all_targets(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Get all scraping targets
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BOINGO_API_URL}/scraping-target",
                headers={"Authorization": f"Bearer {credentials.credentials}"}
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return response.json()
    except Exception as e:
        logger.error(f"Error getting targets: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting targets: {str(e)}")

@router.get("/{target_id}")
async def get_target_by_id(target_id: str, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Get scraping target by ID
    """
    try:
        logger.info(f"Fetching target by ID: {target_id}")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BOINGO_API_URL}/scraping-target/{target_id}",
                headers={"Authorization": f"Bearer {credentials.credentials}"}
            )
            logger.info(f"Boingo API response status: {response.status_code}")
            logger.debug(f"Boingo API response body: {response.text}")
            if response.status_code < 200 or response.status_code >= 300:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = json.dumps(error_json, indent=2)
                except:
                    pass
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"API Error: {error_detail}"
                )
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Network error while fetching target {target_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        logger.error(f"Error fetching target {target_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting target: {str(e)}")

@router.post("", response_model=ScrapingResultResponse)
async def create_target(target: ScrapingTargetCreate, credentials: HTTPAuthorizationCredentials = Security(security)):
    try:
        logger.info(f"Creating new scraping target for URL: {target.website_url}")
        logger.debug(f"Request body: {json.dumps(target.dict(), indent=2)}")

        # First, notify the Boingo API to create the scraping target
        logger.info("Notifying Boingo API")
        async with httpx.AsyncClient() as client:
            boingo_response = await client.post(
                f"{BOINGO_API_URL}/scraping-target",
                headers={
                    "Authorization": f"Bearer {credentials.credentials}",
                    "Content-Type": "application/json"
                },
                json=target.dict()
            )
            logger.info(f"Boingo API notification status: {boingo_response.status_code}")
            logger.debug(f"Boingo API response: {boingo_response.text}")

            # Check if the response is successful
            if boingo_response.status_code not in [200, 201]:
                logger.warning(f"Failed to notify Boingo API (status {boingo_response.status_code})")
                raise HTTPException(status_code=boingo_response.status_code, detail=f"Failed to notify Boingo API: {boingo_response.text}")

            # Parse the response to get the data.id
            try:
                response_data = boingo_response.json()
                target_id = response_data.get("data", {}).get("id")
                if not target_id:
                    logger.error("Boingo API response does not contain data.id")
                    raise HTTPException(status_code=500, detail="Boingo API response does not contain a valid target ID")
            except (ValueError, KeyError) as e:
                logger.error(f"Failed to parse Boingo API response: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Failed to parse Boingo API response: {str(e)}")

        # Use the data.id as the target_id
        max_depth = target.search_range if target.search_range is not None else 2
        max_pages = target.max_properties if target.max_properties is not None else 2
        max_chunks = 3
        scraper_delay = 2.0
        cleaner_delay = 2.0
        max_total_tokens = 2000

        logger.info("Preparing to queue Celery tasks")
        workflow = chain(
            celery_app.signature(
                "Crawler.property_pipeline.scrape_task",
                args=[
                    target.website_url,
                    max_depth,
                    max_pages,
                    max_chunks,
                    scraper_delay,
                    max_total_tokens,
                    target_id  # Use the data.id from Boingo API
                ],
                queue="scraper_queue"
            ),
            celery_app.signature(
                "Crawler.property_pipeline.clean_task",
                kwargs={
                    "delay_seconds": cleaner_delay
                },
                queue="cleaner_queue"
            )
        )
        logger.info("Workflow created, applying async")
        task = workflow.apply_async()
        logger.info(f"Task queued - Task ID: {task.id}, Target ID: {target_id}")

        task_metadata = {
            "target_id": target_id,
            "website_url": target.website_url,
            "status": "queued",
            "created_at": datetime.utcnow().isoformat()
        }
        redis_client.set(f"scraping_task:{task.id}", json.dumps(task_metadata))
        logger.info(f"Task metadata stored in Redis for Task ID: {task.id}")

        return ScrapingResultResponse(
            id=task.id,
            target_id=target_id,  # Use the data.id from Boingo API
            status="queued",
            website_url=target.website_url
        )
    except redis.ConnectionError as e:
        logger.error(f"Redis connection error: {str(e)}")
        raise HTTPException(status_code=503, detail="Redis is unavailable")
    except Exception as e:
        logger.error(f"Error creating target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating target: {str(e)}")
    
    
@router.get("/status/{task_id}", response_model=ScrapingResultResponse)
async def get_task_status(task_id: str, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Check the status of a scraping task.
    """
    try:
        logger.info(f"Checking status for task ID: {task_id}")

        # Retrieve task metadata from Redis
        task_metadata_raw = redis_client.get(f"scraping_task:{task_id}")
        if not task_metadata_raw:
            raise HTTPException(status_code=404, detail="Task not found")
        task_metadata = json.loads(task_metadata_raw)
        target_id = task_metadata.get("target_id", "unknown")
        website_url = task_metadata.get("website_url", "unknown")

        # Check Celery task status
        result = AsyncResult(task_id, app=celery_app)
        if result.state == "PENDING":
            status = "pending"
        elif result.state == "SUCCESS":
            status = "completed"
            # Update metadata with final result from clean_task
            task_metadata["status"] = "completed"
            task_metadata["num_listings"] = result.result.get("num_cleaned", 0)  # Use clean_task result
            redis_client.set(f"scraping_task:{task_id}", json.dumps(task_metadata))
        elif result.state == "FAILURE":
            status = "failed"
            # Update metadata with error
            task_metadata["status"] = "failed"
            task_metadata["error"] = str(result.result)
            redis_client.set(f"scraping_task:{task_id}", json.dumps(task_metadata))
        else:
            status = result.state.lower()

        return ScrapingResultResponse(
            id=task_id,
            target_id=target_id,
            status=status,
            website_url=website_url,
            num_listings=task_metadata.get("num_listings"),
            error=task_metadata.get("error")
        )
    except redis.ConnectionError as e:
        logger.error(f"Redis connection error: {str(e)}")
        raise HTTPException(status_code=503, detail="Redis is unavailable")
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error retrieving task status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving task status: {str(e)}")

@router.put("")
async def update_target(target: ScrapingTargetUpdate, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Update an existing scraping target
    """
    try:
        logger.info(f"Updating scraping target: {json.dumps(target.dict(), indent=2)}")
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{BOINGO_API_URL}/scraping-target",
                headers={
                    "Authorization": f"Bearer {credentials.credentials}",
                    "Content-Type": "application/json"
                },
                json=target.dict()
            )
            logger.info(f"Boingo API response status: {response.status_code}")
            logger.debug(f"Boingo API response: {response.text}")
            if response.status_code < 200 or response.status_code >= 300:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = json.dumps(error_json, indent=2)
                except:
                    pass
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"API Error: {error_detail}"
                )
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Network error while updating target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        logger.error(f"Error updating target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating target: {str(e)}")

@router.delete("")
async def delete_target(target: ScrapingTargetDelete, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Delete a scraping target
    """
    try:
        logger.info(f"Deleting scraping target: {json.dumps(target.dict(), indent=2)}")
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{BOINGO_API_URL}/scraping-target",
                headers={
                    "Authorization": f"Bearer {credentials.credentials}",
                    "Content-Type": "application/json"
                },
                json=target.dict()
            )
            logger.info(f"Boingo API response status: {response.status_code}")
            logger.debug(f"Boingo API response: {response.text}")
            if response.status_code < 200 or response.status_code >= 300:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = json.dumps(error_json, indent=2)
                except:
                    pass
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"API Error: {error_detail}"
                )
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Network error while deleting target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        logger.error(f"Error deleting target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting target: {str(e)}")

@router.post("/pause")
async def pause_target(target: ScrapingTargetPause, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Pause a scraping target
    """
    try:
        logger.info(f"Pausing scraping target: {json.dumps(target.dict(), indent=2)}")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BOINGO_API_URL}/scraping-target/pause",
                headers={
                    "Authorization": f"Bearer {credentials.credentials}",
                    "Content-Type": "application/json"
                },
                json=target.dict()
            )
            logger.info(f"Boingo API response status: {response.status_code}")
            logger.debug(f"Boingo API response: {response.text}")
            if response.status_code < 200 or response.status_code >= 300:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = json.dumps(error_json, indent=2)
                except:
                    pass
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"API Error: {error_detail}"
                )
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Network error while pausing target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        logger.error(f"Error pausing target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error pausing target: {str(e)}")

@router.post("/unpause")
async def unpause_target(target: ScrapingTargetPause, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Unpause a scraping target
    """
    try:
        logger.info(f"Unpausing scraping target: {json.dumps(target.dict(), indent=2)}")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BOINGO_API_URL}/scraping-target/unpause",
                headers={
                    "Authorization": f"Bearer {credentials.credentials}",
                    "Content-Type": "application/json"
                },
                json=target.dict()
            )
            logger.info(f"Boingo API response status: {response.status_code}")
            logger.debug(f"Boingo API response: {response.text}")
            if response.status_code < 200 or response.status_code >= 300:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = json.dumps(error_json, indent=2)
                except:
                    pass
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"API Error: {error_detail}"
                )
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Network error while unpausing target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        logger.error(f"Error unpausing target: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error unpausing target: {str(e)}")