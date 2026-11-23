import json
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import ValidationError
from .models import AccessUpdate, KnowledgeError, KnowledgeRequest, Metadata
from .extract import MAX_BYTES

router = APIRouter(prefix="/api/v1")


def service(request: Request):
    # Local single-user management. Reject browser cross-origin write requests.
    from backend.app.pairing.store import permitted_browser_origin
    if request.method != "GET" and not permitted_browser_origin(request):
        raise KnowledgeError("origin_rejected", "Cross-origin document operations are not permitted.", 403)
    if request.app.state.knowledge is None:
        from backend.app.core.config import PROJECT_ROOT, settings
        from .service import KnowledgeService
        from .embeddings import OllamaEmbeddings
        from .grounding import EvidenceSelector
        request.app.state.knowledge = KnowledgeService(PROJECT_ROOT, OllamaEmbeddings(settings.ollama_url), EvidenceSelector(settings.model, settings.ollama_url, settings.keep_alive))
    return request.app.state.knowledge


@router.get("/documents")
async def list_documents(svc=Depends(service)):
    return {"documents": svc.store.documents()}


async def read_upload(file, metadata):
    try:
        parsed = Metadata.model_validate_json(metadata)
    except ValidationError:
        raise KnowledgeError("invalid_metadata", "Supply valid title, version, scope, procedure identifiers and authorization metadata.", 422) from None
    raw = await file.read(MAX_BYTES + 1)
    return raw, parsed


def _audited(request, action, document_id, detail=None):
    """Stage "usuarios" 5: every manual change in the audit log (who, what, when); never blocks the change."""
    from backend.app.users.audit_web import record_after
    record_after(request, action, "document", document_id, detail)


def _document_id(result, fallback=None):
    return (result.get("id") if isinstance(result, dict) else None) or fallback


@router.post("/documents", status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...), metadata: str = Form(...), svc=Depends(service)):
    raw, parsed = await read_upload(file, metadata)
    result = await svc.ingest(file.filename or "document", raw, parsed)
    _audited(request, "document.upload", _document_id(result), {"title": parsed.title, "version": parsed.version})
    return result


@router.put("/documents/{document_id}")
async def update_document(document_id: str, request: Request, file: UploadFile = File(...), metadata: str = Form(...), svc=Depends(service)):
    raw, parsed = await read_upload(file, metadata)
    result = await svc.ingest(file.filename or "document", raw, parsed, document_id)
    _audited(request, "document.replace", document_id, {"title": parsed.title, "version": parsed.version})
    return result


@router.patch("/documents/{document_id}/authorization")
async def authorize_document(document_id: str, update: AccessUpdate, request: Request, svc=Depends(service)):
    result = await svc.access(document_id, authorized=update.authorized)
    _audited(request, "document.authorize", document_id, {"authorized": update.authorized})
    return result


@router.post("/documents/{document_id}/withdraw")
async def withdraw_document(document_id: str, request: Request, svc=Depends(service)):
    result = await svc.access(document_id, withdrawn=True)
    _audited(request, "document.withdraw", document_id)
    return result


@router.delete("/documents/{document_id}")
async def remove_from_index(document_id: str, request: Request, svc=Depends(service)):
    # Logical removal only: originals and prior revisions are retained.
    result = await svc.access(document_id, withdrawn=True)
    _audited(request, "document.delete", document_id)
    return result


@router.post("/documents/{document_id}/reindex")
async def reindex_document(document_id: str, request: Request, svc=Depends(service)):
    result = await svc.reindex(document_id)
    _audited(request, "document.reindex", document_id)
    return result


@router.post("/knowledge/chat")
async def knowledge_chat(payload: KnowledgeRequest, request: Request, svc=Depends(service)):
    from backend.app.voice.service import release_whisper_for_chat
    await release_whisper_for_chat(request)
    return await svc.query(payload)


@router.get("/knowledge/sources/{citation_id}")
async def source(citation_id: str, svc=Depends(service)):
    async with svc.lock:
        row = svc.store.evidence(citation_id)
        if not row:
            raise KnowledgeError("source_unavailable", "This source is withdrawn, unauthorized, superseded or unknown.", 404)
        return svc.source(row)


@router.get("/knowledge/status")
async def status(svc=Depends(service)):
    try:
        digest = await svc.embeddings.fingerprint()
    except KnowledgeError:
        digest = None
    return {"embedding_model": svc.embeddings.model, "embedding_available": bool(digest),
            "embedding_device": "cpu", "eligible_documents": len(svc.store.documents(True)),
            "answer_mode": "validated_excerpts", "multiuser_authorization": False}
