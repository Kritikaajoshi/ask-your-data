from benchmark.run_benchmark import results_match


def test_same_values_match_in_any_order():
    assert results_match([["East", 1.0], ["West", 2.5]], [["West", 2.5], ["East", 1]])


def test_extra_columns_are_allowed():
    assert results_match([["Smart Watch"]], [["Smart Watch", 40123.5]])


def test_floats_are_compared_to_two_decimals():
    assert results_match([[59.33]], [[59.3333333]])


def test_different_values_or_row_counts_do_not_match():
    assert not results_match([["North"]], [["South"]])
    assert not results_match([["a"], ["b"]], [["a"]])
