from fastapi import FastAPI, UploadFile, File, Query, HTTPException
import uuid
import time
import os

app = FastAPI()

# 从环境变量读取密钥，不在代码写死！
MAIN_TOKEN = os.getenv("MAIN_TOKEN")
if not MAIN_TOKEN:
    raise RuntimeError("环境变量 MAIN_TOKEN 未配置！请到平台后台添加")

task_queue = {}
result_store = {}

def check_token(token: str):
    if token != MAIN_TOKEN:
        raise HTTPException(status_code=403, detail="访问密钥错误")

# 网站下发截屏任务
@app.get("/task/screenshot")
async def create_screenshot_task(token: str = Query()):
    check_token(token)
    tid = str(uuid.uuid4())
    task_queue[tid] = {"type": "screenshot", "time": time.time()}
    return {"task_id": tid}

# 网站下发点击任务
@app.get("/task/click")
async def create_click_task(x: int, y: int, token: str = Query()):
    check_token(token)
    tid = str(uuid.uuid4())
    task_queue[tid] = {"type": "click", "x": x, "y": y, "time": time.time()}
    return {"task_id": tid}

# APP轮询获取任务
@app.get("/client/poll")
async def poll_task(token: str = Query()):
    check_token(token)
    if task_queue:
        tid, data = task_queue.popitem()
        return {"task_id": tid, "data": data}
    return {"task_id": "null"}

# APP上传截图
@app.post("/client/upload_result")
async def upload_result(task_id: str, token: str = Query(), file: UploadFile = File()):
    check_token(token)
    img_bytes = await file.read()
    result_store[task_id] = img_bytes
    return {"status": "ok"}

# 前端获取截图
@app.get("/result/{task_id}")
async def get_image(task_id: str, token: str = Query()):
    check_token(token)
    if task_id not in result_store:
        raise HTTPException(status_code=404, detail="暂无图片")
    from fastapi.responses import Response
    return Response(content=result_store[task_id], media_type="image/png")

# 保活健康检测
@app.get("/ping")
async def ping():
    return {"status":"alive"}

