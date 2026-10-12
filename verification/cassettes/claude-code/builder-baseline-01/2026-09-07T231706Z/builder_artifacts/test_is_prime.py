from is_prime import is_prime


def test_negative_and_small_below_two():
    assert is_prime(-10) is False
    assert is_prime(-1) is False
    assert is_prime(0) is False
    assert is_prime(1) is False


def test_small_primes():
    for p in [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]:
        assert is_prime(p) is True, f"{p} should be prime"


def test_small_composites():
    for c in [4, 6, 8, 9, 10, 12, 15, 16, 20, 21, 25, 27]:
        assert is_prime(c) is False, f"{c} should be composite"


def test_moderately_large_prime():
    # 7919 is the 1000th prime
    assert is_prime(7919) is True


def test_moderately_large_composite():
    # 7920 = 7919 + 1, has many small factors
    assert is_prime(7920) is False
    # 8051 = 83 * 97
    assert is_prime(8051) is False


def test_edge_case_two_and_three():
    assert is_prime(2) is True
    assert is_prime(3) is True


if __name__ == "__main__":
    test_negative_and_small_below_two()
    test_small_primes()
    test_small_composites()
    test_moderately_large_prime()
    test_moderately_large_composite()
    test_edge_case_two_and_three()
    print("All tests passed.")
