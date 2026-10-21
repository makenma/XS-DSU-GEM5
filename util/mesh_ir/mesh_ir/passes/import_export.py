from __future__ import annotations

from mesh_ir.compat import ExportDto
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.torch_compat import ExportHandle, extract_dto


def import_export(handle: ExportHandle) -> ExportDto:
    dto = extract_dto(handle)
    input_positions = tuple(item.position for item in dto.inputs)
    output_positions = tuple(item.position for item in dto.outputs)
    if input_positions != tuple(range(len(input_positions))) or output_positions != tuple(range(len(output_positions))):
        raise MeshIrError("E_ABI_ORDER", "export signature positions are not dense")
    value_ids = {value.value_id for value in dto.values}
    if any(item.value_id not in value_ids for item in dto.inputs + dto.outputs):
        raise MeshIrError("E_ABI_BOUNDS", "export signature references an absent tensor")
    return dto
