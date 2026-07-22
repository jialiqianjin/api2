from fastapi import FastAPI, UploadFile, File, Query, HTTPException
import uuid
import time
import os

app = FastAPI()

# 从平台环境变量读取密钥！不再写死代码
MAIN_TOKEN = os.getenv("MAIN_TOKEN")
if not MAIN_TOKEN:
    raise RuntimeError("请在FastAPICloud后台配置环境变量 MAIN_TOKEN")

task_queue = {}
result_store = {}

def check_token(token: str):
    if token != MAIN_TOKEN:
        raise HTTPException(status_code=401, detail="访问密钥错误")

@app.get("/task/screenshot")
async def create_screenshot_task(token: str = Query(...)):
    check_token(token)
    task_id = str(uuid.uuid4())
    task_queue[task_id] = {"type":"screenshot","create_time":time.time()}
    return {"task_id": task_id}

@app.get("/task/click")
async def create_click_task(x:int,y:int,token:str = Query(...)):
    check_token(token)
    task_id = str(uuid.uuid4())
    task_queue[task_id] = {"type":"click","x":x,"y":y,"create_time":time.time()}
    return {"task_id": task_id}

@app.get("/client/poll")
async def poll_task(token: str = Query(...)):
    check_token(token)
    if len(task_queue) > 0:
        tid = next(iter(task_queue))
        task_data = task_queue.pop(tid)
        return {"task_id":tid, "data":task_data}
    return {"task_id": None}

@app.post("/client/upload_result")
async def upload_result(task_id:str, file:UploadFile=File(...), token:str=Query(...)):
    check_token(token)
    data = await file.read()
    result_store[task_id] = {"data":data,"time":time.time()}
    return {"status":"ok"}

@app.get("/result/{task_id}")
async def get_result(task_id:str, token:str=Query(...)):
    check_token(token)
    item = result_store.get(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="暂无结果，请等待平板执行")
    from fastapi.responses import Response
    return Response(content=item["data"], media_type="image/png")
