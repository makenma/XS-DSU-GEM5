from __future__ import annotations

from copy import deepcopy

import pytest

from axi_test_lib import (
    build_negative_scenario,
    default_config_contract,
    parse_positive_vector,
    validate_config_contract,
)


def valid_pair():
    return default_config_contract(), build_negative_scenario(
        "n3a_unaligned_decerr"
    )


def test_accepts_default_configuration():
    config, scenario = valid_pair()
    assert validate_config_contract(config, scenario)


def test_rejects_invalid_csv():
    for value in ("1,2,3,4", "1,2,,4,5", "1,2,no,4,5"):
        with pytest.raises(ValueError):
            parse_positive_vector(value, 5, "depths")


def test_rejects_invalid_widths_and_datablock():
    config, scenario = valid_pair()
    for field, value in (
        ("link_width_bits", 127),
        ("data_width_bits", 1024),
        ("id_width_bits", 0),
        ("user_width_bits", 1),
        ("cacheline_size", 32),
    ):
        malformed = dict(config)
        malformed[field] = value
        with pytest.raises(ValueError):
            validate_config_contract(malformed, scenario)


def test_rejects_overlapping_or_empty_ranges():
    config, scenario = valid_pair()
    overlap = deepcopy(scenario)
    overlap["target_ranges"].append(
        {"dst_node": 1, "start": "0x100", "end": "0x300"}
    )
    with pytest.raises(ValueError):
        validate_config_contract(config, overlap)
    empty = deepcopy(scenario)
    empty["target_ranges"][0]["end"] = empty["target_ranges"][0]["start"]
    with pytest.raises(ValueError):
        validate_config_contract(config, empty)


def test_rejects_quota_oversubscription():
    config, scenario = valid_pair()
    config["target_capacity"] = [1, 1, 1, 1]
    with pytest.raises(ValueError):
        validate_config_contract(config, scenario)


def test_rejects_zero_or_infinite_buffers():
    config, scenario = valid_pair()
    for value in (0, float("inf")):
        malformed = deepcopy(config)
        malformed["source_fifo_depths"][2] = value
        with pytest.raises(ValueError):
            validate_config_contract(malformed, scenario)


def test_rejects_non_strict_or_compact_mode():
    config, scenario = valid_pair()
    for field, value in (
        ("strict_protocol", False),
        ("data_encoding", "compact"),
    ):
        malformed = dict(config)
        malformed[field] = value
        with pytest.raises(ValueError):
            validate_config_contract(malformed, scenario)
