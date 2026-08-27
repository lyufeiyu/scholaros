from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from contextlib import suppress
from typing import Any, Protocol

from scholaros.config import Settings
from scholaros.domain import Paper, SearchField, SearchResult, SourceFailure

MAX_SOURCE_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_ERROR_DETAIL_BYTES = 300
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", flags=re.I)
SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "using",
        "via",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)
AUTHOR_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
AUTHOR_FAMILY_PARTICLES = frozenset(
    {
        "ab",
        "af",
        "al",
        "ap",
        "ben",
        "bin",
        "binti",
        "da",
        "das",
        "de",
        "degla",
        "del",
        "della",
        "den",
        "der",
        "di",
        "do",
        "dos",
        "du",
        "el",
        "fitz",
        "ibn",
        "la",
        "le",
        "los",
        "san",
        "santa",
        "st",
        "ten",
        "ter",
        "van",
        "vanden",
        "vander",
        "von",
        "zu",
        "zum",
        "zur",
    }
)
VENUE_ALIAS_GROUPS = {
    "cvpr": (
        "cvpr",
        "computer vision and pattern recognition",
    ),
    "aaai": (
        "aaai",
        "aaai conference on artificial intelligence",
        "national conference on artificial intelligence",
    ),
    "neurips": (
        "neurips",
        "nips",
        "neural information processing systems",
    ),
    "icml": ("icml", "international conference on machine learning"),
    "iclr": ("iclr", "international conference on learning representations"),
    "iccv": ("iccv", "international conference on computer vision"),
    "eccv": ("eccv", "european conference on computer vision"),
    "ijcai": (
        "ijcai",
        "international joint conference on artificial intelligence",
    ),
    "acl": (
        "acl",
        "annual meeting of the association for computational linguistics",
    ),
    "emnlp": (
        "emnlp",
        "empirical methods in natural language processing",
    ),
    "kdd": ("kdd", "knowledge discovery and data mining"),
}
VENUE_RECALL_QUERIES = {
    "cvpr": "Computer Vision and Pattern Recognition",
    "aaai": "AAAI Conference on Artificial Intelligence",
    "neurips": "Neural Information Processing Systems",
    "icml": "International Conference on Machine Learning",
    "iclr": "International Conference on Learning Representations",
    "iccv": "International Conference on Computer Vision",
    "eccv": "European Conference on Computer Vision",
    "ijcai": "International Joint Conference on Artificial Intelligence",
    "acl": "Association for Computational Linguistics",
    "emnlp": "Empirical Methods in Natural Language Processing",
    "kdd": "Knowledge Discovery and Data Mining",
}
MAX_SEMANTIC_AUTHOR_PAPERS_SCANNED = 1000
SEMANTIC_AUTHOR_PAGE_SIZE = 100
SEMANTIC_AUTHOR_MAX_CONCURRENCY = 5


class PaperSource(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]: ...


class SourceHttpError(RuntimeError):
    """保留上游 HTTP 状态，避免界面只能解析 HTML/JSON 错误字符串。"""

    def __init__(
        self,
        status_code: int,
        detail: str = "",
        retry_after_seconds: int | None = None,
    ):
        self.status_code = status_code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"HTTP {status_code}")


class SourcePayloadError(RuntimeError):
    """上游返回过大或与约定不符的数据。"""


class JsonHttpSource:
    name = "base"

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def available(self) -> bool:
        return True

    async def get_json(
        self,
        url: str,
        params: dict[str, str | int],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}",
            headers={"User-Agent": "ScholarOS/0.1 (academic metadata client)", **(headers or {})},
        )

        def load() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(
                    request, timeout=self.settings.request_timeout
                ) as response:
                    payload = response.read(MAX_SOURCE_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_SOURCE_RESPONSE_BYTES:
                        raise SourcePayloadError("来源响应超过安全大小限制")
                    body = json.loads(payload)
                    if not isinstance(body, dict):
                        raise SourcePayloadError("来源 JSON 根节点不是对象")
                    return body
            except urllib.error.HTTPError as exc:
                raise _translate_http_error(exc) from exc

        return await asyncio.to_thread(load)


class ArxivSource(JsonHttpSource):
    name = "arxiv"

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        if field == SearchField.DOI:
            raise ValueError("arXiv API 不支持 DOI 字段检索，已跳过")
        if field == SearchField.VENUE:
            raise ValueError(
                "arXiv journal_ref 不是规范期刊名，不支持严格期刊/会议检索，已跳过"
            )
        field_name = {
            SearchField.ALL: "all",
            SearchField.TITLE: "ti",
            SearchField.AUTHOR: "au",
        }[field]
        search_value = _normalize_search_value(query, field)
        if field != SearchField.ALL and len(_normalize_match_text(search_value).split()) > 1:
            search_value = f'"{search_value}"'
        params = urllib.parse.urlencode(
            {
                "search_query": f"{field_name}:{search_value}",
                "start": 0,
                "max_results": limit,
                "sortBy": "relevance",
            }
        )
        request = urllib.request.Request(
            f"https://export.arxiv.org/api/query?{params}",
            headers={"User-Agent": "ScholarOS/0.1 (academic metadata client)"},
        )

        def load() -> bytes:
            try:
                with urllib.request.urlopen(
                    request, timeout=self.settings.request_timeout
                ) as response:
                    payload = response.read(MAX_SOURCE_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_SOURCE_RESPONSE_BYTES:
                        raise SourcePayloadError("来源响应超过安全大小限制")
                    return payload
            except urllib.error.HTTPError as exc:
                raise _translate_http_error(exc) from exc

        root = ET.fromstring(await asyncio.to_thread(load))
        atom = "{http://www.w3.org/2005/Atom}"
        arxiv = "{http://arxiv.org/schemas/atom}"
        papers = []
        for entry in root.findall(f"{atom}entry"):
            landing_url = _text(entry, f"{atom}id")
            external_id = landing_url.rsplit("/", 1)[-1] if landing_url else ""
            links = {
                item.attrib.get("title") or item.attrib.get("rel"): item.attrib.get("href")
                for item in entry.findall(f"{atom}link")
            }
            papers.append(
                Paper(
                    title=_clean(_text(entry, f"{atom}title")),
                    authors=[
                        _clean(_text(author, f"{atom}name"))
                        for author in entry.findall(f"{atom}author")
                    ],
                    year=_year(_text(entry, f"{atom}published")),
                    abstract=_clean(_text(entry, f"{atom}summary")),
                    sources=[self.name],
                    external_id=external_id,
                    doi=_text(entry, f"{arxiv}doi") or None,
                    venue=_text(entry, f"{arxiv}journal_ref") or "arXiv",
                    landing_url=landing_url,
                    pdf_url=links.get("pdf"),
                    is_open_access=True,
                )
            )
        return papers


class OpenAlexSource(JsonHttpSource):
    name = "openalex"

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        params: dict[str, str | int] = {"per-page": limit}
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        if field == SearchField.ALL:
            params["search"] = query
        elif field == SearchField.TITLE:
            params["filter"] = f"title.search:{query}"
        elif field == SearchField.DOI:
            params["filter"] = f"doi:https://doi.org/{_normalize_search_value(query, field)}"
        else:
            entity = "authors" if field == SearchField.AUTHOR else "sources"
            entity_params: dict[str, str | int] = {
                "search": query,
                "per-page": 25 if field == SearchField.AUTHOR else 10,
            }
            if self.settings.contact_email:
                entity_params["mailto"] = self.settings.contact_email
            entity_body = await self.get_json(
                f"https://api.openalex.org/{entity}", entity_params
            )
            entity_ids = []
            for item in entity_body.get("results", []):
                names = [
                    item.get("display_name") or "",
                    *(item.get("display_name_alternatives") or []),
                    *(item.get("alternate_titles") or []),
                    item.get("abbreviated_title") or "",
                ]
                matches = (
                    any(_author_name_matches(query, name) for name in names)
                    if field == SearchField.AUTHOR
                    else any(_venue_matches_query(query, name) for name in names)
                )
                if matches and item.get("id"):
                    entity_ids.append(item["id"].rsplit("/", 1)[-1])
            if not entity_ids:
                return []
            filter_name = (
                "authorships.author.id"
                if field == SearchField.AUTHOR
                else "primary_location.source.id"
            )
            params["filter"] = f"{filter_name}:{'|'.join(entity_ids)}"
        body = await self.get_json("https://api.openalex.org/works", params)
        return self._parse_works(body)

    async def search_author(
        self,
        query: str,
        limit: int,
        *,
        affiliation: str | None,
        topic: str | None,
        venue: str | None,
    ) -> list[Paper]:
        """把作者附加条件下推到 OpenAlex works，返回后仍由聚合层严格复核。"""

        author_ids = await self._matching_entity_ids(
            "authors", query, SearchField.AUTHOR, per_page=100
        )
        if not author_ids:
            return []
        filters = [f"authorships.author.id:{'|'.join(author_ids)}"]
        if affiliation:
            institution_ids = await self._matching_entity_ids(
                "institutions", affiliation, None, per_page=25
            )
            if not institution_ids:
                return []
            filters.append(f"authorships.institutions.id:{'|'.join(institution_ids)}")
        if venue:
            source_ids = await self._matching_entity_ids(
                "sources", venue, SearchField.VENUE, per_page=25
            )
            if not source_ids:
                return []
            filters.append(f"primary_location.source.id:{'|'.join(source_ids)}")
        params: dict[str, str | int] = {
            "per-page": limit,
            "filter": ",".join(filters),
        }
        if topic:
            params["search"] = topic
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        body = await self.get_json("https://api.openalex.org/works", params)
        return self._parse_works(body)

    async def _matching_entity_ids(
        self,
        entity: str,
        query: str,
        field: SearchField | None,
        *,
        per_page: int,
    ) -> list[str]:
        params: dict[str, str | int] = {"search": query, "per-page": per_page}
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        body = await self.get_json(f"https://api.openalex.org/{entity}", params)
        entity_ids: list[str] = []
        for item in body.get("results", []):
            names = [
                item.get("display_name") or "",
                *(item.get("display_name_alternatives") or []),
                *(item.get("alternate_titles") or []),
                item.get("abbreviated_title") or "",
            ]
            if field == SearchField.AUTHOR:
                matches = any(_author_name_matches(query, name) for name in names)
            elif field == SearchField.VENUE:
                matches = any(_venue_matches_query(query, name) for name in names)
            else:
                matches = any(_affiliation_matches(query, name) for name in names)
            if matches and item.get("id"):
                entity_ids.append(item["id"].rsplit("/", 1)[-1])
        return entity_ids

    def _parse_works(self, body: dict[str, Any]) -> list[Paper]:
        papers = []
        for item in body.get("results", []):
            open_access = item.get("open_access") or {}
            best_location = item.get("best_oa_location") or {}
            primary_location = item.get("primary_location") or {}
            source = primary_location.get("source") or {}
            ids = item.get("ids") or {}
            authorships = item.get("authorships", [])
            author_affiliations: dict[str, list[str]] = {}
            for authorship in authorships:
                name = authorship.get("author", {}).get("display_name") or ""
                affiliations = [
                    institution.get("display_name", "")
                    for institution in authorship.get("institutions", [])
                    if institution.get("display_name")
                ]
                _merge_author_affiliations(author_affiliations, name, affiliations)
            papers.append(
                Paper(
                    title=_clean(item.get("title") or ""),
                    authors=[
                        authorship.get("author", {}).get("display_name", "")
                        for authorship in authorships
                        if authorship.get("author", {}).get("display_name")
                    ],
                    year=item.get("publication_year"),
                    abstract=_inverted_abstract(item.get("abstract_inverted_index")),
                    sources=[self.name],
                    external_id=item.get("id", "").rsplit("/", 1)[-1],
                    doi=_normalize_doi(ids.get("doi")),
                    venue=source.get("display_name"),
                    landing_url=primary_location.get("landing_page_url") or item.get("id"),
                    pdf_url=best_location.get("pdf_url"),
                    is_open_access=bool(open_access.get("is_oa")),
                    citation_count=item.get("cited_by_count"),
                    author_affiliations=author_affiliations,
                )
            )
        return papers


class CrossrefSource(JsonHttpSource):
    name = "crossref"

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        if field == SearchField.DOI:
            return await self._search_doi(query)
        body = await self.get_json(
            "https://api.crossref.org/v1/works", self._params(query, limit, field)
        )
        return self._parse_items(body)

    async def _search_doi(self, query: str) -> list[Paper]:
        doi = _normalize_search_value(query, SearchField.DOI)
        params = {"mailto": self.settings.contact_email} if self.settings.contact_email else {}
        body = await self.get_json(
            f"https://api.crossref.org/v1/works/{urllib.parse.quote(doi, safe='')}",
            params,
        )
        item = body.get("message") or {}
        return self._parse_items({"message": {"items": [item]}}) if item else []

    async def search_author(
        self,
        query: str,
        limit: int,
        *,
        affiliation: str | None,
        topic: str | None,
        venue: str | None,
    ) -> list[Paper]:
        params = self._author_filter_params(
            query,
            limit,
            affiliation=affiliation,
            topic=topic,
            venue=venue,
        )
        body = await self.get_json("https://api.crossref.org/v1/works", params)
        return self._parse_items(body)

    def _author_filter_params(
        self,
        query: str,
        limit: int,
        *,
        affiliation: str | None,
        topic: str | None,
        venue: str | None,
    ) -> dict[str, str | int]:
        params = self._params(query, limit, SearchField.AUTHOR)
        if affiliation:
            params["query.affiliation"] = affiliation
        if topic:
            params["query.bibliographic"] = topic
        if venue:
            identity = _venue_identity(venue)
            params["query.container-title"] = (
                VENUE_RECALL_QUERIES[identity] if identity else venue
            )
        return params

    def _params(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> dict[str, str | int]:
        if field == SearchField.DOI:
            raise ValueError("DOI 检索必须使用 Crossref 精确标识符接口")
        params: dict[str, str | int] = {
            "rows": limit,
            "select": "DOI,title,author,published,abstract,container-title,URL,is-referenced-by-count",
        }
        if field == SearchField.VENUE:
            venue_identity = _venue_identity(query)
            if venue_identity:
                params["query.container-title"] = VENUE_RECALL_QUERIES[venue_identity]
            else:
                params["filter"] = (
                    f"container-title:{_normalize_search_value(query, field)}"
                )
        else:
            query_field = {
                SearchField.ALL: "query.bibliographic",
                SearchField.TITLE: "query.title",
                SearchField.AUTHOR: "query.author",
            }[field]
            params[query_field] = _normalize_search_value(query, field)
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        return params

    def _parse_items(self, body: dict[str, Any]) -> list[Paper]:
        papers = []
        for item in body.get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            date_parts = (item.get("published") or {}).get("date-parts") or [[]]
            authors = [
                _clean(f"{author.get('given', '')} {author.get('family', '')}")
                for author in item.get("author", [])
            ]
            author_affiliations: dict[str, list[str]] = {}
            for name, author in zip(authors, item.get("author", []), strict=True):
                affiliations = [
                    affiliation.get("name", "")
                    for affiliation in author.get("affiliation", [])
                    if affiliation.get("name")
                ]
                _merge_author_affiliations(author_affiliations, name, affiliations)
            papers.append(
                Paper(
                    title=_clean(title),
                    authors=authors,
                    year=date_parts[0][0] if date_parts and date_parts[0] else None,
                    abstract=_strip_tags(item.get("abstract") or ""),
                    sources=[self.name],
                    external_id=item.get("DOI", ""),
                    doi=item.get("DOI"),
                    venue=(item.get("container-title") or [None])[0],
                    landing_url=item.get("URL"),
                    # Crossref 的 TDM PDF 链接不等同于开放获取；保守地只保留落地页。
                    pdf_url=None,
                    is_open_access=False,
                    citation_count=item.get("is-referenced-by-count"),
                    author_affiliations=author_affiliations,
                )
            )
        return papers


class AcmMetadataSource(CrossrefSource):
    """通过 Crossref 的 ACM DOI 前缀检索书目；不抓取或绕过 ACM DL。"""

    name = "acm"

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        if field == SearchField.DOI:
            doi = _normalize_search_value(query, field)
            if not doi.lower().startswith("10.1145/"):
                return []
            return await self._search_doi(doi)
        params = self._params(query, limit, field)
        current_filter = params.get("filter")
        params["filter"] = (
            f"{current_filter},prefix:10.1145" if current_filter else "prefix:10.1145"
        )
        body = await self.get_json("https://api.crossref.org/v1/works", params)
        papers = self._parse_items(body)
        return papers

    async def search_author(
        self,
        query: str,
        limit: int,
        *,
        affiliation: str | None,
        topic: str | None,
        venue: str | None,
    ) -> list[Paper]:
        params = self._author_filter_params(
            query,
            limit,
            affiliation=affiliation,
            topic=topic,
            venue=venue,
        )
        current_filter = params.get("filter")
        params["filter"] = (
            f"{current_filter},prefix:10.1145" if current_filter else "prefix:10.1145"
        )
        body = await self.get_json("https://api.crossref.org/v1/works", params)
        return self._parse_items(body)


class SemanticScholarSource(JsonHttpSource):
    name = "semantic_scholar"

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        if field == SearchField.TITLE:
            raise ValueError(
                "Semantic Scholar 当前适配器不支持该结构化字段检索，已跳过"
            )
        headers = {}
        if self.settings.semantic_scholar_api_key:
            headers["x-api-key"] = self.settings.semantic_scholar_api_key
        fields = (
            "paperId,title,authors,year,abstract,venue,url,externalIds,"
            "citationCount,openAccessPdf,isOpenAccess"
        )
        if field == SearchField.AUTHOR:
            return await self.search_author(
                query,
                limit,
                affiliation=None,
                topic=None,
                venue=None,
            )
        if field == SearchField.DOI:
            doi = urllib.parse.quote(_normalize_search_value(query, field), safe="")
            body = await self.get_json(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                {"fields": fields},
                headers,
            )
            items = [body] if body.get("paperId") else []
        elif field == SearchField.VENUE:
            body = await self.get_json(
                "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
                {"venue": query, "limit": limit, "fields": fields},
                headers,
            )
            items = body.get("data", [])
        else:
            body = await self.get_json(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                {"query": query, "limit": limit, "fields": fields},
                headers,
            )
            items = body.get("data", [])
        return self._parse_items(items)

    async def search_author(
        self,
        query: str,
        limit: int,
        *,
        affiliation: str | None,
        topic: str | None,
        venue: str | None,
    ) -> list[Paper]:
        headers = {}
        if self.settings.semantic_scholar_api_key:
            headers["x-api-key"] = self.settings.semantic_scholar_api_key
        paper_fields = (
            "paperId,title,authors,year,abstract,venue,url,externalIds,"
            "citationCount,openAccessPdf,isOpenAccess"
        )
        body = await self.get_json(
            "https://api.semanticscholar.org/graph/v1/author/search",
            {
                "query": query,
                "limit": min(100, max(20, limit)),
                "fields": "name,affiliations",
            },
            headers,
        )
        candidates = [
            author
            for author in body.get("data", [])
            if author.get("authorId")
            and _author_name_matches(query, author.get("name") or "")
            and (
                not affiliation
                or any(
                    _affiliation_matches(affiliation, item)
                    for item in author.get("affiliations", [])
                    if isinstance(item, str)
                )
            )
        ]
        if not candidates:
            return []
        base_budget, extra_budget = divmod(
            MAX_SEMANTIC_AUTHOR_PAPERS_SCANNED, len(candidates)
        )
        base_result_limit, extra_results = divmod(limit, len(candidates))
        semaphore = asyncio.Semaphore(SEMANTIC_AUTHOR_MAX_CONCURRENCY)

        async def load_candidate(index: int, author: dict[str, Any]) -> list[Paper]:
            async with semaphore:
                return await self._load_author_papers(
                    str(author["authorId"]),
                    paper_fields,
                    headers,
                    topic=topic,
                    venue=venue,
                    result_limit=max(
                        1, base_result_limit + (1 if index < extra_results else 0)
                    ),
                    scan_budget=base_budget + (1 if index < extra_budget else 0),
                )

        tasks = [
            asyncio.create_task(load_candidate(index, author))
            for index, author in enumerate(candidates)
        ]
        try:
            values = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        papers: list[Paper] = []
        for author, value in zip(candidates, values, strict=True):
            name = author.get("name") or ""
            affiliations = [
                affiliation
                for affiliation in author.get("affiliations", [])
                if isinstance(affiliation, str) and affiliation.strip()
            ]
            if name and affiliations:
                for paper in value:
                    paper.author_affiliations[name] = affiliations
            papers.extend(value)
        return papers

    async def _load_author_papers(
        self,
        author_id: str,
        paper_fields: str,
        headers: dict[str, str],
        *,
        topic: str | None,
        venue: str | None,
        result_limit: int,
        scan_budget: int,
    ) -> list[Paper]:
        """分页扫描作者论文；附加条件在每页即刻过滤，避免首批截断漏检。"""

        offset = 0
        scanned = 0
        matched: list[Paper] = []
        url = (
            "https://api.semanticscholar.org/graph/v1/author/"
            f"{urllib.parse.quote(author_id, safe='')}/papers"
        )
        while scanned < scan_budget and len(matched) < result_limit:
            page_size = min(SEMANTIC_AUTHOR_PAGE_SIZE, scan_budget - scanned)
            value = await self.get_json(
                url,
                {"limit": page_size, "offset": offset, "fields": paper_fields},
                headers,
            )
            items = value.get("data", [])
            if not isinstance(items, list) or not items:
                break
            scanned += len(items)
            matched.extend(
                paper
                for paper in self._parse_items(items)
                if (not topic or _topic_matches(topic, paper))
                and (not venue or _venue_matches_query(venue, paper.venue or ""))
            )
            next_offset = value.get("next")
            if next_offset is None:
                break
            try:
                next_offset = int(next_offset)
            except (TypeError, ValueError):
                raise SourcePayloadError("Semantic Scholar 作者论文分页游标无效") from None
            if next_offset <= offset:
                raise SourcePayloadError("Semantic Scholar 作者论文分页游标未前进")
            offset = next_offset
        return matched[:result_limit]

    def _parse_items(self, items: Sequence[dict[str, Any]]) -> list[Paper]:
        papers = []
        for item in items:
            ids = item.get("externalIds") or {}
            open_pdf = item.get("openAccessPdf") or {}
            papers.append(
                Paper(
                    title=_clean(item.get("title") or ""),
                    authors=[author.get("name", "") for author in item.get("authors", [])],
                    year=item.get("year"),
                    abstract=_clean(item.get("abstract") or ""),
                    sources=[self.name],
                    external_id=item.get("paperId", ""),
                    doi=ids.get("DOI"),
                    venue=item.get("venue"),
                    landing_url=item.get("url"),
                    pdf_url=open_pdf.get("url"),
                    is_open_access=bool(item.get("isOpenAccess")),
                    citation_count=item.get("citationCount"),
                )
            )
        return papers


class DblpSource(JsonHttpSource):
    name = "dblp"

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        if field in {SearchField.DOI, SearchField.VENUE}:
            label = "DOI" if field == SearchField.DOI else "期刊/会议名称"
            raise ValueError(f"DBLP publication API 不能按 {label} 精确检索论文，已跳过")
        dblp_query = _normalize_search_value(query, field)
        if field == SearchField.AUTHOR:
            dblp_query = f"author:{'_'.join(dblp_query.split())}:"
        body = await self.get_json(
            "https://dblp.org/search/publ/api",
            {"q": dblp_query, "h": limit, "format": "json"},
        )
        raw_hits = body.get("result", {}).get("hits", {}).get("hit", [])
        papers = []
        for hit in raw_hits:
            item = hit.get("info") or {}
            raw_authors = (item.get("authors") or {}).get("author", [])
            if isinstance(raw_authors, dict):
                raw_authors = [raw_authors]
            authors = [author.get("text", "") for author in raw_authors]
            papers.append(
                Paper(
                    title=_strip_tags(item.get("title") or ""),
                    authors=authors,
                    year=_int_or_none(item.get("year")),
                    abstract="",
                    sources=[self.name],
                    external_id=item.get("key", ""),
                    doi=item.get("doi"),
                    venue=item.get("venue"),
                    landing_url=item.get("url"),
                )
            )
        return papers


class IeeeSource(JsonHttpSource):
    name = "ieee"

    @property
    def available(self) -> bool:
        return bool(self.settings.ieee_xplore_api_key)

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        if not self.settings.ieee_xplore_api_key:
            return []
        params = self._params(query, limit, field)
        body = await self.get_json(
            "https://ieeexploreapi.ieee.org/api/v1/search/articles", params
        )
        return self._parse_articles(body)

    async def search_author(
        self,
        query: str,
        limit: int,
        *,
        affiliation: str | None,
        topic: str | None,
        venue: str | None,
    ) -> list[Paper]:
        if not self.settings.ieee_xplore_api_key:
            return []
        params = self._params(query, limit, SearchField.AUTHOR)
        if affiliation:
            params["affiliation"] = affiliation
        if topic:
            params["querytext"] = topic
        if venue:
            identity = _venue_identity(venue)
            params["publication_title"] = (
                VENUE_RECALL_QUERIES[identity] if identity else venue
            )
        body = await self.get_json(
            "https://ieeexploreapi.ieee.org/api/v1/search/articles", params
        )
        return self._parse_articles(body)

    def _params(
        self, query: str, limit: int, field: SearchField
    ) -> dict[str, str | int]:
        query_field = {
            SearchField.ALL: "querytext",
            SearchField.TITLE: "article_title",
            SearchField.AUTHOR: "author",
            SearchField.DOI: "doi",
            SearchField.VENUE: "publication_title",
        }[field]
        query_value = _normalize_search_value(query, field)
        if field == SearchField.VENUE and (venue_identity := _venue_identity(query)):
            query_value = VENUE_RECALL_QUERIES[venue_identity]
        params: dict[str, str | int] = {
            "apikey": self.settings.ieee_xplore_api_key,
            query_field: query_value,
            "max_records": limit,
            "start_record": 1,
            "sort_order": "desc",
            "sort_field": "article_number",
        }
        return params

    def _parse_articles(self, body: dict[str, Any]) -> list[Paper]:
        papers = []
        for item in body.get("articles", []):
            author_items = item.get("authors", {}).get("authors", [])
            authors = [author.get("full_name", "") for author in author_items]
            author_affiliations: dict[str, list[str]] = {}
            for name, author in zip(authors, author_items, strict=True):
                _merge_author_affiliations(
                    author_affiliations,
                    name,
                    _ieee_author_affiliations(author.get("affiliation")),
                )
            pdf_url = item.get("pdf_url") if item.get("access_type") == "OPEN_ACCESS" else None
            papers.append(
                Paper(
                    title=_clean(item.get("title") or ""),
                    authors=authors,
                    year=_int_or_none(item.get("publication_year")),
                    abstract=_clean(item.get("abstract") or ""),
                    sources=[self.name],
                    external_id=str(item.get("article_number", "")),
                    doi=item.get("doi"),
                    venue=item.get("publication_title"),
                    landing_url=item.get("html_url"),
                    pdf_url=pdf_url,
                    is_open_access=bool(pdf_url),
                    citation_count=_int_or_none(item.get("citing_paper_count")),
                    author_affiliations=author_affiliations,
                )
            )
        return papers


class MemorySource:
    """测试、演示和私有索引可使用的内存论文源。"""

    name = "memory"
    available = True

    def __init__(self, papers: Sequence[Paper]):
        self.papers = list(papers)

    async def search(
        self, query: str, limit: int, field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        tokens = set(_normalize_title(query).split())
        ranked = sorted(
            self.papers,
            key=lambda paper: len(
                tokens & set(_normalize_title(_paper_field_text(paper, field)).split())
            ),
            reverse=True,
        )
        return ranked[:limit]


async def _search_source_with_author_filters(
    source: PaperSource,
    query: str,
    limit: int,
    field: SearchField,
    *,
    affiliation: str | None,
    topic: str | None,
    venue: str | None,
) -> list[Paper]:
    specialized = getattr(source, "search_author", None)
    if field == SearchField.AUTHOR and any((affiliation, topic, venue)) and callable(
        specialized
    ):
        return await specialized(
            query,
            limit,
            affiliation=affiliation,
            topic=topic,
            venue=venue,
        )
    return await source.search(query, limit, field)


class PaperSearchService:
    workflow_blocked_sources = frozenset({"ieee"})

    def __init__(self, sources: Iterable[PaperSource]):
        self.sources = {source.name: source for source in sources}

    @classmethod
    def default(cls, settings: Settings) -> PaperSearchService:
        return cls(
            [
                ArxivSource(settings),
                OpenAlexSource(settings),
                CrossrefSource(settings),
                AcmMetadataSource(settings),
                SemanticScholarSource(settings),
                DblpSource(settings),
                IeeeSource(settings),
            ]
        )

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": source.name,
                "available": source.available,
                "requires_key": source.name == "ieee",
                "workflow_eligible": source.name not in self.workflow_blocked_sources,
                "status": (
                    "API Key 已配置；官方激活状态将在首次检索时验证"
                    if source.name == "ieee" and source.available
                    else "API Key 未配置；检索时将自动跳过"
                    if source.name == "ieee"
                    else "可用"
                    if source.available
                    else "不可用"
                ),
                "access": (
                    "ACM 元数据（经 Crossref）"
                    if source.name == "acm"
                    else "IEEE 官方 API"
                    if source.name == "ieee"
                    else "开放元数据 API"
                ),
            }
            for source in self.sources.values()
        ]

    def workflow_sources(self) -> list[str]:
        """返回获准进入 AI 写作链路的来源；受限源仅用于独立检索。"""
        return [name for name in self.sources if name not in self.workflow_blocked_sources]

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        selected: Sequence[str] | None = None,
        field: SearchField | str = SearchField.ALL,
        author_affiliation: str | None = None,
        author_topic: str | None = None,
        author_venue: str | None = None,
    ) -> SearchResult:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        try:
            search_field = SearchField(field)
        except ValueError as exc:
            raise ValueError("检索字段只能是 all、title、author、doi 或 venue") from exc
        if search_field == SearchField.DOI and not _valid_normalized_doi(query):
            raise ValueError("DOI 检索值不能为空，且必须包含有效 DOI 标识符")
        author_filters = {
            "affiliation": _validate_optional_search_filter(
                author_affiliation, "作者学校/机构"
            ),
            "topic": _validate_optional_search_filter(author_topic, "作者主题关键词"),
            "venue": _validate_optional_search_filter(author_venue, "作者会议/期刊"),
        }
        if search_field != SearchField.AUTHOR and any(author_filters.values()):
            raise ValueError("学校/机构、主题和会议附加条件仅能用于作者检索")
        names = list(selected) if selected is not None else list(self.sources)
        chosen = [self.sources[name] for name in names if name in self.sources]
        unknown = [name for name in names if name not in self.sources]
        failures = [
            SourceFailure(
                _safe_source_name(name),
                "未知论文源",
                "请从来源状态列表中选择有效名称，并检查拼写。",
            )
            for name in unknown
        ]
        available = [source for source in chosen if source.available]
        failures.extend(
            SourceFailure(
                source.name,
                "IEEE API Key 未配置，已自动跳过"
                if source.name == "ieee"
                else "未配置所需凭据，已跳过",
                (
                    "在项目 .env 中设置 IEEE_XPLORE_API_KEY；若邮件状态仍为 waiting，"
                    "请等待 IEEE 激活后重启 ScholarOS。"
                    if source.name == "ieee"
                    else "配置该来源所需凭据并重启 ScholarOS，或取消该来源。"
                ),
            )
            for source in chosen
            if not source.available
        )
        if author_filters["affiliation"]:
            unverifiable = [
                source for source in available if source.name in {"arxiv", "dblp"}
            ]
            failures.extend(
                SourceFailure(
                    source.name,
                    "该来源不提供当前适配器可核验的作者—机构对应关系，已跳过",
                    "改用 OpenAlex、Crossref、Semantic Scholar、ACM 或已配置的 IEEE；"
                    "也可取消学校/机构条件，仅按姓名和其他条件检索。",
                )
                for source in unverifiable
            )
            available = [source for source in available if source not in unverifiable]
        if not available:
            return SearchResult([], failures)

        per_source = (
            100
            if search_field == SearchField.AUTHOR and any(author_filters.values())
            else min(100, max(10, math.ceil(limit / len(available)) + 8))
        )
        values = await asyncio.gather(
            *(
                _search_source_with_author_filters(
                    source,
                    query,
                    per_source,
                    search_field,
                    affiliation=author_filters["affiliation"],
                    topic=author_filters["topic"],
                    venue=author_filters["venue"],
                )
                for source in available
            ),
            return_exceptions=True,
        )
        papers = []
        for source, value in zip(available, values, strict=True):
            if isinstance(value, BaseException):
                failures.append(_describe_source_failure(source.name, value))
            else:
                papers.extend(value)
        matching_papers = [
            paper
            for paper in papers
            if _paper_matches_query(query, paper, search_field)
            and _paper_matches_author_filters(
                query,
                paper,
                affiliation=author_filters["affiliation"],
                topic=author_filters["topic"],
                venue=author_filters["venue"],
            )
        ]
        filtered_out = len(papers) - len(matching_papers)
        return SearchResult(
            self._rank_and_deduplicate(query, matching_papers, search_field)[:limit],
            failures,
            filtered_out,
        )

    async def search_many(
        self,
        queries: Sequence[str],
        *,
        limit: int = 20,
        selected: Sequence[str] | None = None,
        field: SearchField | str = SearchField.ALL,
    ) -> SearchResult:
        """分别召回互补主题后合并，避免把多个研究簇强制成一个过窄长查询。"""

        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        clean_queries = list(
            dict.fromkeys(query.strip() for query in queries if query.strip())
        )
        if not clean_queries:
            raise ValueError("至少需要一个非空检索词")
        per_query_limit = max(1, math.ceil(limit / len(clean_queries)) + 4)
        clusters: list[list[Paper]] = []
        failures: list[SourceFailure] = []
        filtered_out = 0
        active_sources = list(selected) if selected is not None else list(self.sources)
        for query in clean_queries:
            if not active_sources:
                break
            result = await self.search(
                query,
                limit=min(100, per_query_limit),
                selected=active_sources,
                field=field,
            )
            clusters.append(result.papers)
            failures.extend(result.failures)
            filtered_out += result.filtered_out
            stopped_sources = {
                failure.source
                for failure in result.failures
                if failure.retryable
                or failure.status_code in {401, 403, 429}
                or failure.reason == "未知论文源"
                or "已跳过" in failure.reason
            }
            active_sources = [
                name for name in active_sources if name not in stopped_sources
            ]
        unique_failures = {
            (
                failure.source,
                failure.reason,
                failure.status_code,
                failure.retry_after_seconds,
            ): failure
            for failure in failures
        }
        return SearchResult(
            self._round_robin_deduplicate(clusters, limit),
            list(unique_failures.values()),
            filtered_out,
        )

    @staticmethod
    def _round_robin_deduplicate(clusters: Sequence[Sequence[Paper]], limit: int) -> list[Paper]:
        """先为每个查询簇保留结果，再按轮次补齐，并在稳定顺序中去重。"""

        positions = [0] * len(clusters)
        selected: list[Paper] = []
        by_key: dict[str, Paper] = {}
        while len(selected) < limit:
            progressed = False
            for index, cluster in enumerate(clusters):
                if positions[index] >= len(cluster):
                    continue
                paper = cluster[positions[index]]
                positions[index] += 1
                progressed = True
                key = _paper_dedup_key(paper)
                if key is None:
                    continue
                current = by_key.get(key)
                if current is not None:
                    _merge_paper(current, paper)
                    continue
                by_key[key] = paper
                selected.append(paper)
                if len(selected) >= limit:
                    break
            if not progressed:
                break
        return selected

    @staticmethod
    def _rank_and_deduplicate(
        query: str, papers: list[Paper], field: SearchField = SearchField.ALL
    ) -> list[Paper]:
        query_tokens = _significant_tokens(query)
        deduplicated: dict[str, Paper] = {}
        for paper in papers:
            key = _paper_dedup_key(paper)
            if key is None:
                continue
            current = deduplicated.get(key)
            if current is None:
                deduplicated[key] = paper
                continue
            _merge_paper(current, paper)

        for paper in deduplicated.values():
            field_tokens = _significant_tokens(_paper_field_text(paper, field))
            abstract_tokens = _significant_tokens(paper.abstract)
            field_overlap = len(query_tokens & field_tokens) / max(1, len(query_tokens))
            abstract_overlap = len(query_tokens & abstract_tokens) / max(1, len(query_tokens))
            if field != SearchField.ALL:
                field_overlap = 1.0
                abstract_overlap = 0
            citations = min(0.2, math.log1p(paper.citation_count or 0) / 30)
            abstract_bonus = 0.05 if paper.abstract else 0
            source_bonus = min(0.15, 0.04 * len(paper.sources))
            paper.score = round(
                1.3 * field_overlap
                + 0.4 * abstract_overlap
                + citations
                + abstract_bonus
                + source_bonus,
                4,
            )
            paper.cite_key = _cite_key(paper)
        return sorted(deduplicated.values(), key=lambda paper: paper.score, reverse=True)


def _paper_dedup_key(paper: Paper) -> str | None:
    paper.landing_url = normalize_http_url(paper.landing_url)
    paper.pdf_url = normalize_http_url(paper.pdf_url)
    normalized_doi = _valid_normalized_doi(paper.doi)
    if normalized_doi:
        return f"doi:{normalized_doi.casefold()}"
    normalized_title = _normalize_match_text(paper.title)
    return f"title:{normalized_title}" if normalized_title else None


def _merge_paper(current: Paper, paper: Paper) -> None:
    current.sources = sorted(set(current.sources + paper.sources))
    current.abstract = max((current.abstract, paper.abstract), key=len)
    current.pdf_url = current.pdf_url or paper.pdf_url
    current.landing_url = current.landing_url or paper.landing_url
    current.is_open_access = current.is_open_access or paper.is_open_access
    current.citation_count = max(current.citation_count or 0, paper.citation_count or 0)
    current.score = max(current.score, paper.score)
    for author, affiliations in paper.author_affiliations.items():
        current.author_affiliations[author] = sorted(
            set(current.author_affiliations.get(author, []) + affiliations)
        )


def _describe_source_failure(source: str, error: BaseException) -> SourceFailure:
    """把不可信上游异常转换为可公开展示、可执行的诊断。"""

    status_code = _http_status_code(error)
    if status_code == 429:
        retry_after_seconds = _retry_after_seconds(error)
        return SourceFailure(
            source,
            "请求频率超过该来源限额（HTTP 429）",
            _rate_limit_suggestion(source, retry_after_seconds),
            retryable=True,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )
    if status_code in {401, 403}:
        return SourceFailure(
            source,
            f"身份验证或访问权限未通过（HTTP {status_code}）",
            _authorization_suggestion(source),
            status_code=status_code,
        )
    if status_code == 404:
        return SourceFailure(
            source,
            "该来源没有找到对应资源（HTTP 404）",
            "检查 DOI 或检索值是否完整；也可改用综合主题检索或其他来源交叉查找。",
            status_code=status_code,
        )
    if status_code is not None and status_code >= 500:
        return SourceFailure(
            source,
            f"来源服务暂时不可用（HTTP {status_code}）",
            _service_unavailable_suggestion(source),
            retryable=True,
            status_code=status_code,
        )
    if status_code is not None:
        return SourceFailure(
            source,
            f"来源拒绝了当前请求（HTTP {status_code}）",
            "检查检索值和字段是否符合该来源要求；仍失败时暂时取消该来源。",
            status_code=status_code,
        )
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or (
        isinstance(error, urllib.error.URLError)
        and isinstance(error.reason, (TimeoutError, asyncio.TimeoutError))
    ):
        return SourceFailure(
            source,
            "连接该来源超时",
            _network_suggestion(source),
            retryable=True,
        )
    if isinstance(error, (urllib.error.URLError, OSError)):
        return SourceFailure(
            source,
            "无法连接该来源（网络、DNS 或 TLS 错误）",
            _network_suggestion(source),
            retryable=True,
        )
    if isinstance(
        error,
        (
            json.JSONDecodeError,
            ET.ParseError,
            SourcePayloadError,
            AttributeError,
            KeyError,
            TypeError,
        ),
    ):
        return SourceFailure(
            source,
            "来源返回了无法解析或结构异常的数据",
            "上游响应可能临时异常；稍后重试，持续出现时请更新 ScholarOS 适配器。",
            retryable=True,
        )
    if isinstance(error, ValueError):
        reason = _safe_error_message(error)
        return SourceFailure(
            source,
            reason,
            "改用该来源支持的检索字段，或取消该来源并用其他来源完成检索。",
        )
    return SourceFailure(
        source,
        "来源适配器发生未分类错误",
        "保留其他来源结果；重试仍失败时，请记录来源与提示并检查 ScholarOS 更新。",
    )


def _http_status_code(error: BaseException) -> int | None:
    if isinstance(error, SourceHttpError):
        return error.status_code
    if isinstance(error, urllib.error.HTTPError):
        return error.code
    match = re.search(r"\bHTTP\s+([1-5]\d{2})\b", str(error), flags=re.I)
    return int(match.group(1)) if match else None


def _retry_after_seconds(error: BaseException) -> int | None:
    if isinstance(error, SourceHttpError):
        return error.retry_after_seconds
    if isinstance(error, urllib.error.HTTPError):
        value = error.headers.get("Retry-After") if error.headers else None
        return _parse_retry_after(value)
    return None


def _translate_http_error(error: urllib.error.HTTPError) -> SourceHttpError:
    try:
        detail = error.read(MAX_ERROR_DETAIL_BYTES).decode(
            "utf-8", errors="replace"
        )
    except OSError:
        detail = ""
    finally:
        with suppress(OSError):
            error.close()
    value = error.headers.get("Retry-After") if error.headers else None
    return SourceHttpError(error.code, detail, _parse_retry_after(value))


def _parse_retry_after(value: str | None) -> int | None:
    if not value or not value.strip().isdigit():
        return None
    seconds = int(value.strip())
    return seconds if 0 <= seconds <= 604_800 else None


def _safe_error_message(error: BaseException) -> str:
    message = _strip_tags(str(error))
    message = re.sub(r"[\[{].*", "", message).strip(" :,-")
    return message[:160] or error.__class__.__name__


def _safe_source_name(value: str) -> str:
    display = "".join(
        character for character in value if ord(character) >= 32 and ord(character) != 127
    ).strip()
    return display[:64] or "未知来源"


def _rate_limit_suggestion(source: str, retry_after_seconds: int | None) -> str:
    suggestions = {
        "semantic_scholar": (
            "在 https://www.semanticscholar.org/product/api#api-key-form 申请 Key，"
            "在项目 .env 设置 SEMANTIC_SCHOLAR_API_KEY，重启 ScholarOS；"
            "配置前请稍后重试或暂时取消该来源。"
        ),
        "crossref": (
            "在项目 .env 设置 SCHOLAROS_CONTACT_EMAIL 以使用 Crossref polite pool；"
            "随后重启，并降低检索频率或稍后重试。"
        ),
        "acm": (
            "ACM 当前经 Crossref 检索；在项目 .env 设置 SCHOLAROS_CONTACT_EMAIL，"
            "重启后再试，或暂时取消 ACM。"
        ),
        "dblp": "稍后重试并降低检索频率；频繁出现时暂时取消 DBLP。",
        "openalex": (
            "在项目 .env 设置 SCHOLAROS_CONTACT_EMAIL，重启后降低检索频率；"
            "仍受限时稍后重试。"
        ),
        "ieee": "等待 IEEE 配额窗口恢复；若持续出现，请在 API Portal 检查 Key 和配额。",
        "arxiv": "降低检索频率并稍后重试；当前检索可暂时取消 arXiv。",
    }
    suggestion = suggestions.get(
        source, "等待一段时间后重试；若持续受限，可暂时取消该来源。"
    )
    if retry_after_seconds is not None:
        return f"上游要求等待 {retry_after_seconds} 秒后再试；{suggestion}"
    return suggestion


def _authorization_suggestion(source: str) -> str:
    if source == "ieee":
        return (
            "检查项目 .env 中的 IEEE_XPLORE_API_KEY；若 IEEE 邮件状态仍为 waiting，"
            "请等待激活邮件，激活后重启 ScholarOS。"
        )
    if source == "semantic_scholar":
        return (
            "尚未配置时先申请 SEMANTIC_SCHOLAR_API_KEY；已配置时检查是否完整有效，"
            "修正项目 .env 后重启 ScholarOS。"
        )
    if source in {"crossref", "acm"}:
        prefix = "ACM 当前经 Crossref 检索；" if source == "acm" else ""
        return (
            f"{prefix}检查 SCHOLAROS_CONTACT_EMAIL，并按 Crossref 返回信息联系支持；"
            "不要反复请求。"
        )
    if source in {"arxiv", "dblp"}:
        return (
            f"{source} 公开接口不需要 API Key；停止连续请求并稍后重试，"
            "持续被拒绝时检查代理出口或该来源服务状态。"
        )
    if source == "openalex":
        return (
            "ScholarOS 当前使用 OpenAlex 公开接口；设置 SCHOLAROS_CONTACT_EMAIL 后重启，"
            "并检查代理出口或来源服务状态。"
        )
    return "检查该来源的密钥、账号状态和访问权限，修正后重启 ScholarOS。"


def _service_unavailable_suggestion(source: str) -> str:
    if source == "dblp":
        return (
            "这是 DBLP 服务端临时故障；稍后重试，或暂时取消 DBLP，"
            "继续使用 OpenAlex、Crossref、arXiv 等来源。"
        )
    if source == "acm":
        return (
            "ACM 当前经 Crossref 检索，实际是 Crossref 上游暂时故障；"
            "稍后重试，或暂时取消 ACM。"
        )
    if source == "crossref":
        return "Crossref 上游暂时故障；稍后重试，或先使用其他来源。"
    if source == "ieee":
        return "IEEE 上游暂时故障；稍后重试，并以 IEEE API Portal 状态为准。"
    return "该论文源上游暂时故障；稍后重试，或暂时取消它并继续使用其他来源。"


def _network_suggestion(source: str) -> str:
    if source == "acm":
        return (
            "ACM 当前经 Crossref 检索；检查网络/代理能否访问 api.crossref.org，"
            "或暂时取消 ACM。"
        )
    return f"检查网络、代理和系统时间能否正常访问 {source}，然后重试或暂时取消该来源。"


def search_match_policy(
    field: SearchField | str,
    *,
    author_affiliation: str | None = None,
    author_topic: str | None = None,
    author_venue: str | None = None,
) -> str:
    """返回对用户可见的聚合层匹配规则。"""

    search_field = SearchField(field)
    policy = {
        SearchField.ALL: "相关性过滤：短检索词须全部命中，较长主题须达到最低有效词项覆盖率。",
        SearchField.TITLE: (
            "标题严格匹配：规范化大小写、重音和一般标点后必须与完整标题一致；"
            "C++、C#、F# 等有语义的技术符号保留区分。"
        ),
        SearchField.AUTHOR: (
            "姓名严格匹配：姓氏和后缀必须完整一致；仅允许返回侧把名字缩写为首字母。"
        ),
        SearchField.DOI: "DOI 严格匹配：规范化 DOI 链接和前缀后必须完全一致。",
        SearchField.VENUE: (
            "期刊/会议严格匹配：普通名称规范化后完整一致；常见计算机顶会可识别"
            "简称、正式容器名和旧称，且不会混入 Workshop。"
        ),
    }[search_field]
    if search_field == SearchField.AUTHOR and any(
        (author_affiliation, author_topic, author_venue)
    ):
        policy += (
            " 附加条件按 AND 组合；学校/机构必须属于命中姓名的同一作者，"
            "主题须命中标题/摘要，会议须命中规范化会议身份。"
        )
    return policy


def build_google_scholar_query(
    query: str,
    field: SearchField | str,
    *,
    author_affiliation: str | None = None,
    author_topic: str | None = None,
    author_venue: str | None = None,
) -> str:
    """生成供用户手动核验的 Google Scholar 多条件检索词。"""

    search_field = SearchField(field)
    if search_field != SearchField.AUTHOR:
        return query.strip()
    values = [query, author_affiliation, author_topic, author_venue]
    return " ".join(f'"{value.strip()}"' for value in values if value and value.strip())


def paper_web_links(paper: Paper) -> list[tuple[str, str]]:
    """返回适合展示的论文主页/DOI 与开放 PDF HTTP(S) 链接。"""

    landing_url = normalize_http_url(paper.landing_url)
    valid_doi = _valid_normalized_doi(paper.doi)
    doi_url = (
        f"https://doi.org/{urllib.parse.quote(valid_doi, safe='/')}"
        if valid_doi
        else None
    )
    primary_url = landing_url or doi_url
    pdf_url = normalize_http_url(paper.pdf_url)
    links = [("网页", primary_url)] if primary_url else []
    if pdf_url and pdf_url != primary_url:
        links.append(("开放 PDF", pdf_url))
    return links


def normalize_http_url(value: str | None) -> str | None:
    """校验并规范化来自外部论文元数据的 HTTP(S) URL。"""

    if not value:
        return None
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return None
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _valid_hostname(ascii_hostname):
        return None
    host = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = f"{host}:{port}" if port is not None else host
    path = urllib.parse.quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query, safe="=&%/:?@!$'()*+,;~-._")
    fragment = urllib.parse.quote(parsed.fragment, safe="=&%/:?@!$'()*+,;~-._")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path, query, fragment))


def _valid_hostname(hostname: str) -> bool:
    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError:
            return False
        return True
    normalized = hostname.rstrip(".")
    if not normalized or len(normalized) > 253:
        return False
    labels = normalized.split(".")
    return all(
        len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )


def _text(element: ET.Element, path: str) -> str:
    child = element.find(path)
    return child.text.strip() if child is not None and child.text else ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _strip_tags(value: str) -> str:
    return _clean(re.sub(r"<[^>]+>", " ", value))


def _year(value: str) -> int | None:
    match = re.match(r"(\d{4})", value or "")
    return int(match.group(1)) if match else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ieee_author_affiliations(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _merge_author_affiliations(
    mapping: dict[str, list[str]], name: str, affiliations: Sequence[str]
) -> None:
    clean_name = name.strip()
    clean_affiliations = [value.strip() for value in affiliations if value.strip()]
    if clean_name and clean_affiliations:
        mapping[clean_name] = sorted(
            set(mapping.get(clean_name, []) + clean_affiliations)
        )


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(
        r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)",
        "",
        value.strip(),
        flags=re.I,
    ).strip()
    return normalized or None


def _valid_normalized_doi(value: str | None) -> str | None:
    normalized = _normalize_doi(value)
    return normalized if normalized and DOI_PATTERN.fullmatch(normalized) else None


def _normalize_search_value(query: str, field: SearchField) -> str:
    value = query.strip()
    if field != SearchField.DOI:
        return value
    doi = _valid_normalized_doi(value)
    if not doi:
        raise ValueError("DOI 检索值不能为空，且必须包含有效 DOI 标识符")
    return doi


def _validate_optional_search_filter(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    if len(clean) < 2 or len(clean) > 200:
        raise ValueError(f"{label}必须在 2 到 200 个字符之间")
    if any(ord(character) < 32 or ord(character) == 127 for character in clean):
        raise ValueError(f"{label}不能包含控制字符")
    return clean


def _paper_matches_query(query: str, paper: Paper, field: SearchField) -> bool:
    if field == SearchField.AUTHOR:
        return any(_author_name_matches(query, author) for author in paper.authors)
    if field == SearchField.TITLE:
        return _normalized_exact_match(query, paper.title)
    if field == SearchField.DOI:
        expected = _valid_normalized_doi(query)
        actual = _valid_normalized_doi(paper.doi)
        return bool(expected and actual and expected.casefold() == actual.casefold())
    if field == SearchField.VENUE:
        return _venue_matches_query(query, paper.venue or "")
    return _topic_matches(query, paper)


def _paper_matches_author_filters(
    query: str,
    paper: Paper,
    *,
    affiliation: str | None,
    topic: str | None,
    venue: str | None,
) -> bool:
    if not any((affiliation, topic, venue)):
        return True
    if affiliation:
        matching_authors = [
            author
            for author in paper.author_affiliations
            if _author_name_matches(query, author)
        ]
        exact_authors = [
            author
            for author in matching_authors
            if _author_name_matches(author, query)
        ]
        selected_authors = exact_authors or matching_authors
        matching_affiliations = [
            institution
            for author in selected_authors
            for institution in paper.author_affiliations[author]
        ]
        if not any(
            _affiliation_matches(affiliation, institution)
            for institution in matching_affiliations
        ):
            return False
    if topic and not _topic_matches(topic, paper):
        return False
    return not venue or _venue_matches_query(venue, paper.venue or "")


def _affiliation_matches(query: str, affiliation: str) -> bool:
    normalized_query = _normalize_match_text(query)
    normalized_affiliation = _normalize_match_text(affiliation)
    if not normalized_query or not normalized_affiliation:
        return False
    if _is_cjk_text(normalized_query):
        return normalized_query.replace(" ", "") in normalized_affiliation.replace(
            " ", ""
        )
    return f" {normalized_query} " in f" {normalized_affiliation} "


def _venue_identity(value: str) -> str | None:
    normalized = _normalize_match_text(value)
    for identity, aliases in VENUE_ALIAS_GROUPS.items():
        acronym, *formal_names = aliases
        if re.fullmatch(rf"{re.escape(acronym)}(?: \d{{4}})?", normalized) or any(
            normalized == name or f" {name} " in f" {normalized} "
            for name in formal_names
        ):
            return identity
    return None


def _venue_matches_query(query: str, venue: str) -> bool:
    normalized_query = _normalize_match_text(query)
    normalized_venue = _normalize_match_text(venue)
    if not normalized_query or not normalized_venue:
        return False
    query_is_workshop = "workshop" in normalized_query.split()
    venue_is_workshop = bool(
        {"workshop", "workshops"} & set(normalized_venue.split())
    )
    if venue_is_workshop and not query_is_workshop:
        return False
    query_identity = _venue_identity(query)
    venue_identity = _venue_identity(venue)
    if query_identity or venue_identity:
        return bool(query_identity and query_identity == venue_identity)
    if normalized_query == normalized_venue:
        return True
    acronym = re.sub(r"[^a-z0-9]", "", query.casefold())
    if not 2 <= len(acronym) <= 12 or not acronym.isalpha():
        return False
    venue_tokens = normalized_venue.split()
    if acronym in venue_tokens:
        return True
    ignored = {"and", "of", "on", "the", "proceedings"}
    derived = "".join(
        token[0]
        for token in venue_tokens
        if token not in ignored and not re.fullmatch(r"\d+(?:st|nd|rd|th)?", token)
    )
    return acronym == derived


def _author_name_matches(query: str, returned_name: str) -> bool:
    query_has_comma = "," in query
    returned_has_comma = "," in returned_name
    if _is_cjk_text(query) and _is_cjk_text(returned_name):
        return _normalized_exact_match(query, returned_name)
    if not query_has_comma and not returned_has_comma and _normalized_exact_match(
        query, returned_name
    ):
        return True
    if query_has_comma and not returned_has_comma:
        query_parts = _author_name_parts(query)
        returned_parts = _author_name_parts(
            returned_name, family_size=len(query_parts[1])
        )
    elif returned_has_comma and not query_has_comma:
        returned_parts = _author_name_parts(returned_name)
        query_parts = _author_name_parts(query, family_size=len(returned_parts[1]))
    else:
        query_parts = _author_name_parts(query)
        returned_parts = _author_name_parts(returned_name)
    query_given, query_family, query_suffix = query_parts
    returned_given, returned_family, returned_suffix = returned_parts
    if not query_family or not returned_family:
        return False
    if query_family != returned_family or query_suffix != returned_suffix:
        return False
    query_given_units = _given_name_units(query_given)
    returned_given_units = _given_name_units(returned_given)
    if len(query_given_units) != len(returned_given_units):
        return False
    return all(
        query_unit == returned_unit
        or (len(returned_unit) == 1 and query_unit.startswith(returned_unit))
        for query_unit, returned_unit in zip(
            query_given_units, returned_given_units, strict=True
        )
    )


def _author_name_parts(
    value: str,
    *,
    family_size: int | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if "," in value:
        family_text, given_text = value.split(",", 1)
        family, family_suffix = _without_author_suffix(_author_words(family_text))
        given, given_suffix = _without_author_suffix(_author_words(given_text))
        suffix = [*family_suffix, *given_suffix]
    else:
        words, suffix = _without_author_suffix(_author_words(value))
        if not words:
            return (), (), tuple(suffix)
        if family_size is not None and family_size > 0:
            family_start = max(0, len(words) - family_size)
        else:
            family_start = len(words) - 1
            while (
                family_start > 0
                and words[family_start - 1] in AUTHOR_FAMILY_PARTICLES
            ):
                family_start -= 1
        family = words[family_start:]
        given = words[:family_start]
    return tuple(given), tuple(family), tuple(suffix)


def _without_author_suffix(words: list[str]) -> tuple[list[str], list[str]]:
    name = list(words)
    suffix = []
    while name and name[-1] in AUTHOR_SUFFIXES:
        suffix.insert(0, name.pop())
    return name, suffix


def _author_words(value: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    hyphens = {"-", "‐", "‑", "‒", "–", "—"}
    characters = []
    for index, character in enumerate(plain):
        if character.isalnum():
            characters.append(character)
        elif character in {"'", "’"}:
            continue
        elif character in hyphens:
            characters.append("-")
        elif character == ".":
            next_character = plain[index + 1] if index + 1 < len(plain) else ""
            if next_character and next_character.isalnum():
                characters.append(" ")
        else:
            characters.append(" ")
    return [word.strip("-") for word in "".join(characters).split() if word.strip("-")]


def _given_name_units(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(unit for part in parts for unit in part.split("-") if unit)


def _is_cjk_text(value: str) -> bool:
    normalized = _normalize_match_text(value).replace(" ", "")
    return bool(normalized) and bool(re.fullmatch(r"[\u3400-\u9fff]+", normalized))


def _normalized_exact_match(query: str, value: str) -> bool:
    normalized_query = _normalize_match_text(query)
    return bool(normalized_query) and normalized_query == _normalize_match_text(value)


def _topic_matches(query: str, paper: Paper) -> bool:
    cjk_units = _cjk_query_units(query)
    if cjk_units:
        title = _normalize_match_text(paper.title).replace(" ", "")
        abstract = _normalize_match_text(paper.abstract).replace(" ", "")
        required = _required_topic_matches(len(cjk_units))
        document_matches = sum(
            unit in title or unit in abstract for unit in cjk_units
        )
        abstract_matches = sum(unit in abstract for unit in cjk_units)
        if document_matches < required:
            return False
        return any(unit in title for unit in cjk_units) or abstract_matches >= required

    query_tokens = _significant_tokens(query)
    if not query_tokens:
        return False
    title_tokens = _significant_tokens(paper.title)
    abstract_tokens = _significant_tokens(paper.abstract)
    venue_tokens = _significant_tokens(paper.venue or "")
    document_tokens = title_tokens | abstract_tokens | venue_tokens
    normalized_query = _normalize_match_text(query)
    normalized_document = _normalize_match_text(
        " ".join((paper.title, paper.abstract))
    )
    if normalized_query and f" {normalized_query} " in f" {normalized_document} ":
        return True
    query_size = len(query_tokens)
    required = _required_topic_matches(query_size)
    if len(query_tokens & document_tokens) < required:
        return False
    return bool(query_tokens & title_tokens) or len(query_tokens & abstract_tokens) >= required


def _cjk_query_units(value: str) -> tuple[str, ...]:
    units = tuple(dict.fromkeys(re.findall(r"[\u3400-\u9fff]+", value)))
    remainder = re.sub(r"[\u3400-\u9fff]+", "", value)
    if not units or any(character.isalnum() for character in remainder):
        return ()
    return units


def _required_topic_matches(query_size: int) -> int:
    if query_size <= 2:
        return query_size
    return math.ceil(query_size * (0.6 if query_size <= 5 else 0.5))


def _significant_tokens(value: str) -> set[str]:
    tokens = set(_normalize_match_text(value).split())
    significant = tokens - SEARCH_STOPWORDS
    return significant or tokens


def _normalize_match_text(value: str) -> str:
    technical = re.sub(
        r"(?<![\w+])([a-z])\+\+(?!\+)",
        r" \1plusplus ",
        value.casefold(),
        flags=re.I,
    )
    technical = re.sub(
        r"(?<![\w#])([a-z])#(?!#)", r" \1sharp ", technical, flags=re.I
    )
    decomposed = unicodedata.normalize("NFKD", technical)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    normalized = " ".join(
        "".join(character if character.isalnum() else " " for character in without_marks)
        .split()
    )
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", normalized)


def _paper_field_text(paper: Paper, field: SearchField) -> str:
    if field == SearchField.TITLE:
        return paper.title
    if field == SearchField.AUTHOR:
        return " ".join(paper.authors)
    if field == SearchField.DOI:
        return paper.doi or ""
    if field == SearchField.VENUE:
        return paper.venue or ""
    return paper.title


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()


def _inverted_abstract(value: dict[str, list[int]] | None) -> str:
    if not value:
        return ""
    positions = [(position, word) for word, indexes in value.items() for position in indexes]
    return " ".join(word for _, word in sorted(positions))


def _cite_key(paper: Paper) -> str:
    family = "Paper"
    if paper.authors:
        family = re.sub(r"[^A-Za-z0-9]", "", paper.authors[0].split()[-1]) or "Paper"
    identity = (paper.doi or _normalize_title(paper.title) or paper.external_id).lower()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:6]
    return f"{family}{paper.year or 'nd'}{digest}"
