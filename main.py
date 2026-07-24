from fastapi import FastAPI, UploadFile, File, Query, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uuid
import time
import os
import json
import base64
from collections import OrderedDict
from pydantic import BaseModel

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
REMOTE_CTRL_PWD = os.getenv("REMOTE_CTRL_PWD")
if not MAIN_TOKEN:
    raise RuntimeError("环境变量 MAIN_TOKEN 未配置！请到平台后台添加")
if not REMOTE_CTRL_PWD:
    raise RuntimeError("环境变量 REMOTE_CTRL_PWD 未配置！远程操控密码缺失")
TASK_EXPIRE_SEC = 30
MAX_SCREEN_CACHE = 15
SCREEN_EXPIRE_SEC = 60
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB

# ========== 内存存储 ==========
task_queue = OrderedDict()
result_store = {}
device_ws = None
client_ws_list = []

# ========== 工具函数 ==========
def check_token(token: str):
    if token != MAIN_TOKEN:
        raise HTTPException(status_code=403, detail="访问密钥错误")

def clean_expire():
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

# ========== 请求体模型 ==========
class ImageBase64Body(BaseModel):
    image: str
    task_id: str  # ✅ task_id放入请求JSON体内

# ========== HTTP接口 ==========
@app.get("/api/remote_auth", summary="远程操控密码验证接口")
async def remote_auth(token: str = Query(), pwd: str = Query()):
    try:
        check_token(token)
        if pwd == REMOTE_CTRL_PWD:
            return {"success": True, "msg": "验证通过"}
        else:
            return {"success": False, "msg": "密码错误，请重新输入"}
    except Exception as e:
        return {"success": False, "msg": str(e)}

@app.get("/task/screenshot", summary="网站下发截屏任务")
async def create_screenshot_task(token: str = Query()):
    try:
        check_token(token)
        clean_expire()
        tid = str(uuid.uuid4())
        task_queue[tid] = {"type": "screenshot", "time": time.time()}
        return {"task_id": tid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/task/click", summary="网站下发点击任务")
async def create_click_task(x: int, y: int, token: str = Query()):
    try:
        check_token(token)
        clean_expire()
        tid = str(uuid.uuid4())
        task_queue[tid] = {"type": "click", "x": x, "y": y, "time": time.time()}
        return {"task_id": tid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/result/{task_id}", summary="前端获取截图图片")
async def get_image(task_id: str, token: str = Query()):
    try:
        check_token(token)
        clean_expire()
        if task_id not in result_store:
            print(f"[GET /result] 图片不存在: {task_id}")
            raise HTTPException(status_code=404, detail="暂无图片")
        print(f"[GET /result] 返回图片: {task_id}, 大小: {len(result_store[task_id]['data'])} 字节")
        return Response(content=result_store[task_id]["data"], media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/client/poll", summary="APP轮询获取任务")
async def poll_task(token: str = Query()):
    try:
        check_token(token)
        clean_expire()
        if task_queue:
            tid = next(iter(task_queue.keys()))
            data = task_queue.pop(tid)
            return {"task_id": tid, "data": data}
        return {"task_id": "null"}
    except Exception as e:
        # 异常返回合法JSON，防止AutoJS解析崩溃
        return {"task_id": "null", "error": str(e)}

@app.post("/client/upload_result", summary="APP上传截图结果（文件上传）")
async def upload_result(task_id: str, token: str = Query(), file: UploadFile = File()):
    try:
        check_token(token)
        print(f"[POST /upload_result] 收到上传请求, task_id={task_id}, 文件名={file.filename}")
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="图片文件过大，上限5MB")
        img_bytes = await file.read()
        result_store[task_id] = {
            "data": img_bytes,
            "time": time.time()
        }
        print(f"  ✅ 已存入缓存")
        return {"status": "ok"}
    except Exception as e:
        print(f"上传异常: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ========== base64上传接口（适配AutoJS6） ==========
@app.post("/client/upload_base64", summary="APP上传截图结果（base64）")
async def upload_base64(token: str = Query(), body: ImageBase64Body = None):
    try:
        check_token(token)
        tid = body.task_id
        print(f"[POST /upload_base64] task_id={tid}")
        img_bytes = base64.b64decode(body.image)
        img_size = len(img_bytes)
        print(f"  ✅ base64解码成功, 大小: {img_size} 字节")
        if img_size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="图片文件过大，上限5MB")
        result_store[tid] = {
            "data": img_bytes,
            "time": time.time()
        }
        print(f"  ✅ 缓存写入完成")
        return {"status": "ok"}
    except Exception as e:
        print(f"base64上传异常：{e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/ping")
async def ping():
    clean_expire()
    return {
        "status": "alive",
        "pending_tasks": len(task_queue),
        "cached_screens": len(result_store)
    }

# ========== WebSocket 链路 ==========
@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket):
    global device_ws
    await websocket.accept()
    device_ws = websocket
    print("✅ 设备端 WebSocket 连接成功")
    try:
        while True:
            data_text = await websocket.receive_text()
            forward_count = 0
            for cli in client_ws_list:
                try:
                    await cli.send_text(data_text)
                    forward_count += 1
                except Exception:
                    pass
            print(f"  📤 已转发给 {forward_count} 个网页客户端")
    except WebSocketDisconnect:
        print("❌ 设备端 WebSocket 断开")
        device_ws = None
    except Exception as e:
        print(f"设备ws异常: {e}")
        device_ws = None

@app.websocket("/ws/client")
async def ws_client(websocket: WebSocket):
    global device_ws
    await websocket.accept()
    client_ws_list.append(websocket)
    print(f"✅ 网页端WebSocket连接，在线:{len(client_ws_list)}")
    try:
        while True:
            msg = await websocket.receive_text()
            if device_ws is not None:
                await device_ws.send_text(msg)
    except WebSocketDisconnect:
        if websocket in client_ws_list:
            client_ws_list.remove(websocket)
    except Exception as e:
        print(f"网页ws异常:{e}")
        if websocket in client_ws_list:
            client_ws_list.remove(websocket)



