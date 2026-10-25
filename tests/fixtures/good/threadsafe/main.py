import threading
lock = threading.Lock()
with lock:
    value = 1
