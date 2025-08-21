import httpx


def get_free_port(host="127.0.0.1"):
    """
    Get an available free port on the specified IP address

    Args:
        ip (str): IP address, defaults to localhost '127.0.0.1'

    Returns:
        int: Available port number
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Set port to 0 to let the system assign a random free port
        sock.bind((host, 0))
        # Get the assigned port number
        _, port = sock.getsockname()
        return port
    finally:
        sock.close()


async def get_net_info(proxy_port: int):
    """
    Get network TLS/UA information through proxy stack.

    Args:
        proxy_port (int): The MITM proxy port.

    Returns:
        dict: Network information containing UA, IP, JA3, JA4, or None if failed.
    """
    try:
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        async with httpx.AsyncClient(proxy=proxy_url, timeout=10, verify=False) as client:
            response = await client.get("https://tls.peet.ws/api/all")
            if response.status_code == 200:
                tls_data = response.json()
                result = {}
                # Get User-Agent from http1.headers array
                if "user_agent" in tls_data:
                    result["ua"] = tls_data["user_agent"]
                # Get IP
                if "ip" in tls_data:
                    result["ip"] = tls_data["ip"]
                # Get JA3 fingerprint hash
                if "tls" in tls_data and "ja3_hash" in tls_data["tls"]:
                    result["ja3"] = tls_data["tls"]["ja3_hash"]
                # Get JA4 fingerprint
                if "tls" in tls_data and "ja4" in tls_data["tls"]:
                    result["ja4"] = tls_data["tls"]["ja4"]
                return result
    except IOError:
        return None
