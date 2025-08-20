import logging
import sys
import uuid
from typing import List, Optional
import argparse
import threading
from cachetools import TTLCache
from datetime import timedelta

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
import uvicorn

from .pool import InstancePool
from .log import apply_logging_adapter

app = FastAPI()
logger = logging.getLogger(__name__)

instance_pool = None
tasks = TTLCache(maxsize=10000, ttl=timedelta(days=1).total_seconds())
client_key = None

class ProxyConfig(BaseModel):
    scheme: str
    host: str
    port: int

class LinkSocksConfig(BaseModel):
    url: str
    token: str

class CreateTaskRequest(BaseModel):
    clientKey: str
    type: str
    url: str
    userAgent: Optional[str] = None
    proxy: Optional[ProxyConfig] = None
    siteKey: Optional[str] = None
    action: Optional[str] = None
    content: bool = False
    linksocks: Optional[LinkSocksConfig] = None
    screencast_path: Optional[str] = None

class TaskResultRequest(BaseModel):
    clientKey: str
    taskId: str

@app.post("/createTask")
async def create_task(request: CreateTaskRequest):
    # Validate clientKey
    if request.clientKey != client_key:
        raise HTTPException(status_code=403, detail="Invalid clientKey")
    if request.type not in ["CloudflareChallenge", "RecaptchaInvisible", "Turnstile"]:
        raise HTTPException(status_code=400, detail="Unsupported task type")
    
    if request.type == "Turnstile" and not request.siteKey:
        raise HTTPException(status_code=400, detail="siteKey is required for Turnstile tasks")
    
    data = request.model_dump()
    data.pop("clientKey", None)
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "processing",
        "data": data,
        "result": None
    }
    
    threading.Thread(target=process_task, args=(task_id,), daemon=True).start()
    
    logger.info(f"Created task for: {task_id}.")
    
    return {"taskId": task_id}

@app.post("/getTaskResult")
async def get_task_result(request: TaskResultRequest):
    # Validate clientKey
    if request.clientKey != client_key:
        raise HTTPException(status_code=403, detail="Invalid clientKey")
    if request.taskId not in tasks:
        return {
            "status": "error",
            "error": "Task not found",
            "result": None
        }

    task = tasks[request.taskId]
    return {
        "status": task["status"],
        "result": task["result"] if task["status"] == "completed" else None
    }
    
def stop_instances():
    if instance_pool:
        instance_pool.stop()

def process_task(task_id: str):
    task = tasks[task_id]
    task_data = task["data"]
    try:
        result = instance_pool.run_task(task_data)
        task["result"] = result
        if result.get("success", False):
            logger.info(f"Job finished successfully: {task_id}.")
        else:
            logger.info(f"Job failed: {task_id} ({result.get('error', 'unknown reason')}).")
    finally:
        task["status"] = "completed"

def main(argl: List[str] = None, ready: threading.Event = None, log: bool = True):
    global instance_pool, client_key
    
    if argl is None:
        argl = sys.argv[1:]
    
    if log:
        apply_logging_adapter([
            ('http.*->.*', logging.DEBUG),
            ('server disconnect', logging.DEBUG),
            ('client disconnect', logging.DEBUG),
            ('server connect', logging.DEBUG),
            ('client connect', logging.DEBUG),
        ], level=10)
        logging.getLogger('hpack').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    parser = argparse.ArgumentParser(description="Cloudflare bypass API server")
    parser.add_argument("-K", "--clientKey", required=True, help="Client API key")
    parser.add_argument("-M", "--maxTasks", type=int, default=1, help="Maximum concurrent tasks")
    parser.add_argument("-P", "--port", type=int, default=3000, help="Server listen port")
    parser.add_argument("-H", "--host", default="localhost", help="Server listen host")
    parser.add_argument("-T", "--timeout", type=int, default=120, help="Maximum task timeout in seconds")
    parser.add_argument("-L", "--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("-V", "--vdisplay", action="store_true", help="Run browser in virtual display mode")
    parser.add_argument("--no-hazetunnel", action="store_true", help="Skip hazetunnel and connect directly to pproxy upstream")
    
    args = parser.parse_args(argl)

    # Store the expected clientKey for request validation
    client_key = args.clientKey
    
    if args.vdisplay:
        from pyvirtualdisplay import Display

        display = Display(visible=0, size=(1920, 1080))
        display.start()

    try:
        instance_pool = InstancePool(size=args.maxTasks, timeout=args.timeout, use_hazetunnel=not args.no_hazetunnel, headless=args.headless)
        instance_pool.init_instances()
        
        if ready:
            ready.set()
        
        try:
            uvicorn.run(app, host=args.host, port=args.port, log_config=None, log_level=None)
        finally:
            instance_pool.stop()
    finally:
        if args.vdisplay:
            display.stop()

if __name__ == '__main__':
    main()