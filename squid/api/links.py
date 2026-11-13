"""Route-derived relative links for API representations."""

from litestar import Request


def relative_url_for(request: Request, route_name: str, /, **path_params: object) -> str:
    """Build a route URL including the application's mount prefix.

    The result has no host or scheme, so untrusted Host headers cannot change it.
    """
    route_path = request.app.route_reverse(route_name, **path_params)
    root_path = str(request.scope.get("root_path", "")).rstrip("/")
    return f"{root_path}{route_path}"
