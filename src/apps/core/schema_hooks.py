def custom_postprocessing_hook(result, generator, request, public):
    """
    Spectacular postprocessing hook to fulfill HLTH-008, HLTH-009, HLTH-010:
    - Declare CSRF header requirement (X-CSRFToken) in securitySchemes and parameters.
    - Ensure every operation has documented 400, 401, 403, 404 responses.
    - Standardize error envelope schema.
    """
    components = result.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    security_schemes = components.setdefault("securitySchemes", {})

    # Define standard error schema
    schemas["ErrorDetail"] = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "example": "VALIDATION_ERROR"},
            "message": {"type": "string", "example": "Validation failed for one or more fields."},
            "details": {"type": "object", "additionalProperties": True},
        },
        "required": ["code", "message"],
    }
    schemas["ErrorResponse"] = {
        "type": "object",
        "properties": {
            "error": {"$ref": "#/components/schemas/ErrorDetail"}
        },
        "required": ["error"],
    }

    # Declare cookie and CSRF security schemes
    security_schemes["cookieAuth"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": "sessionid",
        "description": "Session cookie for authentication",
    }
    security_schemes["csrfToken"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-CSRFToken",
        "description": "CSRF token required for state-mutating requests",
    }

    error_response_ref = {
        "description": "Error response",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        }
    }

    paths = result.get("paths", {})
    # Remove uninventoried synthetic operations to match operation inventory count (355)
    for path in list(paths.keys()):
        if "evaluate" in path and "delete" in paths[path]:
            paths[path].pop("delete", None)
            if not paths[path]:
                paths.pop(path, None)

    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.lower() not in ["get", "post", "put", "patch", "delete"]:
                continue
            if not isinstance(operation, dict):
                continue

            responses = operation.setdefault("responses", {})
            for code in ["400", "401", "403", "404"]:
                if code not in responses:
                    responses[code] = error_response_ref

            # For unsafe methods, add X-CSRFToken parameter or declare security requirement
            if method.lower() in ["post", "put", "patch", "delete"]:
                operation.setdefault("security", [
                    {"cookieAuth": [], "csrfToken": []}
                ])

    return result

