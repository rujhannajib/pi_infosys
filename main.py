from flask import Flask, jsonify, render_template, request, Response
import psutil
import subprocess
import time
import threading
import re
import cv2
import numpy as np
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os

PORT = 4000
app = Flask(__name__)

load_dotenv()

# for authentication
auth = HTTPBasicAuth()

users = {
    "admin": generate_password_hash(os.getenv('INFO_PASSWORD'))
}

# Initialize CPU percent
psutil.cpu_percent(interval=None)

ESP32_STREAM_URL = os.getenv("STREAM_URL")

latest_frame = None
frame_lock = threading.Lock()

speed_cache = {
    "timestamp": 0,
    "download": None,
    "upload": None,
    "ping": None,
    "running": False
}

def get_cpu_temp():
    try:
        temp = subprocess.check_output(
            ["vcgencmd", "measure_temp"]
        ).decode()
        return temp.replace("temp=", "").strip()
    except:
        return "N/A"
    
def speedtest_scheduler():
    while True:
        run_speedtest()
        time.sleep(1800)  # 30 minutes
    
def run_speedtest():
    global speed_cache

    if speed_cache["running"]:
        return

    speed_cache["running"] = True

    try:
        output = subprocess.check_output(
            ["speedtest", "--simple"],
            stderr=subprocess.STDOUT
        ).decode()

        speed_cache["ping"] = float(
            re.search(r"Ping:\s+([\d.]+)", output).group(1)
        )
        speed_cache["download"] = float(
            re.search(r"Download:\s+([\d.]+)", output).group(1)
        )
        speed_cache["upload"] = float(
            re.search(r"Upload:\s+([\d.]+)", output).group(1)
        )
        speed_cache["timestamp"] = int(time.time())

    except Exception as e:
        speed_cache["error"] = str(e)

    speed_cache["running"] = False

@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username

@app.route("/api/stats")
@auth.login_required
def stats():
    return jsonify({
        "cpu_temp": get_cpu_temp(),
        "cpu_usage": psutil.cpu_percent(),
        "memory": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "uptime": int(time.time() - psutil.boot_time())
    })
    
@app.route("/api/internet")
@auth.login_required
def internet_speed():
    return {
        "download": speed_cache["download"],
        "upload": speed_cache["upload"],
        "ping": speed_cache["ping"],
        "last_run": speed_cache["timestamp"],
        "running": speed_cache["running"]
    }
    
def camera_capture():
    global latest_frame
    retry_delay = 2

    while True:
        print("📷 Connecting to ESP32 camera...")
        cap = cv2.VideoCapture(ESP32_STREAM_URL, cv2.CAP_FFMPEG)

        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

        if not cap.isOpened():
            print("❌ Camera not reachable, retrying...")
            time.sleep(retry_delay)
            continue

        print("✅ Camera connected")

        while True:
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Camera stream lost")
                cap.release()
                break

            with frame_lock:
                latest_frame = frame.copy()

        time.sleep(retry_delay)


            
def generate_camera_stream():
    while True:
        with frame_lock:
            frame = latest_frame

        if frame is None:
            placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(
                placeholder,
                "Waiting for camera...",
                (30, 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2
            )
            ret, buffer = cv2.imencode(".jpg", placeholder)
            frame_bytes = buffer.tobytes()
        else:
            ret, buffer = cv2.imencode(".jpg", frame)
            frame_bytes = buffer.tobytes()

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" +
               frame_bytes + b"\r\n")

        time.sleep(0.1)

        
@app.route("/video")
def video_feed():
    return Response(
        generate_camera_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
    

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    threading.Thread(target=speedtest_scheduler, daemon=True).start()
    # threading.Thread(target=camera_capture, daemon=True).start()
    # 4000 for testing, 5000 for server
    app.run(host="0.0.0.0", port=PORT, threaded=True)
