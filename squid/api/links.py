"""Route-derived relative links for API representations."""

from fastapi import Request


def relative_url_for(request: Request, route_name: str, /, **path_params: object) -> str:
    """Build a route URL including the application's mount prefix.

    The result has no host or scheme, so untrusted Host headers cannot change it.
    """
    route_path = request.app.url_path_for(route_name, **{key: str(value) for key, value in path_params.items()})
    root_path = str(request.scope.get("root_path", "")).rstrip("/")
    return f"{root_path}{route_path}"
