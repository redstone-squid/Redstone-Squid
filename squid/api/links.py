"""Route-derived relative links for API representations."""

from fastapi import Request


def relative_url_for(request: Request, route_name: str, /, **path_params: object) -> str:
    """Build a mounted-root-aware relative URL without consulting request host headers."""
    route_path = request.app.url_path_for(route_name, **{key: str(value) for key, value in path_params.items()})
    root_path = str(request.scope.get("root_path", "")).rstrip("/")
    return f"{root_path}{route_path}"
