from fastapi import FastAPI, UploadFile, File, Query, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uuid
import time
import os
import json
from collections import OrderedDict

app = FastAPI()

# ========== 跨域配置 ==========
ALLOW_ORIGINS = [
    "https://jialiqianjin.l2.ink",
    "https://www.jialiqianjin.l2.ink"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 配置 ==========
MAIN_TOKEN = os.getenv("MAIN_TOKEN")
# 新增远程操控密码，只存在后端环境变量
REMOTE_CTRL_PWD = os.getenv("REMOTE_CTRL_PWD")
if not MAIN_TOKEN:
    raise RuntimeError("环境变量 MAIN_TOKEN 未配置！请到平台后台添加")
if not REMOTE_CTRL_PWD:
    raise RuntimeError("环境变量 REMOTE_CTRL_PWD 未配置！远程操控密码缺失")
TASK_EXPIRE_SEC = 30       # 任务30秒超时自动清理
MAX_SCREEN_CACHE = 15      # 最多保留15张截图，防止内存爆炸
SCREEN_EXPIRE_SEC = 60     # 截图60秒自动过期清理
MAX_UPLOAD_SIZE = 5 * 1024  # 限制单张图片最大5MB
# ========== 内存存储 ==========
# OrderedDict 保证任务先进先出 FIFO
task_queue = OrderedDict()
result_store = {}
# WebSocket全局连接
device_ws = None
client_ws_list = []
# ========== 工具函数 ==========
def check_token(token: str):
    if token != MAIN_TOKEN:
        raise HTTPException(status_code=403, detail="访问密钥错误")
def clean_expire():
    """清理过期任务 + 过期截图 + 限制缓存数量"""
    now = time.time()
    expired_tasks = [k for k, v in task_queue.items() if now - v["time"] > TASK_EXPIRE_SEC]
    for k in expired_tasks:
        del task_queue[k]
    expired_imgs = [tid for tid, data in result_store.items()
                    if now - data["time"] > SCREEN_EXPIRE_SEC]
    for tid in expired_imgs:
        del result_store[tid]
    if len(result_store) > MAX_SCREEN_CACHE:
        sorted_keys = sorted(result_store.keys(), key=lambda k: result_store[k]["time"])
        del_count = len(result_store) - MAX_SCREEN_CACHE
        for i in range(del_count):
            del result_store[sorted_keys[i]]
# ========== 原有HTTP接口全部保留（兼容旧功能） ==========
@app.get("/api/remote_auth", summary="远程操控密码验证接口")
async def remote_auth(token: str = Query(), pwd: str = Query()):
    check_token(token)
    if pwd == REMOTE_CTRL_PWD:
        return {"success": True, "msg": "验证通过"}
    else:
        return {"success": False, "msg": "密码错误，请重新输入"}
@app.get("/task/screenshot", summary="网站下发截屏任务")
async def create_screenshot_task(token: str = Query()):
    check_token(token)
    clean_expire()
    tid = str(uuid.uuid4())
    task_queue[tid] = {"type": "screenshot", "time": time.time()}
    return {"task_id": tid}
@app.get("/task/click", summary="网站下发点击任务")
async def create_click_task(x: int, y: int, token: str = Query()):
    check_token(token)
    clean_expire()
    tid = str(uuid.uuid4())
    task_queue[tid] = {"type": "click", "x": x, "y": y, "time": time.time()}
    return {"task_id": tid}
@app.get("/result/{task_id}", summary="前端获取截图图片")
async def get_image(task_id: str, token: str = Query()):
    check_token(token)
    if task_id not in result_store:
        raise HTTPException(status_code=404, detail="暂无图片")
    return Response(content=result_store[task_id]["data"], media_type="image/jpeg")
@app.get("/client/poll", summary="APP轮询获取任务")
async def poll_task(token: str = Query()):
    check_token(token)
    clean_expire()
    if task_queue:
        tid = next(iter(task_queue.keys()))
        data = task_queue.pop(tid)
        return {"task_id": tid, "data": data}
    return {"task_id": "null"}
@app.post("/client/upload_result", summary="APP上传截图结果")
async def upload_result(task_id: str, token: str = Query(), file: UploadFile = File()):
    check_token(token)
    if file.size and file.size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="图片文件过大，上限5MB")
    try:
        img_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail="读取图片失败")
    result_store[task_id] = {
        "data": img_bytes,
        "time": time.time()
    }
    return {"status": "ok"}
@app.get("/ping")
async def ping():
    clean_expire()
    return {"status": "alive", "pending_tasks": len(task_queue), "cached_screens": len(result_store)}
# ========== 新增WebSocket实时画面通道（低延迟） ==========
# 平板AutoJS连接地址 /ws/device
@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket):
    global device_ws
    await websocket.accept()
    device_ws = websocket
    try:
        while True:
            data_text = await websocket.receive_text()
            # 平板推送画面，转发所有网页客户端
            for cli in client_ws_list:
                try:
                    await cli.send_text(data_text)
                except:
                    pass
    except WebSocketDisconnect:
        device_ws = None
# 网页前端连接地址 /ws/client
@app.websocket("/ws/client")
async def ws_client(websocket: WebSocket):
    global device_ws
    await websocket.accept()
    client_ws_list.append(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            # 网页下发点击指令，直接发给平板
            if device_ws is not None:
                try:
                    await device_ws.send_text(msg)
                except:
                    pass
    except WebSocketDisconnect:
        if websocket in client_ws_list:
            client_ws_list.remove(websocket)




