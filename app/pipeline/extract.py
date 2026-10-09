"""组装实际已渲染的抽取输入；调用供应商前先保留完整内容。"""

from app.domain.extraction import ExtractionRequest, GenerationParameter
from app.domain.ports import ExtractionStore
from app.domain.source_document import SourceDocument
from app.llm.extraction_contract import canonical_schema_bytes, validate_rendered_body


def build_extraction_request(
    source: SourceDocument,
    archive: ExtractionStore,
    *,
    requested_model: str,
    rendered_body: bytes,
    parameters: tuple[GenerationParameter, ...] = (),
) -> ExtractionRequest:
    validate_rendered_body(requested_model, parameters, rendered_body)
    return ExtractionRequest(
        source_recipe_id=source.recipe_id,
        source=archive.put(source.model_dump_json().encode(), "application/json"),
        rendered_request=archive.put(rendered_body, "application/json"),
        output_schema=archive.put(canonical_schema_bytes(), "application/schema+json"),
        requested_model=requested_model,
        generation_parameters=parameters,
    )
