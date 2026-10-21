import pytest

from mesh_ir.serving_calibration import ServingMeasurement, compare_serving


def measurement(latency=100, traffic=128):
    return ServingMeasurement(1000, 2.0, {1: latency}, {1: 'SUCCESS'},
                              {1: 'digest'}, {'AR': traffic}, 4, 200)


def test_calibration_reports_latency_and_cost_without_hiding_request_error():
    result = compare_serving(measurement(), measurement(120))
    assert result['request_relative_error']['1'] == .2
    assert result['max_absolute_request_error'] == .2
    assert result['host_speedup'] == 1


@pytest.mark.parametrize('fault', ['traffic', 'identity', 'payload'])
def test_calibration_refuses_incomparable_execution(fault):
    candidate = measurement(120, 64 if fault == 'traffic' else 128)
    if fault == 'identity':
        candidate.latencies = {2: 120}
    if fault == 'payload':
        candidate.outputs[1] = 'changed'
    with pytest.raises(ValueError):
        compare_serving(measurement(), candidate)
