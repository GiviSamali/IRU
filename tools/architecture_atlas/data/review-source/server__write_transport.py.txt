"""One logical write over the existing, sequential device command callback."""
import hashlib
import uuid


async def write_in_chunks(send, params):
    content = params["content"]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    chunks = [content[i:i + 4000] for i in range(0, len(content), 4000)]
    operation = uuid.uuid4().hex
    receipts = []
    for index, chunk in enumerate(chunks):
        part = {**params, "content": chunk, "append": index > 0,
                "_write_transaction": {"id": operation, "index": index, "count": len(chunks),
                    "sha256": digest, "append": bool(params.get("append"))}}
        result = await send(part)
        if not isinstance(result, dict):
            result = {"status": "unknown", "error": "write_result_unconfirmed"}
        if result.get("error") or result.get("status") not in {"staged", "ok"}:
            known_not_committed = result.get("committed") is False or result.get("dispatched") is False
            return {**result, "chunks_confirmed": len(receipts), "chunks_total": len(chunks),
                    "status":result.get("status", "error") if known_not_committed else "unknown",
                    "committed":False if known_not_committed else None}
        if (result.get("operation_id") != operation or result.get("chunk_index") != index
                or result.get("chunk_sha256") != hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                or (index < len(chunks) - 1 and result.get("status") != "staged")):
            return {"status": "unknown", "error": "write_chunk_receipt_mismatch",
                    "chunks_confirmed": len(receipts), "committed": None}
        receipts.append({"index": index, "sha256": result["chunk_sha256"]})
    if (result.get("committed") is not True or result.get("content_sha256") != digest
            or result.get("chars_written") != len(content) or result.get("verified") is not True):
        return {"status": "unknown", "error": "write_commit_unconfirmed", "committed": None}
    return {**result, "chunks_confirmed": len(receipts), "chunks_total": len(chunks),
            "chunk_receipts": receipts}
