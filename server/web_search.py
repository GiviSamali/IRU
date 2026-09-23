"""Yandex Search API v2 backend for the existing web_search tool contract."""
import asyncio
import base64
import binascii
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx

SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
MAX_XML_BYTES = 2 * 1024 * 1024


def _text(element) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def parse_search_response(data: object, max_results: int) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("rawData"), str) or not data["rawData"]:
        return {"error": "Yandex Search API: отсутствует rawData в JSON-ответе"}
    raw = data["rawData"]
    if len(raw) > (MAX_XML_BYTES + 2) // 3 * 4:
        return {"error": "Yandex Search API: ответ слишком большой"}
    try:
        xml = base64.b64decode(raw, validate=True).decode("utf-8-sig")
    except (ValueError, binascii.Error, UnicodeError):
        return {"error": "Yandex Search API: некорректный Base64 или кодировка XML"}
    # Search responses need no DTD/entity declarations. Do not expand external data.
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I):
        return {"error": "Yandex Search API: недопустимые объявления XML"}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {"error": "Yandex Search API: некорректный XML"}
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    if root.tag != "yandexsearch" or root.find("response") is None:
        return {"error": "Yandex Search API: неизвестная структура XML"}
    error = root.find(".//response/error")
    if error is not None:
        if error.get("code") == "15":
            return {"answer": None, "results": []}
        return {"error": "Yandex Search API: поисковый сервис вернул ошибку в XML"}
    results = []
    for doc in root.findall(".//response//doc"):
        url = _text(doc.find("url"))
        domain = _text(doc.find("domain"))
        if not url and domain:
            url = "https://" + domain
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
        except ValueError:
            continue
        passages = " ".join(filter(None, (_text(p) for p in doc.findall(".//passage"))))
        content = passages or _text(doc.find("headline")) or _text(doc.find("snippet"))
        results.append({"title": (_text(doc.find("title")) or domain or parsed.hostname)[:300],
                        "url": url, "content": content[:800]})
        if len(results) >= max_results:
            break
    return {"answer": None, "results": results}


async def run_web_search(query: str, max_results: int = 5) -> dict:
    query = query.strip()
    if not query:
        return {"error": "Пустой запрос"}
    if len(query) > 400:
        return {"error": "Yandex Search API: запрос должен содержать не более 400 символов"}
    key = os.environ.get("YANDEX_SEARCH_API_KEY", "").strip()
    folder_id = os.environ.get("YANDEX_FOLDER_ID", "").strip()
    if not key:
        return {"error": "YANDEX_SEARCH_API_KEY не настроен в окружении сервера"}
    if not folder_id:
        return {"error": "YANDEX_FOLDER_ID не настроен в окружении сервера"}
    limit = max(1, min(int(max_results or 5), 10))
    body = {"folderId": folder_id,
            "query": {"queryText": query, "searchType": "SEARCH_TYPE_RU"},
            "groupSpec": {"groupMode": "GROUP_MODE_FLAT", "groupsOnPage": str(limit), "docsInGroup": "1"},
            "maxPassages": "4", "responseFormat": "FORMAT_XML"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for attempt in range(2):
                try:
                    response = await client.post(SEARCH_URL, headers={"Authorization": f"Api-Key {key}"}, json=body)
                except httpx.RequestError:
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    return {"error": "Yandex Search API: сетевая ошибка или таймаут. Попробуйте позже"}
                status = response.status_code
                if status >= 500 and attempt == 0:
                    await asyncio.sleep(2)
                    continue
                if status != 200:
                    reason = {401: "проверьте API key", 403: "проверьте права и folder ID",
                              429: "лимит запросов, попробуйте позже"}.get(status, "поиск временно недоступен")
                    return {"error": f"Yandex Search API: HTTP {status}, {reason}"}
                try:
                    data = response.json()
                except ValueError:
                    return {"error": "Yandex Search API: некорректный JSON"}
                return parse_search_response(data, limit)
    except httpx.RequestError:
        return {"error": "Yandex Search API: сетевая ошибка или таймаут. Попробуйте позже"}
