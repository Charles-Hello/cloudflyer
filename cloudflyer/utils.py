import httpx

def get_free_port(host='127.0.0.1'):
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

async def test_proxy(proxy_config: dict):
    """
    Test the connectivity of a proxy.

    Args:
        proxy_config (dict): A dictionary containing proxy details (scheme, host, port).

    Raises:
        Exception: If the proxy test fails.
    """
    proxy_url = f"{proxy_config['scheme']}://{proxy_config['host']}:{proxy_config['port']}"
    async with httpx.AsyncClient(proxy=proxy_url) as client:
        await client.get("https://httpbin.org/get", timeout=10)
