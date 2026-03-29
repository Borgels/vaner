# Import necessary libraries
import aiohttp
from aiohttp import web

# Define the VanerProxy class
class VanerProxy:
    def __init__(self, target_host='localhost', target_port=11434):
        self.target_host = target_host
        self.target_port = target_port
        self.app = web.Application()
        self.app.router.add_route('*', '/{tail:.*}', self.handle_request)

    async def handle_request(self, request):
        # Determine the target URL
        target_url = f'http://{self.target_host}:{self.target_port}{request.path}'
        headers = {key: value for (key, value) in request.headers.items() if key != 'Host'}

        # Create a session and make the request to the target server
        async with aiohttp.ClientSession() as session:
            if request.method == 'POST' and request.path in ['/api/chat', '/v1/chat/completions']:
                # Intercept POST requests for /api/chat and /v1/chat/completions
                data = await request.json()
                # Inject top-5 artifact context (dummy implementation)
                artifacts = self.get_top_5_artifacts()
                if artifacts:
                    data['context'] = artifacts
                async with session.post(target_url, headers=headers, json=data) as response:
                    return web.Response(text=await response.text(), status=response.status)
            else:
                # Forward other requests
                async with session.request(request.method, target_url, headers=headers, data=await request.read()) as response:
                    return web.Response(body=await response.read(), status=response.status)

    def get_top_5_artifacts(self):
        # Dummy implementation to return top-5 artifacts
        return [{'id': i, 'content': f'Artifact {i}'} for i in range(1, 6)]

# Run the proxy server
def run_proxy(host='localhost', port=11435):
    proxy = VanerProxy()
    web.run_app(proxy.app, host=host, port=port)

if __name__ == '__main__':
    run_proxy()
