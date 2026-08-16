"""Schema-driven prompt-private field enforcement.

The marker lives on the owning Pydantic field.  Consumers derive the actual
runtime strings recursively and inspect the final rendered messages; there is
no second hand-maintained deny-list to drift from the contract.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class PrivateToken:
    path: str
    value: str


class PromptPrivateLeak(ValueError):
    """Raised before request budgeting, cache lookup, ledger, or transport."""


def _leaf_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        token = value.strip()
        if token:
            yield token
        return
    if isinstance(value, BaseModel):
        for field_name in value.__class__.model_fields:
            yield from _leaf_strings(getattr(value, field_name))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _leaf_strings(item)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _leaf_strings(item)


def private_tokens(model: BaseModel, *, prefix: str = '') -> tuple[PrivateToken, ...]:
    """Return actual non-empty strings from recursively marked fields."""
    found: list[PrivateToken] = []
    for field_name, field in model.__class__.model_fields.items():
        value = getattr(model, field_name)
        path = f'{prefix}.{field_name}' if prefix else field_name
        extra = field.json_schema_extra or {}
        if bool(extra.get('prompt_private_keys')):
            if not isinstance(value, Mapping):
                raise TypeError(f'{path}: prompt_private_keys requires a mapping field')
            found.extend(
                PrivateToken(f'{path}[key]', token)
                for key in value
                if (token := str(key).strip())
            )
        if bool(extra.get('prompt_private')):
            found.extend(PrivateToken(path, token) for token in _leaf_strings(value))
            continue
        if isinstance(value, BaseModel):
            found.extend(private_tokens(value, prefix=path))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, BaseModel):
                    found.extend(private_tokens(item, prefix=f'{path}[{index}]'))
        elif isinstance(value, Mapping):
            for key, item in value.items():
                if isinstance(item, BaseModel):
                    found.extend(private_tokens(item, prefix=f'{path}[{key!r}]'))
    return tuple(found)


def assert_prompt_private_absent(
    sources: BaseModel | Iterable[BaseModel],
    *,
    system: str,
    user: str,
) -> None:
    """Fail closed if a marked runtime value occurs in either final message."""
    models = (sources,) if isinstance(sources, BaseModel) else tuple(sources)
    leaked: list[PrivateToken] = []
    for model in models:
        for token in private_tokens(model):
            if token.value in system or token.value in user:
                leaked.append(token)
    if leaked:
        paths = ', '.join(sorted({token.path for token in leaked}))
        raise PromptPrivateLeak(
            f'prompt-private contract violation in final rendered request: {paths}'
        )
