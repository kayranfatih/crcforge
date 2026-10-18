from __future__ import annotations

import random
import unittest

import _support  # noqa: F401
from crcforge import gf2


def is_irreducible(poly: int) -> bool:
    """Trial division by every polynomial of degree <= deg/2 (fine for small degrees)."""
    degree = gf2.degree(poly)
    if degree <= 0:
        return False
    for divisor in range(2, 1 << (degree // 2 + 1)):
        if gf2.mod(poly, divisor) == 0 and gf2.degree(divisor) < degree:
            return False
    return True


class ArithmeticTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = random.Random(42)

    def test_division_identity(self) -> None:
        for _ in range(300):
            a = self.rng.getrandbits(self.rng.randrange(1, 200))
            b = self.rng.getrandbits(self.rng.randrange(1, 80)) or 1
            quotient, remainder = gf2.divmod_poly(a, b)
            self.assertEqual(gf2.mul(quotient, b) ^ remainder, a)
            self.assertLess(gf2.degree(remainder), gf2.degree(b))
            self.assertEqual(gf2.mod(a, b), remainder)
            self.assertEqual(gf2.div(a, b), quotient)

    def test_square_and_multiplication(self) -> None:
        for _ in range(200):
            a = self.rng.getrandbits(150)
            b = self.rng.getrandbits(90)
            self.assertEqual(gf2.square(a), gf2.mul(a, a))
            self.assertEqual(gf2.mul(a, b), gf2.mul(b, a))

    def test_gcd_divides_both(self) -> None:
        for _ in range(200):
            common = self.rng.getrandbits(20) | 1
            a = gf2.mul(common, self.rng.getrandbits(60) | 1)
            b = gf2.mul(common, self.rng.getrandbits(60) | 1)
            g = gf2.gcd(a, b)
            self.assertEqual(gf2.mod(a, g), 0)
            self.assertEqual(gf2.mod(b, g), 0)
            self.assertEqual(gf2.mod(g, gf2.gcd(common, g)), 0)

    def test_powmod_x_matches_repeated_multiplication(self) -> None:
        modulus = 0x11021
        value = 1
        for exponent in range(200):
            self.assertEqual(gf2.powmod_x(exponent, modulus), value)
            value = gf2.mod(value << 1, modulus)
        with self.assertRaises(ValueError):
            gf2.powmod_x(-1, modulus)

    def test_derivative_and_square_root(self) -> None:
        self.assertEqual(gf2.derivative(0b1011), 0b0001 | 0b0100)  # x^3+x+1 -> 3x^2+1 = x^2+1
        for _ in range(100):
            a = self.rng.getrandbits(80)
            self.assertEqual(gf2.sqrt_poly(gf2.square(a)), a)
            self.assertEqual(gf2.derivative(gf2.square(a)), 0)

    def test_division_by_zero(self) -> None:
        with self.assertRaises(ZeroDivisionError):
            gf2.mod(5, 0)
        with self.assertRaises(ZeroDivisionError):
            gf2.divmod_poly(5, 0)


class FactorizationTest(unittest.TestCase):
    def test_small_factors_of_products_are_irreducible_and_reconstruct(self) -> None:
        rng = random.Random(7)
        irreducibles = [p for p in range(2, 1 << 9) if is_irreducible(p)]
        for _ in range(60):
            chosen = [rng.choice(irreducibles) for _ in range(rng.randrange(1, 7))]
            product = 1
            for factor in chosen:
                product = gf2.mul(product, factor)
            factors = gf2.small_factors(product, 8)
            rebuilt = 1
            for factor, multiplicity in factors:
                self.assertTrue(is_irreducible(factor))
                for _ in range(multiplicity):
                    rebuilt = gf2.mul(rebuilt, factor)
            self.assertEqual(rebuilt, product)

    def test_large_factors_are_left_out(self) -> None:
        big = next(p for p in range((1 << 20) + 1, 1 << 21, 2) if gf2.degree(p) == 20 and is_irreducible_fast(p))
        product = gf2.mul(big, 0b111)  # x^2+x+1
        self.assertEqual(gf2.small_factors(product, 8), [(0b111, 1)])

    def test_divisors_of_degree(self) -> None:
        factors = [(0b11, 2), (0b111, 1), (0b1011, 1)]  # (x+1)^2 (x^2+x+1) (x^3+x+1)
        divisors = set(gf2.divisors_of_degree(factors, 3, limit=100))
        # x^3+x+1, or (x+1)(x^2+x+1) = x^3+1; (x+1)^3 is impossible with multiplicity 2.
        self.assertEqual(divisors, {0b1011, gf2.mul(0b11, 0b111)})
        self.assertEqual(len(list(gf2.divisors_of_degree(factors, 3, limit=1))), 1)


def is_irreducible_fast(poly: int) -> bool:
    """Rabin-style check: x^(2^n) == x mod p and gcd(x^(2^(n/q)) - x, p) == 1 for prime q | n."""
    n = gf2.degree(poly)

    def frob(k: int) -> int:
        value = 2
        for _ in range(k):
            value = gf2.mod(gf2.square(value), poly)
        return value

    if frob(n) != gf2.mod(2, poly):
        return False
    return all(n % q or gf2.gcd(poly, frob(n // q) ^ 2) == 1 for q in (2, 5))


class LinearSystemTest(unittest.TestCase):
    def test_random_consistent_systems(self) -> None:
        rng = random.Random(11)
        for _ in range(200):
            variables = rng.randrange(1, 24)
            secret = rng.getrandbits(variables)
            rows = []
            for _ in range(rng.randrange(1, 40)):
                coefficients = rng.getrandbits(variables)
                rhs = bin(coefficients & secret).count("1") & 1
                rows.append(coefficients | (rhs << variables))
            solved = gf2.solve_linear(rows, variables)
            assert solved is not None
            particular, basis = solved
            for vector in gf2.span(particular, basis, 64):
                for row in rows:
                    coefficients = row & ((1 << variables) - 1)
                    self.assertEqual(bin(coefficients & vector).count("1") & 1, row >> variables)
            if len(basis) <= 12:
                self.assertIn(secret, set(gf2.span(particular, basis, 1 << len(basis))))

    def test_inconsistent_system(self) -> None:
        # x0 = 0 and x0 = 1
        self.assertIsNone(gf2.solve_linear([0b01, 0b11], 1))

    def test_unique_solution_has_empty_basis(self) -> None:
        rows = [0b1_01, 0b0_10]  # x0 = 1, x1 = 0
        self.assertEqual(gf2.solve_linear(rows, 2), (0b01, []))

    def test_column_solver_agrees_with_row_elimination(self) -> None:
        rng = random.Random(21)
        for _ in range(300):
            variables = rng.randrange(1, 20)
            equations = rng.randrange(1, 40)
            columns = [rng.getrandbits(equations) for _ in range(variables)]
            target = rng.getrandbits(equations) if rng.random() < 0.5 else 0
            if rng.random() < 0.5:  # make it consistent
                secret = rng.getrandbits(variables)
                target = 0
                for index, column in enumerate(columns):
                    if (secret >> index) & 1:
                        target ^= column
            rows = []
            for bit in range(equations):
                coefficients = sum(((column >> bit) & 1) << index for index, column in enumerate(columns))
                rows.append(coefficients | (((target >> bit) & 1) << variables))
            by_rows = gf2.solve_linear(rows, variables)
            by_columns = gf2.solve_columns(columns, target)
            self.assertEqual(by_rows is None, by_columns is None)
            if by_columns is None or by_rows is None:
                continue
            particular, nullspace = by_columns
            self.assertEqual(len(nullspace), len(by_rows[1]))
            for vector in gf2.span(particular, nullspace, 32):
                combined = 0
                for index, column in enumerate(columns):
                    if (vector >> index) & 1:
                        combined ^= column
                self.assertEqual(combined, target)

    def test_span_limit(self) -> None:
        self.assertEqual(len(list(gf2.span(0, [1, 2, 4], 5))), 5)


if __name__ == "__main__":
    unittest.main()
