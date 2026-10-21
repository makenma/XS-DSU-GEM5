from collections import Counter

from mesh_ir.agent_config import agent_proxy_control_base
from mesh_ir.architecture import ArchManifest
from mesh_ir.effective import EffectiveArchitecture
from mesh_ir.runtime_deployment import ServingDeployment
from mesh_ir.runtime_errors import require
from mesh_ir.runtime_fabric import core_parameters, target_parameters


def instantiated_objects(configuration: dict) -> dict[str, dict]:
    objects = {}
    pending = [configuration]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            if 'path' in value and 'type' in value:
                require(value['path'] not in objects, 'instantiated object path is duplicated')
                objects[value['path']] = value
            pending.extend(value.values())
    return objects


def verify_serving_deployment(arch: ArchManifest, runtime: dict, configuration: dict, scenario: dict, *,
                              ticks_per_second: int) -> dict:
    addresses = runtime['serving']['address_map']
    host = runtime['agent_axi_driver']['remote_host_memory']
    deployment = ServingDeployment.from_arch(arch, addresses['npu_control_page_base'],
        agent_proxy_control_base(addresses), host['base'], host['bytes'])
    fabric = deployment.fabric
    require(scenario == deployment.scenario(), 'instantiated deployment scenario differs from admitted topology')
    objects = instantiated_objects(configuration)
    system = configuration['system']
    ruby = system['ruby']
    network = ruby['network']
    require(network['type'] == 'GarnetNetwork' and not network['aggregate_initial'],
        'deployment is not FULL_TIMING Garnet')
    require(network['dual_lane'] == fabric.network.dual_lane and network['ni_flit_size'] == fabric.network.flit_bytes and
        network['num_rows'] == fabric.network.router_rows and
        network['number_of_virtual_networks'] == len(fabric.network.vnet_classes), 'network geometry differs from architecture')
    routers = network['routers']
    require(len(routers) == fabric.routers and {row['router_id'] for row in routers} == set(range(fabric.routers)),
        'physical router coverage differs from architecture')
    for router in routers:
        require(router['dual_lane'] == fabric.network.dual_lane and router['width'] == fabric.network.flit_bytes and
            router['latency'] == fabric.network.router_latency_cycles, 'physical router parameters differ from architecture')
    for link in (*network['ext_links'], *network['int_links']):
        require(link['width'] == fabric.network.flit_bytes and link['latency'] == fabric.network.link_latency_cycles,
            'physical link geometry differs from architecture')
        require(fabric.network.dual_lane or not link['lane_parallel'], 'single-lane deployment contains a parallel lane')
        physical_links = link['network_links'] if 'network_links' in link else (link['network_link'],)
        require(all(row['width'] == fabric.network.flit_bytes and row['link_latency'] == fabric.network.link_latency_cycles
            for row in physical_links), 'physical data-link width or latency differs from architecture')
    edges = Counter((objects[row['src_node']]['router_id'], objects[row['dst_node']]['router_id'])
        for row in network['int_links'])
    expected_edges = Counter()
    cols = fabric.network.router_cols
    for node in range(fabric.routers):
        row, col = divmod(node, cols)
        for next_row, next_col in ((row, col + 1), (row + 1, col)):
            if next_row >= fabric.network.router_rows or next_col >= cols:
                continue
            peer = next_row * cols + next_col
            expected_edges[node, peer] += 2 if fabric.network.dual_lane else 1
            expected_edges[peer, node] += 2 if fabric.network.dual_lane else 1
    require(edges == expected_edges, 'physical mesh links differ from the declared router grid')
    attachments = {row['ext_node']: objects[row['int_node']]['router_id'] for row in network['ext_links']}
    require(len(attachments) == len(network['ext_links']), 'physical endpoint has repeated network attachments')
    sources = [row for row in objects.values() if row['type'] == 'AxiInitiatorAdapter']
    targets = [row for row in objects.values() if row['type'] == 'AxiTargetAdapter']
    require(len(sources) == len(fabric.initiators) and
        {(row['src_node'], row['src_port']) for row in sources} == {(row.src_node, row.src_port) for row in fabric.initiators},
        'physical initiators differ from deployment')
    require(len(targets) == len(fabric.targets) and {row['dst_node'] for row in targets} == {row.dst_node for row in fabric.targets},
        'physical targets differ from deployment')
    require(set(attachments) == {row['shim'] for row in (*sources, *targets)}, 'network endpoint coverage differs from deployment')
    source_map = {(row['src_node'], row['src_port']): row for row in sources}
    target_map = {row['dst_node']: row for row in targets}
    for expected in fabric.initiators:
        actual = source_map[expected.src_node, expected.src_port]
        require(actual['data_bus_bytes'] == arch.axi_data_bytes and attachments[actual['shim']] == expected.router_id,
            'initiator width or physical router differs from deployment')
    memory = []
    for expected in fabric.targets:
        actual = target_map[expected.dst_node]
        require(actual['data_bus_bytes'] == arch.axi_data_bytes and attachments[actual['shim']] == expected.router_id,
            'target width or physical router differs from deployment')
        ranges = [(row.begin, row.end) for row in fabric.address_ranges if row.node == expected.dst_node]
        require(list(zip(actual['memory_range_starts'], actual['memory_range_ends'], strict=True)) == ranges,
            'target backing ranges differ from deployment')
        require(all(actual[name] == value for name, value in target_parameters(expected).items()),
            'target memory service parameters differ from architecture')
        if expected.dst_node in fabric.memory_nodes:
            memory.append(dict(node=expected.dst_node, router_id=expected.router_id, ranges=ranges,
                synthetic_hbm=target_parameters(expected)))
    cores = [row for row in objects.values() if row['type'] == 'MeshDummyCore']
    require(len(cores) == len(arch.core_ids) and {row['core_id'] for row in cores} == set(arch.core_ids),
        'physical compute core coverage differs from architecture')
    parameters = core_parameters(EffectiveArchitecture(arch))
    for core in cores:
        require(all(core[name] == value for name, value in parameters.items()), 'physical core capabilities differ from architecture')
        require(not core['reference_compute'], 'DummyCore execution unexpectedly enables numerical reference compute')
        clock = objects[core['clk_domain']]['clock']
        require(len(clock) == 1 and clock[0] * arch.clock_hz == ticks_per_second, 'physical core clock differs from architecture')
        dma = core['dma']
        source = fabric.initiators[fabric.core_initiator_index(core['core_id'])]
        require(dma['data_bus_bytes'] == arch.axi_data_bytes and
            objects[dma['bridge']]['adapter'] == source_map[source.src_node, source.src_port]['path'],
            'core DMA is connected to the wrong physical initiator')
    endpoints = [row for row in objects.values() if row['type'] == 'NpuMemoryEndpoint']
    require(len(endpoints) == len(fabric.memory_nodes) and
        {row['adapter'] for row in endpoints} == {target_map[node]['path'] for node in fabric.memory_nodes},
        'memory endpoint backing coverage differs from deployment')
    driver, frontend = system['gate6_driver'], system['gate6_frontend']
    require(system['mesh_loader']['command_rom_entries'] == arch.command_rom_entries,
        'native command ROM capacity differs from the architecture')
    source = fabric.initiators[deployment.driver_index]
    require(driver['type'] == 'AgentAxiDriver' and driver['master'] == source_map[source.src_node, source.src_port]['path'] and
        driver['target'] == target_map[fabric.targets[deployment.host_target_index].dst_node]['path'] and
        driver['data_bus_bytes'] == arch.axi_data_bytes, 'CPU driver is not attached to its admitted physical endpoints')
    require(frontend['type'] == 'NpuServingFrontend' and frontend['executor'] == 'serving_mesh',
        'frontend does not execute the compiled Serving Program')
    frontend_source = fabric.initiators[deployment.frontend_index]
    require(frontend['master'] == source_map[frontend_source.src_node, frontend_source.src_port]['path'] and
        frontend['control_target'] == target_map[fabric.targets[deployment.npu_control_target_index].dst_node]['path'] and
        frontend['data_bus_bytes'] == arch.axi_data_bytes and frontend['serving_loader'] == system['mesh_loader']['path'],
        'Serving frontend is not attached to its admitted control and execution endpoints')
    return dict(routers=fabric.routers, router_rows=fabric.network.router_rows, router_cols=fabric.network.router_cols,
        core_ids=list(arch.core_ids), sram_bytes_per_core=arch.sram_bytes, clock_hz=arch.clock_hz,
        command_rom_entries_per_core=arch.command_rom_entries,
        axi_data_bytes=arch.axi_data_bytes, flit_bytes=fabric.network.flit_bytes, dual_lane=fabric.network.dual_lane,
        memory=memory, driver_router=source.router_id)
