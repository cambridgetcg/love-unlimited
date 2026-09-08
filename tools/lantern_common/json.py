"""有界 canonical JSON；產品自行決定 schema，呢度只驗 plain data。"""
from __future__ import annotations

import json
import math

from . import CommonError

DEFAULT_MAX_BYTES = 256 * 1024


def validate_plain(value, *, depth=0, max_string_bytes=DEFAULT_MAX_BYTES,
                   max_list_items=20000, max_dict_items=20000,
                   max_nodes=200000, error=CommonError):
    """限制深度、欄位大小同總節點；共享 reference 唔會造成無界展開。"""
    remaining = max_nodes

    def visit(item, level):
        nonlocal remaining
        remaining -= 1
        if level > 12 or remaining < 0:
            raise error("invalid_input")
        if item is None or type(item) in (bool, int):
            if type(item) is int and abs(item) > 10**30:
                raise error("invalid_input")
        elif type(item) is float:
            if not math.isfinite(item):
                raise error("invalid_input")
        elif isinstance(item, str):
            try:
                if len(item.encode("utf-8")) > max_string_bytes:
                    raise error("invalid_input")
            except UnicodeError:
                raise error("invalid_input") from None
        elif type(item) is list:
            if len(item) > max_list_items:
                raise error("invalid_input")
            for child in item:
                visit(child, level + 1)
        elif type(item) is dict:
            if len(item) > max_dict_items:
                raise error("invalid_input")
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 4096:
                    raise error("invalid_input")
                visit(key, level + 1)
                visit(child, level + 1)
        else:
            raise error("invalid_input")

    visit(value, depth)


def encode_json(value, *, maximum=DEFAULT_MAX_BYTES, error=CommonError,
                too_large="cache_too_large", validator=None) -> bytes:
    """排序 keys、UTF-8、無 NaN；大小上限包括最後 newline。"""
    if validator is None:
        validate_plain(value, max_string_bytes=maximum, error=error)
    else:
        validator(value)
    try:
        encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":"))
        chunks, size = [], 1
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size > maximum:
                raise error(too_large)
            chunks.append(encoded)
        return b"".join(chunks) + b"\n"
    except error:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise error("invalid_input") from None


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def decode_json(data, *, maximum=DEFAULT_MAX_BYTES, error=CommonError,
                validator=None, pairs=unique_pairs):
    """拒絕 duplicate keys、nonfinite、過深或過大輸入；唔修壞 state。"""
    try:
        size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)
        if size > maximum:
            raise error("state_too_large")
        value = json.loads(data, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if validator is None:
            validate_plain(value, max_string_bytes=maximum, error=error)
        else:
            validator(value)
        return value
    except error as exc:
        if str(exc) == "state_too_large":
            raise
        raise error("state_corrupt") from None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise error("state_corrupt") from None
