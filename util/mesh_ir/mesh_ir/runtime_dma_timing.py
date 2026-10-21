from dataclasses import dataclass

from mesh_ir.generated.abi import MESH_OBJECT_DOMAIN as Domain
from mesh_ir.runtime_errors import require


@dataclass(frozen=True)
class DmaExecutionIdentity:
    instance: int
    core_id: int
    domain: int
    region_group_id: int
    region_id: int
    descriptor_id: int
    command_id: int
    generation: int


def dma_execution_identity(row, *, core_id=None, instance=None):
    values = {}
    for field in DmaExecutionIdentity.__dataclass_fields__:
        value = row.get(field, core_id if field == 'core_id' else instance if field == 'instance' else None)
        require(type(value) is int and value >= 0, f'dma execution has no valid {field}')
        values[field] = value
    require(core_id is None or values['core_id'] == core_id, 'dma execution core differs from its frame')
    require(instance is None or values['instance'] == instance, 'dma execution instance differs from its frame')
    require(values['domain'] in (Domain.STATIC, Domain.MOE_OVERLAY, Domain.WEIGHT_FILL, Domain.KV_RUNTIME), 'dma execution domain cannot submit transfers')
    require(values['domain'] != Domain.STATIC or values['region_group_id'] == values['region_id'] == 0,
            'static dma execution carries a runtime region')
    return DmaExecutionIdentity(**values)


def verified_dma_timing_phases(result):
    executions, bursts, joined, timings = {}, {}, {}, {}
    for frame in result.get('instances', []):
        for core_id, ledger in frame['cores'].items():
            observations = ledger['observations']
            for field in ('descriptor_executions', 'runtime_descriptor_executions'):
                for execution in observations.get(field, ()):
                    key = dma_execution_identity(execution, core_id=int(core_id), instance=frame['instance'])
                    require((key.domain == Domain.STATIC) == (field == 'descriptor_executions'), 'dma execution is archived in the wrong domain partition')
                    require(key not in executions, f'descriptor {key.descriptor_id} of core {key.core_id} archives two executions')
                    executions[key] = (execution, frame)
        for burst in frame.get('bursts', ()):
            key = dma_execution_identity(burst, instance=frame['instance'])
            bursts.setdefault(key, []).append(burst)
    for row in result['dma_timings']:
        key = dma_execution_identity(row)
        require(row.get('done_tick') is not None, f'a timing row of descriptor {key.descriptor_id} archives no done tick')
        require(key not in timings, f'descriptor {key.descriptor_id} of core {key.core_id} archives a second timing row')
        timings[key] = row
    for key, (execution, frame) in executions.items():
        row = timings.get(key)
        if row is None:
            require(not execution.get('completed'), f'completed descriptor {key.descriptor_id} of core {key.core_id} has no timing row joining its completion tick {execution["completion_tick"]}')
            continue
        require(row['done_tick'] == execution['completion_tick'], f'timing row of descriptor {key.descriptor_id} matches no archived execution completion')
        own = bursts.get(key, [])
        for field, channel, burst_field, extreme in (
            ('first_ar_tick', 'AR', 'ar_aw_tick', min),
            ('last_r_tick', 'AR', 'response_tick', max),
            ('first_aw_tick', 'AW', 'ar_aw_tick', min),
            ('first_b_tick', 'AW', 'response_tick', min),
        ):
            values = [burst[burst_field] for burst in own if burst['channel'] == channel and burst.get(burst_field)]
            expected = extreme(values) if values else 0
            archived = row.get(field) or 0
            require(archived == expected, f'descriptor {key.descriptor_id} of core {key.core_id} archives {field}={archived}, its own bursts pin the phase at {expected}')
        joined.setdefault((id(frame), key.core_id), []).append((row, execution))
    for key in timings:
        require(key in executions, f'timing row of descriptor {key.descriptor_id} of core {key.core_id} matches no archived execution')
    for key in bursts:
        require(key in executions, f'burst of descriptor {key.descriptor_id} has no archived execution')
    return joined, bursts
