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
REMOTE_CTRL_PWD = os.getenv("REMOTE_CTRL_PWD")
if not MAIN_TOKEN:
    raise RuntimeError("环境变量 MAIN_TOKEN 未配置！请到平台后台添加")
if not REMOTE_CTRL_PWD:
    raise RuntimeError("环境变量 REMOTE_CTRL_PWD 未配置！远程操控密码缺失")
TASK_EXPIRE_SEC = 30
MAX_SCREEN_CACHE = 15
SCREEN_EXPIRE_SEC = 60
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 修复：5MB（之前写错成5KB了）

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

# ========== HTTP接口 ==========
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
        print(f"[GET /result] 图片不存在: {task_id}")
        raise HTTPException(status_code=404, detail="暂无图片")
    print(f"[GET /result] 返回图片: {task_id}, 大小: {len(result_store[task_id]['data'])} 字节")
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
    print(f"[POST /upload_result] 收到上传请求, task_id={task_id}, 文件名={file.filename}, 大小={file.size}")
    if file.size and file.size > MAX_UPLOAD_SIZE:
        print(f"  ❌ 图片过大: {file.size} > {MAX_UPLOAD_SIZE}")
        raise HTTPException(status_code=413, detail="图片文件过大，上限5MB")
    try:
        img_bytes = await file.read()
        print(f"  ✅ 读取成功, 大小: {len(img_bytes)} 字节")
    except Exception as e:
        print(f"  ❌ 读取图片失败: {e}")
        raise HTTPException(status_code=400, detail="读取图片失败")
    result_store[task_id] = {
        "data": img_bytes,
        "time": time.time()
    }
    print(f"  ✅ 已存入缓存, 当前缓存数量: {len(result_store)}")
    return {"status": "ok"}

@app.get("/ping")
async def ping():
    clean_expire()
    return {
        "status": "alive",
        "pending_tasks": len(task_queue),
        "cached_screens": len(result_store),
        "cache_keys": list(result_store.keys())
    }

# ========== WebSocket（保留兼容，不用管） ==========
@app.websocket("/ws/device")
async def ws_device(websocket: WebSocket):
    global device_ws
    await websocket.accept()
    device_ws = websocket
    print("=" * 50)
    print("✅ 设备端 WebSocket 连接成功")
    print("=" * 50)
    try:
        while True:
            data_text = await websocket.receive_text()
            print(f"[设备 → 服务器] 收到消息，长度: {len(data_text)} 字符")
            forward_count = 0
            for cli in client_ws_list:
                try:
                    await cli.send_text(data_text)
                    forward_count += 1
                except Exception as e:
                    print(f"  ⚠️ 转发失败: {e}")
            print(f"  📤 已转发给 {forward_count} 个网页客户端")
    except WebSocketDisconnect:
        print("❌ 设备端 WebSocket 连接断开")
        device_ws = None
    except Exception as e:
        print(f"❌ 设备端异常: {str(e)}")
        device_ws = None

@app.websocket("/ws/client")
async def ws_client(websocket: WebSocket):
    global device_ws
    await websocket.accept()
    client_ws_list.append(websocket)
    print("=" * 50)
    print(f"✅ 网页端 WebSocket 连接成功，当前在线: {len(client_ws_list)} 个")
    print("=" * 50)
    try:
        while True:
            msg = await websocket.receive_text()
            print(f"[网页 → 服务器] 收到指令: {msg[:200]}")
            if device_ws is not None:
                try:
                    await device_ws.send_text(msg)
                    print(f"  📤 指令已转发给设备端")
                except Exception as e:
                    print(f"  ⚠️ 转发给设备失败: {e}")
            else:
                print(f"  ⚠️ 设备端未连接，指令丢弃")
    except WebSocketDisconnect:
        if websocket in client_ws_list:
            client_ws_list.remove(websocket)
        print(f"❌ 网页端断开，剩余在线: {len(client_ws_list)} 个")
    except Exception as e:
        print(f"❌ 网页端异常: {str(e)}")
        if websocket in client_ws_list:
            client_ws_list.remove(websocket)
