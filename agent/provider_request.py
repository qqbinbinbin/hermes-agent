"""Request middleware at the physical provider callback, including auxiliary calls."""
from collections.abc import Mapping


def prepare_provider_request(
    request: dict[str, object], metadata: Mapping[str, object] | None,
    *, provider: str, model: str,
) -> dict[str, object]:
    """Reuse the plugin chain without changing model routing or retry semantics.

    The caller enters its captured turn/profile context before reaching this seam.
    Plugins must validate opaque turn metadata themselves; it grants no authority.
    """
    from hermes_cli.middleware import _safe_copy, middleware_payload
    from hermes_cli.config import get_config_path, load_config_readonly
    from hermes_cli.plugins import _delivery_manager
    from utils import fast_safe_load

    manager = _delivery_manager()
    try:
        config_path = get_config_path()
        if config_path.exists():
            with config_path.open(encoding="utf-8-sig") as source:
                raw = fast_safe_load(source)
            if raw is not None and not isinstance(raw, dict):
                raise ValueError
        config = load_config_readonly().get("plugins", {})
        required = config.get("required_provider_request", [])
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise ValueError
        callbacks = tuple(manager._middleware.get("provider_request", ()))
        for key in required:
            loaded = manager._plugins.get(key)
            registered = any(reg.active and reg.kind == "middleware" and
                reg.key == "provider_request" for reg in manager._ownership_ledger.get(key, ()))
            if loaded is None or not loaded.enabled or loaded.error or not registered or not callbacks:
                raise ValueError
    except (Exception, SystemExit):
        raise RuntimeError("provider_request_rejected") from None
    if not callbacks:
        return request
    context = {key: value for key in (
        "api_mode", "api_request_id", "call_role", "retry_count", "auxiliary_task",
    ) if isinstance(value := (metadata or {}).get(key), (str, int))}
    original = _safe_copy(request)
    final = original
    for callback in callbacks:
        try:
            result = callback(**middleware_payload(request=_safe_copy(final),
                original_request=_safe_copy(original), provider=provider, model=model, **context))
            if (not isinstance(result, Mapping) or result.get("action") == "reject"
                    or not isinstance(result.get("request"), dict)):
                raise ValueError
            final = _safe_copy(result["request"])
        except (Exception, SystemExit):
            raise RuntimeError("provider_request_rejected") from None
    return final
