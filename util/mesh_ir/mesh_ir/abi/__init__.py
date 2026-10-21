from importlib import import_module


_EXPORTS = {
    "decode_program": "decoder",
    "encode_program": "encoder",
    "load_program_json": "json_codec",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
