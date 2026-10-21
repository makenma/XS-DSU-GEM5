from dataclasses import dataclass
import hashlib

from mesh_ir.acceptance import validate_schema
from mesh_ir.agent_workload import load_strict_json
from mesh_ir.generated import abi as A, agent_abi as AG
from mesh_ir.host_bindings import build_host_binding_plans
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import CacheReservationCoordinator, apply_cache_state_replay
from mesh_ir.weight_registry import (
    load_model_weight_image, load_program_weight_registry, tag_manifest_bytes,
    weight_region_order, weight_tag_index_map, weight_tag_manifest,
)


@dataclass(frozen=True)
class RuntimeWeightImage:
    projection: dict
    core_tags: dict
    slot_count: int
    slot_bytes: int
    tag_bytes: tuple

    @classmethod
    def load(cls, program, arch, image_path, registry_path, image_digest, workload):
        image = load_model_weight_image(image_path)
        registry = load_program_weight_registry(registry_path, image, program)
        if image.digest != image_digest:
            raise MeshIrError('E_MOE_CACHE', 'weight image differs from the runtime identity')
        partition = arch.partition(A.SRAM_PARTITION_KIND.WEIGHT_CACHE)
        if partition is None or not arch.sram_weight_cache_slot_bytes:
            raise MeshIrError('E_MOE_CACHE', 'cache geometry is absent')
        symbols = []
        semantic = bytes.fromhex(program.semantic_sha256)
        plans = build_host_binding_plans(program, [region.base for region in arch.regions])
        instance_counts = {(plan.program_id, plan.profile_id): max(plan.instance_counts) for plan in plans}
        instances = sum(instance_counts[(round_.program_id, round_.profile_id)]
            for user in workload.users for task in user.tasks for round_ in task.rounds)
        bound = instances * len(weight_region_order(program)) * max(1, len(program.moe_layer_specs))
        if bound >= (1 << 32) - 1:
            raise MeshIrError('E_MOE_CACHE', 'run may exhaust cache fill identities')
        requirements = {row.symbol_id: row for plan in plans for row in plan.requirements
            if row.kind == AG.BINDING_KIND.WEIGHT_EXTERNAL}
        for symbol, requirement in sorted(requirements.items()):
            binding = registry.symbol(semantic, symbol)
            home = image.home(binding.weight_home_id)
            address = image.arena_base + home.arena_offset + binding.home_offset
            if address != requirement.platform_address or binding.bytes != requirement.required_bytes:
                raise MeshIrError('E_MOE_CACHE', 'registry disagrees with the installed weight binding')
            data = home.content[binding.home_offset:binding.home_offset + binding.bytes]
            symbols.append(dict(symbol_id=symbol, address=(address if address <= (1 << 53) - 1
                            else f"0x{address:016x}"), data_hex=data.hex()))
        manifest = weight_tag_manifest(program, registry)
        projection = dict(program_semantic_digest=program.semantic_sha256,
            effective_architecture_digest=arch.digest().hex(),
            model_weight_image_digest=image.digest, program_weight_registry_digest=registry.digest,
            weight_tag_manifest_digest=hashlib.sha256(tag_manifest_bytes(manifest)).hexdigest(),
            symbols=symbols, reservation_token_bound=bound)
        index = weight_tag_index_map(program)
        core_tags = {core: set() for core in sorted(arch.core_ids)}
        for expert in program.moe_expert_specs:
            core_tags[expert.core_id].add(index[(expert.layer_id, expert.expert_id)])
        return cls(projection, core_tags, partition.bytes // arch.sram_weight_cache_slot_bytes,
                   arch.sram_weight_cache_slot_bytes, tuple(size for _, _, size in weight_region_order(program)))

    def replay_document(self, cores):
        document = {key: value for key, value in self.projection.items()
                    if key not in ('program_semantic_digest', 'symbols', 'reservation_token_bound')}
        document.update(schema='cache_state_replay_v1', version=1, cores=cores)
        self.validate_replay(document, for_execution=False)
        return document

    def load_replay(self, path):
        document = load_strict_json(path)
        self.validate_replay(document)
        return document

    def validate_replay(self, document, *, for_execution=True):
        validate_schema('cache_state_replay.schema.json', document)
        for key in ('effective_architecture_digest', 'model_weight_image_digest',
                    'program_weight_registry_digest', 'weight_tag_manifest_digest'):
            if document[key] != self.projection[key]:
                raise MeshIrError('E_MOE_CACHE', f'cache replay {key} mismatch')
        if [row['core_id'] for row in document['cores']] != list(self.core_tags):
            raise MeshIrError('E_MOE_CACHE', 'cache replay must exactly cover every core in order')
        for row in document['cores']:
            if for_execution and row['next_fill_incarnation'] > (1 << 32) - 1 - self.projection['reservation_token_bound']:
                raise MeshIrError('E_MOE_CACHE', 'warm cache identities cannot cover the run')
            for slot in row['slots']:
                if slot['state'] == A.CACHE_SLOT_STATE.VALID:
                    tag = slot['weight_tag_index']
                    if tag not in self.core_tags[row['core_id']] or slot['valid_bytes'] != self.tag_bytes[tag]:
                        raise MeshIrError('E_MOE_CACHE', 'cache tag or byte count differs from the manifest')
            owner = CacheReservationCoordinator(row['core_id'], self.slot_count, self.slot_bytes,
                self.core_tags[row['core_id']], self.slot_count, self.slot_count, self.slot_count, self.slot_count * 2)
            apply_cache_state_replay(owner, dict(document, cores=[row]))
