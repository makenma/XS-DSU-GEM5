"""Model weight image and program weight-binding registry (contract 7.2/9.6).

Single owner of the weight domain: the immutable image owns content
identity, the registry owns the per-program symbol -> image projection.
Every MoE weight symbol, weight range digest and per-core weight tag
tuple is derived here; runtime code and the oracle must not invent
addresses, sizes or digests of their own.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, canonical_json_bytes

U64_MAX = (1 << 64) - 1
TAG_TUPLE_BYTES = 84
TAG_MANIFEST_MAGIC = b"AI_MESH_WEIGHT_TAG_V1\0"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _schema_root() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas" / "ai_mesh"


def _validate_schema(filename: str, document) -> None:
    schema_path = _schema_root() / filename
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(document),
                    key=lambda item: list(item.absolute_path))
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise MeshIrError("E_RELOCATION",
                          f"{filename}: {path or '$'} {first.message}")


def _projection_digest(document: dict, digest_field: str) -> str:
    projected = {key: value for key, value in document.items()
                 if key != digest_field}
    return _digest(canonical_json_bytes(projected))


def materialize_content(seed_utf8: str, byte_count: int) -> bytes:
    seed = seed_utf8.encode("utf-8")
    if not seed or byte_count <= 0:
        raise MeshIrError("E_RELOCATION", "synthetic weight recipe is empty")
    repeats = byte_count // len(seed) + 1
    return (seed * repeats)[:byte_count]


def _u64(value, what: str) -> int:
    if isinstance(value, bool):
        raise MeshIrError("E_ABI_BOUNDS", f"{what} must be an integer")
    if isinstance(value, int):
        if 0 <= value <= U64_MAX:
            return value
        raise MeshIrError("E_ABI_BOUNDS", f"{what} out of u64 range")
    if isinstance(value, str) and value.startswith("0x") and len(value) == 18:
        return int(value, 16)
    raise MeshIrError("E_ABI_BOUNDS", f"{what} is not a u64-json value")


def _hex64(value, what: str) -> bytes:
    if not isinstance(value, str) or len(value) != 64:
        raise MeshIrError("E_ABI_BOUNDS", f"{what} must be lowercase hex[64]")
    try:
        return bytes.fromhex(value)
    except ValueError as err:
        raise MeshIrError("E_ABI_BOUNDS",
                          f"{what} must be lowercase hex[64]") from err


@dataclass(frozen=True)
class WeightHome:
    weight_home_id: int
    arena_offset: int
    bytes: int
    content_sha256: bytes
    read_only_alias_group: int
    seed_utf8: str
    content: bytes = field(repr=False, default=b"")

    def address(self, arena_base: int) -> int:
        return arena_base + self.arena_offset


@dataclass(frozen=True)
class ModelWeightImage:
    arena_base: int
    arena_bytes: int
    homes: tuple
    digest: str
    document: dict = field(repr=False, default_factory=dict)

    def home(self, weight_home_id: int) -> WeightHome:
        for candidate in self.homes:
            if candidate.weight_home_id == weight_home_id:
                return candidate
        raise MeshIrError("E_RELOCATION", "weight home is not in the image",
                          weight_home_id=weight_home_id)


@dataclass(frozen=True)
class WeightRegion:
    region_offset: int
    bytes: int
    resolved_content_digest: bytes


@dataclass(frozen=True)
class WeightSymbolBinding:
    symbol_id: int
    weight_home_id: int
    home_offset: int
    bytes: int
    flags: int
    resolved_content_digest: bytes
    read_only_alias_group: int
    regions: tuple


@dataclass(frozen=True)
class ProgramWeightBindings:
    program_id: int
    program_semantic_digest: bytes
    symbols: tuple


@dataclass(frozen=True)
class ProgramWeightRegistry:
    image: ModelWeightImage
    programs: tuple
    digest: str
    document: dict = field(repr=False, default_factory=dict)

    def program(self, program_semantic_digest: bytes) -> ProgramWeightBindings:
        for candidate in self.programs:
            if candidate.program_semantic_digest == program_semantic_digest:
                return candidate
        raise MeshIrError("E_RELOCATION",
                          "program is not covered by the weight registry")

    def symbol(self, program_semantic_digest: bytes,
               symbol_id: int) -> WeightSymbolBinding:
        bindings = self.program(program_semantic_digest)
        for candidate in bindings.symbols:
            if candidate.symbol_id == symbol_id:
                return candidate
        raise MeshIrError("E_RELOCATION",
                          "weight symbol is not bindable in this program",
                          symbol_id=symbol_id)


def load_model_weight_image(path) -> ModelWeightImage:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != "model_weight_image_v1" or \
            document.get("version") != 1:
        raise MeshIrError("E_RELOCATION", "unsupported weight image schema")
    _validate_schema("model_weight_image_v1.schema.json", document)
    arena_base = _u64(document["arena_base"], "arena_base")
    arena_bytes = _u64(document["arena_bytes"], "arena_bytes")
    homes = []
    for index, entry in enumerate(document["homes"]):
        if entry["weight_home_id"] != index + 1:
            raise MeshIrError("E_RELOCATION",
                              "weight home ids must be dense from 1")
        byte_count = int(entry["bytes"])
        seed = entry["synthetic_fill"]["seed_utf8"]
        content = materialize_content(seed, byte_count)
        digest = _hex64(entry["content_sha256"], "content_sha256")
        if bytes.fromhex(_digest(content)) != digest:
            raise MeshIrError("E_RELOCATION",
                              "weight home digest does not match its content",
                              weight_home_id=entry["weight_home_id"])
        offset = _u64(entry["arena_offset"], "arena_offset")
        if offset + byte_count > arena_bytes:
            raise MeshIrError("E_RELOCATION", "weight home escapes the arena",
                              weight_home_id=entry["weight_home_id"])
        homes.append(WeightHome(
            weight_home_id=entry["weight_home_id"],
            arena_offset=offset,
            bytes=byte_count,
            content_sha256=digest,
            read_only_alias_group=int(entry["read_only_alias_group"]),
            seed_utf8=seed,
            content=content,
        ))
    for first in homes:
        for second in homes:
            if first.weight_home_id >= second.weight_home_id:
                continue
            if first.read_only_alias_group != second.read_only_alias_group:
                continue
            if first.content_sha256 != second.content_sha256 or \
                    first.bytes != second.bytes:
                raise MeshIrError("E_RELOCATION",
                                  "alias group mixes different weight content",
                                  alias_group=first.read_only_alias_group)
            if first.arena_offset == second.arena_offset:
                raise MeshIrError("E_RELOCATION",
                                  "alias group must not overlap in the arena",
                                  alias_group=first.read_only_alias_group)
    digest = _projection_digest(document, "model_weight_image_digest")
    if document["model_weight_image_digest"] != digest:
        raise MeshIrError("E_RELOCATION",
                          "model_weight_image_digest projection mismatch")
    return ModelWeightImage(arena_base=arena_base, arena_bytes=arena_bytes,
                            homes=tuple(homes), digest=digest,
                            document=document)


def _slice_digest(home: WeightHome, offset: int, byte_count: int) -> bytes:
    if offset + byte_count > home.bytes:
        raise MeshIrError("E_RELOCATION",
                          "weight range escapes its home",
                          weight_home_id=home.weight_home_id)
    return hashlib.sha256(
        home.content[offset:offset + byte_count]).digest()


def load_program_weight_registry(path, image: ModelWeightImage,
                                 program) -> ProgramWeightRegistry:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != "program_weight_bindings_v1" or \
            document.get("version") != 1:
        raise MeshIrError("E_RELOCATION", "unsupported registry schema")
    _validate_schema("program_weight_bindings_v1.schema.json", document)
    if _hex64(document["model_weight_image_digest"],
              "model_weight_image_digest") != \
            bytes.fromhex(image.digest):
        raise MeshIrError("E_RELOCATION",
                          "registry references another weight image")
    semantic = bytes.fromhex(program.semantic_sha256())
    programs = []
    for entry in document["programs"]:
        if _hex64(entry["program_semantic_digest"],
                  "program_semantic_digest") != semantic:
            continue
        symbols = []
        seen_regions = {}
        previous_symbol = 0
        for record in entry["symbols"]:
            symbol_id = int(record["symbol_id"])
            if symbol_id <= previous_symbol:
                raise MeshIrError("E_RELOCATION",
                                  "registry symbols must ascend by symbol id")
            previous_symbol = symbol_id
            home = image.home(int(record["weight_home_id"]))
            offset = _u64(record["home_offset"], "home_offset")
            byte_count = int(record["bytes"])
            declared = _hex64(record["resolved_content_digest"],
                              "resolved_content_digest")
            if declared != _slice_digest(home, offset, byte_count):
                raise MeshIrError("E_RELOCATION",
                                  "symbol digest does not match image content",
                                  symbol_id=symbol_id)
            if int(record["read_only_alias_group"]) != \
                    home.read_only_alias_group:
                raise MeshIrError("E_RELOCATION",
                                  "symbol alias group differs from its home",
                                  symbol_id=symbol_id)
            regions = []
            previous_range = None
            for region in record["regions"]:
                region_offset = _u64(region["region_offset"], "region_offset")
                region_bytes = int(region["bytes"])
                region_digest = _hex64(region["resolved_content_digest"],
                                       "resolved_content_digest")
                region_range = (region_offset, region_bytes)
                if previous_range is not None and \
                        region_range <= previous_range:
                    if region_range == previous_range:
                        continue
                    raise MeshIrError("E_RELOCATION",
                                      "symbol regions must ascend",
                                      symbol_id=symbol_id)
                if region_offset + region_bytes > byte_count:
                    raise MeshIrError("E_RELOCATION",
                                      "symbol region escapes the symbol",
                                      symbol_id=symbol_id)
                if region_digest != _slice_digest(home, offset + region_offset,
                                                  region_bytes):
                    raise MeshIrError("E_RELOCATION",
                                      "region digest does not match content",
                                      symbol_id=symbol_id)
                alias_key = (symbol_id, region_offset, region_bytes)
                if alias_key in seen_regions and \
                        seen_regions[alias_key] != region_digest:
                    raise MeshIrError("E_RELOCATION",
                                      "conflicting alias for one region",
                                      symbol_id=symbol_id)
                seen_regions[alias_key] = region_digest
                previous_range = region_range
                regions.append(WeightRegion(
                    region_offset=region_offset, bytes=region_bytes,
                    resolved_content_digest=region_digest))
            symbols.append(WeightSymbolBinding(
                symbol_id=symbol_id,
                weight_home_id=home.weight_home_id,
                home_offset=offset,
                bytes=byte_count,
                flags=int(record["flags"]),
                resolved_content_digest=declared,
                read_only_alias_group=int(record["read_only_alias_group"]),
                regions=tuple(regions),
            ))
        programs.append(ProgramWeightBindings(
            program_id=int(entry["program_id"]),
            program_semantic_digest=semantic,
            symbols=tuple(symbols),
        ))
    if not programs:
        raise MeshIrError("E_RELOCATION",
                          "registry does not cover the loaded program")
    digest = _projection_digest(document, "program_weight_registry_digest")
    if document["program_weight_registry_digest"] != digest:
        raise MeshIrError("E_RELOCATION",
                          "program_weight_registry_digest projection mismatch")
    return ProgramWeightRegistry(image=image, programs=tuple(programs),
                                 digest=digest, document=document)


def region_digest(registry: ProgramWeightRegistry, program,
                  symbol_id: int, offset: int, byte_count: int) -> bytes:
    binding = registry.symbol(bytes.fromhex(program.semantic_sha256()),
                              symbol_id)
    for region in binding.regions:
        if region.region_offset == offset and region.bytes == byte_count:
            return region.resolved_content_digest
    raise MeshIrError("E_RELOCATION",
                      "MoE weight region is not bound in the registry",
                      symbol_id=symbol_id)


def encode_tag_tuple(program_semantic_digest: bytes, symbol_id: int,
                     offset: int, byte_count: int,
                     resolved_content_digest: bytes) -> bytes:
    return (
        bytes(program_semantic_digest) +
        struct.pack("<I", symbol_id) +
        struct.pack("<Q", offset) +
        struct.pack("<Q", byte_count) +
        bytes(resolved_content_digest)
    )


def weight_region_order(program) -> tuple:
    if not program.required_features & A.DYNAMIC_MOE_V1:
        return ()
    return tuple(sorted({(expert.weight_symbol_id, expert.weight_region_offset,
                          expert.weight_bytes)
                         for expert in program.moe_expert_specs}))


def weight_tag_index_map(program) -> dict:
    """Cacheable tag index of every expert, keyed by (layer, expert) because
    an expert id is only unique inside its layer."""
    order = weight_region_order(program)
    index_of = {region: index for index, region in enumerate(order)}
    return {(expert.layer_id, expert.expert_id):
            index_of[(expert.weight_symbol_id, expert.weight_region_offset,
                      expert.weight_bytes)]
            for expert in program.moe_expert_specs}


def weight_tag_manifest(program, registry: ProgramWeightRegistry) -> tuple:
    semantic = bytes.fromhex(program.semantic_sha256())
    manifest = []
    for index, (symbol_id, offset, byte_count) in enumerate(
            weight_region_order(program)):
        digest = region_digest(registry, program, symbol_id, offset,
                               byte_count)
        manifest.append((index, encode_tag_tuple(semantic, symbol_id, offset,
                                                 byte_count, digest)))
    return tuple(manifest)


def tag_manifest_bytes(manifest) -> bytes:
    body = bytearray(TAG_MANIFEST_MAGIC)
    body += struct.pack("<I", len(manifest))
    for index, tuple_bytes in manifest:
        body += struct.pack("<I", index)
        body += tuple_bytes
    return bytes(body)


def load_gate5_weight_artifacts(program_path, image_path, registry_path):
    program = decode_program(Path(program_path).read_bytes())
    image = load_model_weight_image(image_path)
    registry = load_program_weight_registry(registry_path, image, program)
    return program, image, registry
