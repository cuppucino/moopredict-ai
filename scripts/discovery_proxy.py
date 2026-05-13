import socket
import threading
import os

def forward(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        src.close()
        dst.close()

def start_proxy():
    # Detect local IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except:
        local_ip = '0.0.0.0'
    finally:
        s.close()

    port = 11111
    target_host = '127.0.0.1'
    target_port = 11111

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server.bind((local_ip, port))
        server.listen(10)
        print(f"🚀 Discovery Proxy started on {local_ip}:{port} -> {target_host}:{target_port}")
        
        while True:
            client, addr = server.accept()
            print(f"🔗 Discovery hit from {addr}")
            
            try:
                target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target.connect((target_host, target_port))
                
                threading.Thread(target=forward, args=(client, target)).start()
                threading.Thread(target=forward, args=(target, client)).start()
            except Exception as e:
                print(f"❌ Failed to connect to target: {e}")
                client.close()
    except Exception as e:
        print(f"❌ Proxy error: {e}")

if __name__ == "__main__":
    start_proxy()
