"""Minimal OData v4 query support for the mock RESO Web API.

Implements exactly the subset the platform contracts on:
``$filter`` (eq, gt, lt, and, or, parentheses), ``$select``, ``$top``,
``$skip``, ``$orderby``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Record = Mapping[str, Any]
Predicate = Callable[[Record], bool]

_TOKEN_RE = re.compile(r"\(|\)|'(?:[^']|'')*'|-?\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*")


class ODataError(ValueError):
    """Raised when a query option cannot be parsed."""


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ODataError(f"unexpected character {text[pos]!r} in $filter")
        tokens.append(match.group(0))
        pos = match.end()
    return tokens


def _parse_literal(token: str) -> str | int | float:
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError as exc:
        raise ODataError(f"invalid literal {token!r} in $filter") from exc


def _comparison(field: str, op: str, literal: str | int | float) -> Predicate:
    def predicate(record: Record) -> bool:
        value = record.get(field)
        if isinstance(literal, str):
            if not isinstance(value, str):
                return False
            if op == "eq":
                return value == literal
            if op == "gt":
                return value > literal
            return value < literal
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        val, lit = float(value), float(literal)
        if op == "eq":
            return val == lit
        if op == "gt":
            return val > lit
        return val < lit

    return predicate


class _FilterParser:
    """Recursive descent over: expr := and-term ('or' and-term)*."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> Predicate:
        predicate = self._parse_or()
        if self._pos != len(self._tokens):
            raise ODataError(f"unexpected token {self._tokens[self._pos]!r} in $filter")
        return predicate

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        token = self._peek()
        if token is None:
            raise ODataError("unexpected end of $filter")
        self._pos += 1
        return token

    def _parse_or(self) -> Predicate:
        left = self._parse_and()
        while self._peek() == "or":
            self._next()
            right = self._parse_and()
            left = self._combine(left, right, any)
        return left

    def _parse_and(self) -> Predicate:
        left = self._parse_factor()
        while self._peek() == "and":
            self._next()
            right = self._parse_factor()
            left = self._combine(left, right, all)
        return left

    def _parse_factor(self) -> Predicate:
        if self._peek() == "(":
            self._next()
            predicate = self._parse_or()
            if self._next() != ")":
                raise ODataError("expected ')' in $filter")
            return predicate
        field = self._next()
        op = self._next()
        if op not in ("eq", "gt", "lt"):
            raise ODataError(f"unsupported operator {op!r} in $filter")
        return _comparison(field, op, _parse_literal(self._next()))

    @staticmethod
    def _combine(
        left: Predicate, right: Predicate, mode: Callable[[list[bool]], bool]
    ) -> Predicate:
        return lambda record: mode([left(record), right(record)])


def parse_filter(expression: str) -> Predicate:
    return _FilterParser(_tokenize(expression)).parse()


def _parse_non_negative_int(params: Mapping[str, str], name: str) -> int | None:
    raw = params.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ODataError(f"{name} must be an integer") from exc
    if value < 0:
        raise ODataError(f"{name} must be non-negative")
    return value


def _apply_orderby(records: list[Record], orderby: str) -> list[Record]:
    parts = orderby.split()
    if not parts or len(parts) > 2 or (len(parts) == 2 and parts[1] not in ("asc", "desc")):
        raise ODataError(f"invalid $orderby {orderby!r}")
    field = parts[0]
    reverse = len(parts) == 2 and parts[1] == "desc"
    return sorted(
        records,
        key=lambda r: (r.get(field) is None, r.get(field)),
        reverse=reverse,
    )


def apply_query(records: Sequence[Record], params: Mapping[str, str]) -> list[dict[str, Any]]:
    """Apply the supported OData query options to a record set, in spec order."""
    results: list[Record] = list(records)

    filter_expr = params.get("$filter")
    if filter_expr:
        predicate = parse_filter(filter_expr)
        results = [r for r in results if predicate(r)]

    orderby = params.get("$orderby")
    if orderby:
        results = _apply_orderby(results, orderby)

    skip = _parse_non_negative_int(params, "$skip")
    if skip:
        results = results[skip:]
    top = _parse_non_negative_int(params, "$top")
    if top is not None:
        results = results[:top]

    output = [dict(r) for r in results]
    select = params.get("$select")
    if select:
        fields = [f.strip() for f in select.split(",") if f.strip()]
        output = [{f: r.get(f) for f in fields} for r in output]
    return output
