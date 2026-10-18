"""Arithmetic over GF(2).

Polynomials are Python ints where bit ``i`` is the coefficient of ``x**i``.
Linear systems use one int per equation: bits ``0..n-1`` hold the coefficients
and bit ``n`` holds the right-hand side.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Sequence

_SPREAD = tuple(sum(((byte >> bit) & 1) << (2 * bit) for bit in range(8)) for byte in range(256))


def degree(poly: int) -> int:
    """Degree of ``poly``; the zero polynomial has degree -1."""
    return poly.bit_length() - 1


def mul(a: int, b: int) -> int:
    """Carry-less product of two polynomials."""
    if a.bit_length() < b.bit_length():
        a, b = b, a
    result = 0
    while b:
        if b & 1:
            result ^= a
        a <<= 1
        b >>= 1
    return result


def square(a: int) -> int:
    """Square of a polynomial (spreads the bits apart, no cross terms in GF(2))."""
    result = 0
    shift = 0
    while a:
        result |= _SPREAD[a & 0xFF] << shift
        a >>= 8
        shift += 16
    return result


def divmod_poly(a: int, b: int) -> tuple[int, int]:
    if b == 0:
        raise ZeroDivisionError("polynomial division by zero")
    divisor_length = b.bit_length()
    quotient = 0
    while a.bit_length() >= divisor_length:
        shift = a.bit_length() - divisor_length
        quotient |= 1 << shift
        a ^= b << shift
    return quotient, a


def mod(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("polynomial division by zero")
    divisor_length = b.bit_length()
    while a.bit_length() >= divisor_length:
        a ^= b << (a.bit_length() - divisor_length)
    return a


def div(a: int, b: int) -> int:
    return divmod_poly(a, b)[0]


def mulmod(a: int, b: int, modulus: int) -> int:
    return mod(mul(a, b), modulus)


def gcd(a: int, b: int) -> int:
    while b:
        a, b = b, mod(a, b)
    return a


def powmod_x(exponent: int, modulus: int) -> int:
    """``x**exponent mod modulus``."""
    if exponent < 0:
        raise ValueError("exponent must be non-negative")
    result = mod(1, modulus)
    for bit in bin(exponent)[2:]:
        result = mod(square(result), modulus)
        if bit == "1":
            result = mod(result << 1, modulus)
    return result


def derivative(poly: int) -> int:
    # d/dx x^n = n*x^(n-1); only odd n survive in characteristic 2.
    odd_terms = 0
    bit = 1
    while bit <= poly:
        odd_terms |= poly & (bit << 1)
        bit <<= 2
    return odd_terms >> 1


def sqrt_poly(poly: int) -> int:
    """Square root of a polynomial that only has even-degree terms."""
    result = 0
    index = 0
    while poly:
        if poly & 1:
            result |= 1 << index
        poly >>= 2
        index += 1
    return result


def _squarefree(poly: int) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    if degree(poly) < 1:
        return result
    poly_derivative = derivative(poly)
    if poly_derivative == 0:
        return [(factor, multiplicity * 2) for factor, multiplicity in _squarefree(sqrt_poly(poly))]
    c = gcd(poly, poly_derivative)
    w = div(poly, c)
    multiplicity = 1
    while w != 1:
        y = gcd(w, c)
        factor = div(w, y)
        if factor != 1:
            result.append((factor, multiplicity))
        w = y
        c = div(c, y)
        multiplicity += 1
    if c != 1:
        result.extend((factor, m * 2) for factor, m in _squarefree(sqrt_poly(c)))
    return result


def _distinct_degree(poly: int, max_degree: int) -> list[tuple[int, int]]:
    """Group the irreducible factors of a square-free ``poly`` by degree (up to ``max_degree``)."""
    result: list[tuple[int, int]] = []
    remaining = poly
    h = mod(2, remaining) if degree(remaining) > 0 else 0
    d = 1
    while d <= max_degree and degree(remaining) >= 2 * d:
        h = mod(square(h), remaining)
        g = gcd(remaining, h ^ 2)
        if g != 1:
            result.append((g, d))
            remaining = div(remaining, g)
            h = mod(h, remaining)
        d += 1
    if degree(remaining) >= 1 and degree(remaining) <= max_degree:
        result.append((remaining, degree(remaining)))
    return result


def _equal_degree(poly: int, factor_degree: int, rng: random.Random) -> list[int]:
    """Split a product of distinct irreducibles of equal degree (Cantor-Zassenhaus, char 2)."""
    total_degree = degree(poly)
    if total_degree == factor_degree:
        return [poly]
    factors = [poly]
    target_count = total_degree // factor_degree
    while len(factors) < target_count:
        a = rng.getrandbits(total_degree) | 1
        trace = mod(a, poly)
        term = trace
        for _ in range(factor_degree - 1):
            term = mod(square(term), poly)
            trace ^= term
        next_factors: list[int] = []
        for factor in factors:
            if degree(factor) == factor_degree:
                next_factors.append(factor)
                continue
            g = gcd(factor, mod(trace, factor))
            if 0 < degree(g) < degree(factor):
                next_factors.extend((g, div(factor, g)))
            else:
                next_factors.append(factor)
        factors = next_factors
    return factors


def small_factors(poly: int, max_degree: int, seed: int = 0x5EED) -> list[tuple[int, int]]:
    """Irreducible factors of ``poly`` with degree <= ``max_degree``, as (factor, multiplicity)."""
    rng = random.Random(seed)
    found: dict[int, int] = {}
    for squarefree_part, multiplicity in _squarefree(poly):
        for group, group_degree in _distinct_degree(squarefree_part, max_degree):
            for factor in _equal_degree(group, group_degree, rng):
                found[factor] = found.get(factor, 0) + multiplicity
    return sorted(found.items())


def divisors_of_degree(
    factors: Sequence[tuple[int, int]],
    target_degree: int,
    limit: int,
) -> Iterator[int]:
    """Products of the given irreducible factors whose degree is exactly ``target_degree``."""
    expanded = [(factor, degree(factor), multiplicity) for factor, multiplicity in factors]
    produced = 0

    def walk(index: int, product: int, product_degree: int) -> Iterator[int]:
        nonlocal produced
        if produced >= limit:
            return
        if product_degree == target_degree:
            produced += 1
            yield product
            return
        if index >= len(expanded):
            return
        factor, factor_degree, multiplicity = expanded[index]
        power = product
        for count in range(multiplicity + 1):
            if product_degree + count * factor_degree > target_degree:
                break
            yield from walk(index + 1, power, product_degree + count * factor_degree)
            power = mul(power, factor)

    yield from walk(0, 1, 0)


def solve_linear(rows: Sequence[int], variable_count: int) -> tuple[int, list[int]] | None:
    """Solve a GF(2) linear system.

    Returns ``(particular_solution, nullspace_basis)`` or ``None`` when the system is
    inconsistent. Free variables are zero in the particular solution.
    """
    rhs_bit = 1 << variable_count
    coefficient_mask = rhs_bit - 1
    pivots: dict[int, int] = {}
    for row in rows:
        for column, pivot_row in pivots.items():
            if (row >> column) & 1:
                row ^= pivot_row
        coefficients = row & coefficient_mask
        if coefficients == 0:
            if row & rhs_bit:
                return None
            continue
        column = coefficients.bit_length() - 1
        for other_column, pivot_row in pivots.items():
            if (pivot_row >> column) & 1:
                pivots[other_column] = pivot_row ^ row
        pivots[column] = row

    solution = 0
    for column, pivot_row in pivots.items():
        if pivot_row & rhs_bit:
            solution |= 1 << column

    basis: list[int] = []
    for free_column in range(variable_count):
        if free_column in pivots:
            continue
        vector = 1 << free_column
        for column, pivot_row in pivots.items():
            if (pivot_row >> free_column) & 1:
                vector |= 1 << column
        basis.append(vector)
    return solution, basis


def span(particular: int, basis: Sequence[int], limit: int) -> Iterator[int]:
    """Enumerate ``particular`` plus every combination of ``basis`` vectors (up to ``limit``)."""
    for index in range(min(1 << len(basis), limit)):
        vector = particular
        bit = 0
        while index:
            if index & 1:
                vector ^= basis[bit]
            index >>= 1
            bit += 1
        yield vector
