import pytest

from intake_agent.cli import main
from intake_agent.estimate import Assumptions, estimate_cost_usd, measure_prompt_sizes


def test_prefix_is_long_enough_to_cache_and_caching_lowers_the_estimate() -> None:
    sizes = measure_prompt_sizes()
    # Claude Opus 5.5 caches prefixes of 512 tokens or more.
    assert sizes.prefix_chars / Assumptions().chars_per_token > 512
    cached = estimate_cost_usd(sizes, Assumptions(), cached=True)
    uncached = estimate_cost_usd(sizes, Assumptions(), cached=False)
    assert 0 < cached < uncached


def test_estimate_command_labels_its_output_as_an_estimate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["estimate-cost"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("ESTIMATE, not a measurement.")
    assert "| Typical |" in output
