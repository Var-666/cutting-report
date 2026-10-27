import re

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope


class FrontendAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if re.search(r'-[A-Za-z0-9_-]{8,}\.(?:js|css)$', path):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response
