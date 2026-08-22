from cloth_next.veyra.iteration import (IterationCandidate,
                                        run_monotonic_iterations)


def candidate(name="candidate", delta=1.0):
    return IterationCandidate(name, {0: (delta, 0.0, 0.0)})


def test_two_improving_passes_are_accepted():
    counts = iter((1500, 620))
    result = run_monotonic_iterations(
        {0: (0.0, 0.0, 0.0)}, 2129,
        lambda _count, pass_index: (() if pass_index == 2 else
                                    (candidate(str(pass_index)),)),
        lambda _coordinates, _candidate: next(counts))
    assert (result.final_count, result.accepted) == (620, 2)
    assert result.termination_reason == "PARTIAL_SUCCESS"


def test_increase_rolls_back_exactly():
    coordinates = {0: (0.1, 0.2, 0.3)}
    baseline = dict(coordinates)
    result = run_monotonic_iterations(
        coordinates, 2129, lambda *_: (candidate(),),
        lambda *_: 2200)
    assert result.rejected == 1
    assert coordinates == baseline


def test_equal_count_rolls_back_exactly():
    coordinates = {0: (0.1, 0.2, 0.3)}
    baseline = dict(coordinates)
    result = run_monotonic_iterations(
        coordinates, 2129, lambda *_: (candidate(),),
        lambda *_: 2129)
    assert result.termination_reason == "NO_SAFE_REPAIR"
    assert coordinates == baseline


def test_monotonic_sequence_can_reach_zero():
    counts = iter((1200, 0))
    result = run_monotonic_iterations(
        {0: (0.0, 0.0, 0.0)}, 2129,
        lambda *_: (candidate(),), lambda *_: next(counts))
    assert result.final_count == 0
    assert result.termination_reason == "SUCCESS"


def test_partial_success_when_no_further_candidate_exists():
    result = run_monotonic_iterations(
        {0: (0.0, 0.0, 0.0)}, 2129,
        lambda _count, pass_index: (candidate(),) if pass_index == 0 else (),
        lambda *_: 900)
    assert result.final_count == 900
    assert result.termination_reason == "PARTIAL_SUCCESS"


def test_cancel_during_applied_candidate_rolls_back_to_last_accept():
    coordinates = {0: (0.0, 0.0, 0.0)}
    checks = [0]
    def cancelled():
        checks[0] += 1
        return checks[0] == 2
    result = run_monotonic_iterations(
        coordinates, 2129, lambda *_: (candidate(delta=.25),),
        lambda *_: 1000, cancelled=cancelled)
    assert result.termination_reason == "CANCELLED"
    assert coordinates == {0: (0.0, 0.0, 0.0)}
