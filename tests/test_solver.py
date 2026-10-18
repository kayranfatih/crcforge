from __future__ import annotations

import itertools
import random
import unittest

import _support  # noqa: F401
from crcforge.catalog import get_algorithm
from crcforge.checksums import SumChecksum
from crcforge.crc import CrcAlgorithm, calculate_crc
from crcforge.solver import (
    REFLECTION_COMBOS,
    Sample,
    checksum_variants,
    derive_polys,
    expected_chance_matches,
    sample_variants,
    scan_custom,
    solve,
    solve_init_xorout,
    sum_family_candidates,
    swap_value_bytes,
    x_plus_1_multiplicity,
)


def params(algorithm: CrcAlgorithm) -> tuple[int, int, int, bool, bool]:
    return (algorithm.poly, algorithm.init, algorithm.xor_out, algorithm.refin, algorithm.refout)


def shape(algorithm: CrcAlgorithm) -> tuple[int, bool, bool]:
    return (algorithm.poly, algorithm.refin, algorithm.refout)


def random_crc(rng: random.Random, width: int) -> CrcAlgorithm:
    return CrcAlgorithm(
        "secret",
        width,
        rng.randrange(1 << width) | 1,
        rng.randrange(1 << width),
        rng.randrange(1 << width),
        rng.random() < 0.5,
        rng.random() < 0.5,
    )


def samples_for(algorithm: CrcAlgorithm, datas: list[bytes]) -> list[Sample]:
    return [Sample(data, calculate_crc(data, algorithm)) for data in datas]


class SolveTest(unittest.TestCase):
    def assert_sound(self, result_matches: list, samples: list[Sample]) -> None:  # type: ignore[type-arg]
        for match in result_matches:
            for sample in samples:
                self.assertEqual(calculate_crc(sample.data, match.algorithm), sample.checksum, match)

    def test_recovers_random_custom_crcs_exactly(self) -> None:
        rng = random.Random(2024)
        for width in (5, 8, 12, 16, 24, 32, 40, 64):
            for _ in range(6):
                secret = random_crc(rng, width)
                length = rng.randrange(6, 30)
                datas = [rng.randbytes(length) for _ in range(3)]
                datas += [rng.randbytes(length + 13), rng.randbytes(length + 29)]
                samples = samples_for(secret, datas)
                result = solve(samples, width, swap_checksums=False)
                with self.subTest(width=width, secret=secret):
                    self.assert_sound(result.crc_matches, samples)
                    same_shape = [m for m in result.crc_matches if shape(m.algorithm) == shape(secret)]
                    self.assertTrue(same_shape)
                    if all(m.free_bits == 0 for m in same_shape):
                        self.assertIn(params(secret), [params(m.algorithm) for m in same_shape])

    def test_recovers_catalog_polynomials_with_random_init_and_xorout_exactly(self) -> None:
        rng = random.Random(99)
        for name in ("crc-8", "crc-16-ccitt-false", "crc-16-arc", "crc-24-openpgp", "crc-32", "crc-32c", "crc-64-xz"):
            base = get_algorithm(name)
            assert isinstance(base, CrcAlgorithm)
            secret = CrcAlgorithm(
                "secret",
                base.width,
                base.poly,
                rng.randrange(1 << base.width),
                rng.randrange(1 << base.width),
                base.refin,
                base.refout,
            )
            samples = samples_for(secret, [rng.randbytes(12), rng.randbytes(12), rng.randbytes(12), rng.randbytes(31)])
            result = solve(samples, base.width, swap_checksums=False)
            found = [params(m.algorithm) for m in result.crc_matches if shape(m.algorithm) == shape(secret)]
            with self.subTest(name):
                self.assertIn(params(secret), found)
                self.assert_sound(result.crc_matches, samples)
                # A polynomial divisible by (x+1)^m gives 2^m equivalent init/xorout twins; others are unique.
                multiplicity = x_plus_1_multiplicity(base.width, base.poly)
                self.assertEqual(len(found), 1 << multiplicity)
                if multiplicity:
                    self.assertTrue(any("(x+1)" in note for note in result.derived_notes))

    def test_equal_length_samples_find_the_polynomial_and_flag_ambiguity(self) -> None:
        secret = get_algorithm("crc-32")
        assert isinstance(secret, CrcAlgorithm)
        rng = random.Random(1)
        samples = samples_for(secret, [rng.randbytes(20) for _ in range(3)])
        result = solve(samples, 32, swap_checksums=False)
        self.assertEqual([m.algorithm.name for m in result.catalog_matches], ["crc-32"])
        self.assertTrue(any(m.algorithm.poly == 0x04C11DB7 and m.free_bits for m in result.crc_matches))
        self.assertTrue(any("same length" in note for note in result.derived_notes))
        self.assert_sound(result.crc_matches, samples)

    def test_two_equal_length_samples_use_factorization(self) -> None:
        rng = random.Random(3)
        for _ in range(5):
            secret = random_crc(rng, 32)
            samples = samples_for(secret, [rng.randbytes(16), rng.randbytes(16)])
            result = solve(samples, 32, swap_checksums=False, init_values=[secret.init])
            self.assertIn(params(secret), [params(m.algorithm) for m in result.crc_matches])

    def test_different_lengths_only_scan_small_widths(self) -> None:
        rng = random.Random(4)
        secret = random_crc(rng, 8)
        samples = samples_for(secret, [rng.randbytes(n) for n in (3, 7, 12, 20)])
        result = solve(samples, 8, swap_checksums=False)
        # init/xorout may come back as an equivalent pair; soundness below proves it fits every sample.
        shapes = [(m.algorithm.poly, m.algorithm.refin, m.algorithm.refout) for m in result.crc_matches]
        self.assertIn((secret.poly, secret.refin, secret.refout), shapes)
        self.assertTrue(any("scanned" in note for note in result.notes))
        self.assert_sound(result.crc_matches, samples)

    def test_catalog_match_skips_the_slow_scan_unless_exhaustive(self) -> None:
        algorithm = get_algorithm("crc-16-xmodem")
        samples = [Sample(data, algorithm.compute(data)) for data in (b"A", b"BCD", b"EFGHI", b"JKLMNOP")]
        quick = solve(samples, 16, swap_checksums=False, reflections=[(False, False)])
        self.assertEqual([m.algorithm.name for m in quick.catalog_matches], ["crc-16-xmodem"])
        self.assertTrue(any("scan was skipped" in note for note in quick.notes))
        full = solve(samples, 16, swap_checksums=False, reflections=[(False, False)], exhaustive=True)
        self.assertTrue(any("scanned" in note for note in full.notes))
        self.assertIn("crc-16-xmodem", [m.catalog_name for m in full.crc_matches])

    def test_under_determined_scan_is_refused_with_a_note(self) -> None:
        rng = random.Random(5)
        secret = random_crc(rng, 16)
        samples = samples_for(secret, [rng.randbytes(5), rng.randbytes(9)])
        result = solve(samples, 16, swap_checksums=False, reflections=[(True, True)])
        self.assertEqual(result.crc_matches, [])
        self.assertTrue(any("cannot single out" in note for note in result.notes))

    def test_wide_crc_without_equal_lengths_asks_for_more_samples(self) -> None:
        rng = random.Random(6)
        secret = random_crc(rng, 32)
        samples = samples_for(secret, [rng.randbytes(5), rng.randbytes(9), rng.randbytes(13)])
        result = solve(samples, 32, swap_checksums=False)
        self.assertTrue(any("same length" in note for note in result.notes))

    def test_byte_swapped_checksums_are_detected(self) -> None:
        algorithm = get_algorithm("crc-16-modbus")
        rng = random.Random(7)
        samples = [
            Sample(sample.data, int(swap_value_bytes(sample.checksum, 16) or 0))
            for sample in samples_for(algorithm, [rng.randbytes(10) for _ in range(3)])  # type: ignore[arg-type]
        ]
        result = solve(samples, 16)
        self.assertIn(
            ("crc-16-modbus", "byte-swapped"), [(m.algorithm.name, m.crc_mode) for m in result.catalog_matches]
        )

    def test_byte_order_scan(self) -> None:
        algorithm = get_algorithm("crc-16-xmodem")
        rng = random.Random(8)
        datas = [rng.randbytes(8) for _ in range(3)]
        samples = [Sample(data[::-1], algorithm.compute(data)) for data in datas]
        result = solve(samples, 16, byte_order_modes=("native", "reverse"))
        self.assertIn(("crc-16-xmodem", "reverse"), [(m.algorithm.name, m.input_mode) for m in result.catalog_matches])

    def test_sum_layouts_are_identified(self) -> None:
        layout = SumChecksum("hidden", 16, word_size=2, endian="little", complement="twos")
        rng = random.Random(9)
        samples = [Sample(data, layout.compute(data)) for data in (rng.randbytes(12) for _ in range(4))]
        result = solve(samples, 16, swap_checksums=False)
        self.assertTrue(any(m.algorithm.params() == layout.params() for m in result.checksum_matches))

    def test_catalog_checksums_are_reported(self) -> None:
        algorithm = get_algorithm("internet-checksum")
        rng = random.Random(10)
        samples = [Sample(data, algorithm.compute(data)) for data in (rng.randbytes(20) for _ in range(3))]
        result = solve(samples, 16, swap_checksums=False)
        self.assertIn("ones-complement-16", [m.algorithm.name for m in result.checksum_matches])

    def test_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            solve([], 16)
        with self.assertRaises(ValueError):
            solve([Sample(b"x", 0x1FFFF)], 16)
        with self.assertRaises(ValueError):
            solve([Sample(b"x", 1)], 0)


class InitXorTest(unittest.TestCase):
    def test_constraints_select_the_matching_pair(self) -> None:
        algorithm = get_algorithm("crc-16-ccitt-false")
        assert isinstance(algorithm, CrcAlgorithm)
        samples = samples_for(algorithm, [b"123456789"])
        by_init = solve_init_xorout(samples, 16, 0x1021, False, False, init_values=[0xFFFF])
        assert by_init is not None
        self.assertEqual(by_init.pairs, ((0xFFFF, 0x0000),))
        by_xorout = solve_init_xorout(samples, 16, 0x1021, False, False, xorout_values=[0x0000])
        assert by_xorout is not None
        self.assertIn((0xFFFF, 0x0000), by_xorout.pairs)
        self.assertIsNone(solve_init_xorout(samples, 16, 0x1021, False, False, init_values=[0xFFFF], xorout_values=[1]))


class DerivePolysTest(unittest.TestCase):
    def test_no_equal_lengths_returns_none(self) -> None:
        self.assertIsNone(derive_polys([Sample(b"a", 1), Sample(b"bc", 2)], 8, False, False))

    def test_contradictory_samples_give_no_polynomial(self) -> None:
        self.assertEqual(derive_polys([Sample(b"ab", 1), Sample(b"ab", 2)], 16, False, False), ([], False))

    def test_identical_samples_carry_no_information(self) -> None:
        self.assertIsNone(derive_polys([Sample(b"ab", 1), Sample(b"ab", 1)], 16, False, False))


class ScanCustomTest(unittest.TestCase):
    def naive(
        self, samples: list[Sample], width: int, inits: list[int], xorouts: list[int]
    ) -> set[tuple[int, int, int, bool, bool]]:
        found = set()
        for poly, init, xorout, (refin, refout) in itertools.product(
            range(1 << width), inits, xorouts, REFLECTION_COMBOS
        ):
            algorithm = CrcAlgorithm("n", width, poly, init, xorout, refin, refout)
            if all(calculate_crc(s.data, algorithm) == s.checksum for s in samples):
                found.add(params(algorithm))
        return found

    def scan(
        self, samples: list[Sample], width: int, inits: list[int] | None, xorouts: list[int] | None, jobs: int = 1
    ) -> set[tuple[int, int, int, bool, bool]]:
        result = scan_custom(
            [("native", "as-given", tuple(samples))],
            width,
            range(1 << width),
            REFLECTION_COMBOS,
            inits,
            xorouts,
            keep=100_000,
            jobs=jobs,
        )
        return {params(m.algorithm) for m in result.matches}

    def test_matches_naive_brute_force_with_fixed_init(self) -> None:
        rng = random.Random(11)
        for width in (4, 5, 6):
            secret = random_crc(rng, width)
            samples = samples_for(secret, [rng.randbytes(6)])
            inits = [0, secret.init]
            xorouts = list(range(1 << width))
            with self.subTest(width=width):
                self.assertEqual(self.scan(samples, width, inits, None), self.naive(samples, width, inits, xorouts))
                self.assertEqual(self.scan(samples, width, inits, xorouts), self.naive(samples, width, inits, xorouts))

    def test_matches_naive_brute_force_with_fixed_xorout(self) -> None:
        rng = random.Random(12)
        width = 5
        secret = random_crc(rng, width)
        samples = samples_for(secret, [rng.randbytes(4), rng.randbytes(7)])
        xorouts = [secret.xor_out]
        inits = list(range(1 << width))
        self.assertEqual(self.scan(samples, width, None, xorouts), self.naive(samples, width, inits, xorouts))

    def test_matches_naive_brute_force_with_both_free(self) -> None:
        rng = random.Random(13)
        width = 4
        secret = random_crc(rng, width)
        samples = samples_for(secret, [rng.randbytes(3), rng.randbytes(5), rng.randbytes(8)])
        everything = list(range(1 << width))
        self.assertEqual(self.scan(samples, width, None, None), self.naive(samples, width, everything, everything))

    def test_parallel_workers_give_the_same_matches(self) -> None:
        rng = random.Random(14)
        secret = random_crc(rng, 8)
        samples = samples_for(secret, [rng.randbytes(9)])
        self.assertEqual(self.scan(samples, 8, [0, 0xFF], None, jobs=2), self.scan(samples, 8, [0, 0xFF], None, jobs=1))

    def test_keep_limits_stored_matches_but_counts_all(self) -> None:
        samples = [Sample(b"123456789", 0x31C3)]
        result = scan_custom(
            [("native", "as-given", tuple(samples))], 8, range(256), REFLECTION_COMBOS, [0], None, keep=3
        )
        self.assertEqual(len(result.matches), 3)
        self.assertGreater(result.match_count, 3)
        self.assertEqual(result.tested, 256 * 4)


class HelpersTest(unittest.TestCase):
    def test_checksum_variants(self) -> None:
        self.assertEqual(checksum_variants(0x1234, 16), [("as-given", 0x1234), ("byte-swapped", 0x3412)])
        self.assertEqual(checksum_variants(0x1221, 16)[1:], [("byte-swapped", 0x2112)])
        self.assertEqual(checksum_variants(0x12, 8), [("as-given", 0x12)])
        self.assertEqual(checksum_variants(0x3434, 16), [("as-given", 0x3434)])
        self.assertIsNone(swap_value_bytes(0x123, 12))

    def test_sample_variants(self) -> None:
        variants = sample_variants([Sample(b"a", 0x0102)], 16)
        self.assertEqual([name for name, _ in variants], ["as-given", "byte-swapped"])
        self.assertEqual(variants[1][1][0].checksum, 0x0201)

    def test_expected_chance_matches(self) -> None:
        self.assertAlmostEqual(expected_chance_matches(65536, 4, 1, 1, 16, 1), 4.0)
        self.assertLess(expected_chance_matches(65536, 4, 1, 1, 16, 2), 0.001)

    def test_sum_family_candidates(self) -> None:
        self.assertEqual(sum_family_candidates(12), [])
        names = [c.name for c in sum_family_candidates(16)]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(name.startswith("custom-") for name in names))


if __name__ == "__main__":
    unittest.main()
