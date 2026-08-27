from __future__ import annotations

import asyncio
import io
import urllib.error
import urllib.parse
from dataclasses import replace

import pytest

from scholaros.domain import Paper, SearchField
from scholaros.papers import (
    AcmMetadataSource,
    ArxivSource,
    CrossrefSource,
    DblpSource,
    IeeeSource,
    JsonHttpSource,
    MemorySource,
    OpenAlexSource,
    PaperSearchService,
    SemanticScholarSource,
    SourceHttpError,
    SourcePayloadError,
    normalize_http_url,
)


@pytest.mark.asyncio
async def test_search_deduplicates_doi_and_merges_sources() -> None:
    first = Paper(
        title="Traceable Research Agents",
        authors=["Ada Smith"],
        year=2025,
        abstract="short",
        sources=["first"],
        external_id="a",
        doi="10.1000/example",
    )
    second = Paper(
        title="Traceable Research Agents",
        authors=["Ada Smith"],
        year=2025,
        abstract="a much longer abstract",
        sources=["second"],
        external_id="b",
        doi="10.1000/example",
        is_open_access=True,
    )
    source_a = MemorySource([first])
    source_a.name = "a"
    source_b = MemorySource([second])
    source_b.name = "b"
    service = PaperSearchService([source_a, source_b])

    result = await service.search("traceable research agents", limit=5)

    assert not result.failures
    assert len(result.papers) == 1
    assert result.papers[0].abstract == "a much longer abstract"
    assert result.papers[0].sources == ["first", "second"]
    assert result.papers[0].cite_key

    reversed_result = await PaperSearchService([source_b, source_a]).search(
        "traceable research agents", limit=5
    )
    assert reversed_result.papers[0].cite_key == result.papers[0].cite_key


@pytest.mark.asyncio
async def test_search_many_keeps_separate_related_work_clusters() -> None:
    source = MemorySource(
        [
            Paper(
                title="Localized feature selection for classification",
                authors=["Ada Smith"],
                year=2025,
                abstract="Feature subsets adapt to heterogeneous local regions.",
                sources=["memory"],
                external_id="localized-fs",
            ),
            Paper(
                title="Large language models for feature selection",
                authors=["Bo Chen"],
                year=2026,
                abstract="Language-model semantic priors guide feature selection.",
                sources=["memory"],
                external_id="llm-fs",
            ),
        ]
    )

    result = await PaperSearchService([source]).search_many(
        ["localized feature selection", "large language models feature selection"],
        limit=10,
    )

    assert {paper.external_id for paper in result.papers} == {"localized-fs", "llm-fs"}


@pytest.mark.asyncio
async def test_search_many_reserves_space_for_each_query_cluster() -> None:
    papers = [
        Paper(
            title=f"Alpha method {index}",
            authors=["Ada Smith"],
            year=2025,
            abstract="alpha method",
            sources=["memory"],
            external_id=f"alpha-{index}",
            citation_count=1000 - index,
        )
        for index in range(5)
    ]
    papers.extend(
        [
            Paper(
                title="Beta method",
                authors=["Bo Chen"],
                year=2025,
                abstract="beta method",
                sources=["memory"],
                external_id="beta",
            ),
            Paper(
                title="Gamma method",
                authors=["Cy Diaz"],
                year=2025,
                abstract="gamma method",
                sources=["memory"],
                external_id="gamma",
            ),
        ]
    )

    result = await PaperSearchService([MemorySource(papers)]).search_many(
        ["alpha", "beta", "gamma"], limit=3
    )

    assert {paper.external_id for paper in result.papers} == {"alpha-0", "beta", "gamma"}


@pytest.mark.asyncio
async def test_search_many_stops_retrying_rate_limited_source_within_batch() -> None:
    class RateLimitedSource:
        name = "limited"
        available = True

        def __init__(self):
            self.calls = 0

        async def search(self, query, limit, field=SearchField.ALL):
            del query, limit, field
            self.calls += 1
            raise SourceHttpError(429, "rate limited", retry_after_seconds=60)

    source = RateLimitedSource()
    result = await PaperSearchService([source]).search_many(
        ["alpha", "beta", "gamma"], limit=9
    )

    assert source.calls == 1
    assert len(result.failures) == 1
    assert result.failures[0].retry_after_seconds == 60


@pytest.mark.asyncio
async def test_deduplication_replaces_unsafe_links_with_valid_source_links() -> None:
    unsafe = Paper(
        title="Merged paper links",
        authors=[],
        year=2026,
        abstract="",
        sources=["unsafe"],
        external_id="unsafe",
        doi="10.1000/merged",
        landing_url="javascript:bad",
        pdf_url="https://example.org/\x07",
    )
    valid = Paper(
        title="Merged paper links",
        authors=[],
        year=2026,
        abstract="",
        sources=["valid"],
        external_id="valid",
        doi="10.1000/merged",
        landing_url="https://example.org/paper",
        pdf_url="https://example.org/paper.pdf",
    )
    first = MemorySource([unsafe])
    first.name = "first"
    second = MemorySource([valid])
    second.name = "second"

    result = await PaperSearchService([first, second]).search("merged paper", limit=5)

    assert result.papers[0].landing_url == "https://example.org/paper"
    assert result.papers[0].pdf_url == "https://example.org/paper.pdf"


@pytest.mark.asyncio
async def test_unknown_source_is_reported_not_raised() -> None:
    result = await PaperSearchService([]).search("agents", selected=["missing"])
    assert result.papers == []
    assert result.failures[0].source == "missing"
    assert result.failures[0].suggestion

    unsafe = await PaperSearchService([]).search(
        "agents", selected=["bad\x1b]8;;https://evil.example\x07"]
    )
    assert "\x1b" not in unsafe.failures[0].source
    assert "\x07" not in unsafe.failures[0].source


@pytest.mark.asyncio
async def test_source_http_failures_have_safe_source_specific_solutions() -> None:
    class FailingSource:
        available = True

        def __init__(self, name: str, error: BaseException):
            self.name = name
            self.error = error

        async def search(self, query, limit, field=SearchField.ALL):
            raise self.error

    cases = [
        (
            "semantic_scholar",
            SourceHttpError(429, '{"message":"Too Many Requests"}', 17),
            "SEMANTIC_SCHOLAR_API_KEY",
        ),
        (
            "dblp",
            SourceHttpError(503, "<html><body>No server</body></html>"),
            "DBLP 服务端",
        ),
        (
            "acm",
            SourceHttpError(503, "upstream unavailable"),
            "Crossref",
        ),
    ]

    for source_name, error, expected_solution in cases:
        result = await PaperSearchService([FailingSource(source_name, error)]).search(
            "research agents"
        )
        failure = result.failures[0]
        assert failure.status_code in {429, 503}
        assert failure.retryable is True
        assert expected_solution in failure.suggestion
        assert "<html>" not in failure.reason
        assert "Too Many Requests" not in failure.reason
        if source_name == "semantic_scholar":
            assert failure.retry_after_seconds == 17
            assert "等待 17 秒" in failure.suggestion


@pytest.mark.asyncio
async def test_ieee_auth_failure_explains_activation_state() -> None:
    class FailingIeee:
        name = "ieee"
        available = True

        async def search(self, query, limit, field=SearchField.ALL):
            raise SourceHttpError(403, "forbidden")

    result = await PaperSearchService([FailingIeee()]).search("research agents")

    failure = result.failures[0]
    assert failure.status_code == 403
    assert "IEEE_XPLORE_API_KEY" in failure.suggestion
    assert "waiting" in failure.suggestion


@pytest.mark.asyncio
async def test_raw_http_error_retry_after_is_preserved_for_arxiv() -> None:
    class FailingArxiv:
        name = "arxiv"
        available = True

        async def search(self, query, limit, field=SearchField.ALL):
            raise urllib.error.HTTPError(
                "https://export.arxiv.org/api/query",
                429,
                "Too Many Requests",
                {"Retry-After": "29"},
                io.BytesIO(b"rate limited"),
            )

    result = await PaperSearchService([FailingArxiv()]).search("agents")

    failure = result.failures[0]
    assert failure.retry_after_seconds == 29
    assert "等待 29 秒" in failure.suggestion


@pytest.mark.asyncio
async def test_http_error_body_is_bounded_and_retry_after_is_preserved(
    settings, monkeypatch
) -> None:
    class TrackingBody(io.BytesIO):
        def __init__(self):
            super().__init__(b"x" * 10_000)
            self.read_sizes = []

        def read(self, size=-1):
            self.read_sizes.append(size)
            return super().read(size)

    body = TrackingBody()
    error = urllib.error.HTTPError(
        "https://example.invalid",
        429,
        "Too Many Requests",
        {"Retry-After": "23"},
        body,
    )

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)

    with pytest.raises(SourceHttpError) as captured:
        await JsonHttpSource(settings).get_json("https://example.invalid", {})

    assert body.read_sizes == [300]
    assert captured.value.retry_after_seconds == 23


@pytest.mark.asyncio
async def test_timeout_and_malformed_payload_have_specific_diagnostics(
    settings, monkeypatch
) -> None:
    class FailingSource:
        name = "dblp"
        available = True

        async def search(self, query, limit, field=SearchField.ALL):
            raise urllib.error.URLError(TimeoutError("timed out"))

    timeout = await PaperSearchService([FailingSource()]).search("agents")
    assert timeout.failures[0].reason == "连接该来源超时"
    assert timeout.failures[0].retryable is True

    class JsonListResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: JsonListResponse(b"[]")
    )
    with pytest.raises(SourcePayloadError, match="根节点"):
        await JsonHttpSource(settings).get_json("https://example.invalid", {})


@pytest.mark.asyncio
async def test_public_source_auth_failure_does_not_request_unsupported_key() -> None:
    class PublicSource:
        available = True

        def __init__(self, name):
            self.name = name

        async def search(self, query, limit, field=SearchField.ALL):
            raise SourceHttpError(403, "forbidden")

    for source_name in ("arxiv", "dblp", "openalex"):
        result = await PaperSearchService([PublicSource(source_name)]).search("agents")
        suggestion = result.failures[0].suggestion
        assert "检查该来源的密钥" not in suggestion
        assert "公开接口" in suggestion


@pytest.mark.asyncio
async def test_search_limit_is_validated() -> None:
    with pytest.raises(ValueError, match="1 到 100"):
        await PaperSearchService([]).search("agents", limit=-1)


@pytest.mark.asyncio
async def test_search_service_passes_structured_field_to_source() -> None:
    class RecordingSource:
        name = "recording"
        available = True

        def __init__(self):
            self.field = None

        async def search(self, query, limit, field=SearchField.ALL):
            self.field = field
            return []

    source = RecordingSource()

    await PaperSearchService([source]).search(
        "Geoffrey Hinton", selected=["recording"], field=SearchField.AUTHOR
    )

    assert source.field == SearchField.AUTHOR


@pytest.mark.asyncio
async def test_author_search_requires_same_name_but_accepts_returned_initials() -> None:
    papers = [
        Paper(
            title=title,
            authors=[author],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id=str(index),
        )
        for index, (title, author) in enumerate(
            [
                ("Full name", "Geoffrey Hinton"),
                ("Initial", "G. Hinton"),
                ("Comma initial", "Hinton, G."),
                ("Wrong given name", "George Hinton"),
                ("Extra middle name", "Geoffrey E. Hinton"),
                ("Wrong family name", "Geoffrey Hilton"),
                ("Wrong comma semantics", "Geoffrey, Hinton"),
            ]
        )
    ]

    result = await PaperSearchService([MemorySource(papers)]).search(
        "Geoffrey Hinton", field=SearchField.AUTHOR
    )

    assert {paper.title for paper in result.papers} == {
        "Full name",
        "Initial",
        "Comma initial",
    }
    assert result.filtered_out == 4


@pytest.mark.asyncio
async def test_author_search_normalizes_diacritics_for_returned_initials() -> None:
    source = MemorySource(
        [
            Paper(
                title="Matching abbreviated author",
                authors=["J. Schmidhuber"],
                year=2026,
                abstract="",
                sources=["memory"],
                external_id="abbreviated-author",
            )
        ]
    )

    result = await PaperSearchService([source]).search(
        "Jürgen Schmidhuber", field=SearchField.AUTHOR
    )

    assert [paper.title for paper in result.papers] == ["Matching abbreviated author"]

    source.papers = [
        Paper(
            title="Matching Chinese author spacing",
            authors=["张 伟"],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="chinese-author-spacing",
        )
    ]
    chinese_result = await PaperSearchService([source]).search(
        "张伟", field=SearchField.AUTHOR
    )
    assert [paper.external_id for paper in chinese_result.papers] == [
        "chinese-author-spacing"
    ]

    source.papers[0].authors = ["张, 伟"]
    chinese_comma_result = await PaperSearchService([source]).search(
        "张伟", field=SearchField.AUTHOR
    )
    assert [paper.external_id for paper in chinese_comma_result.papers] == [
        "chinese-author-spacing"
    ]

    source.papers[0].authors = ["伟, 张"]
    reversed_chinese_result = await PaperSearchService([source]).search(
        "张伟", field=SearchField.AUTHOR
    )
    assert reversed_chinese_result.papers == []


@pytest.mark.asyncio
async def test_author_search_preserves_family_particles_hyphens_and_suffixes() -> None:
    papers = [
        Paper(
            title=title,
            authors=[author],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id=title,
        )
        for title, author in [
            ("Particle accepted", "J. de la Cruz"),
            ("Particles abbreviated", "J. d. l. Cruz"),
            ("Hyphen accepted", "A. Hinton-Smith"),
            ("Family abbreviated", "A. H.-Smith"),
            ("Suffix comma accepted", "Hinton, Geoffrey Jr."),
            ("Suffix family comma accepted", "Hinton Jr., Geoffrey"),
            ("Bin particle accepted", "bin Laden, O."),
            ("Dos particle accepted", "dos Santos, J."),
            ("Hyphenated given accepted", "J.-P. Serre"),
            ("Compact initials accepted", "J.P. Serre"),
            ("Spaced initials accepted", "J. P. Serre"),
            ("Comma compact initials accepted", "Serre, J.P."),
            ("St particle accepted", "I. St. John"),
            ("St particle abbreviated", "I. S. John"),
            ("Af particle abbreviated", "L. a. Hällström"),
            ("Ap particle abbreviated", "A. a. Rhys"),
        ]
    ]
    service = PaperSearchService([MemorySource(papers)])

    particle_result = await service.search(
        "Juan de la Cruz", field=SearchField.AUTHOR
    )
    hyphen_result = await service.search(
        "Anne Hinton-Smith", field=SearchField.AUTHOR
    )
    suffix_result = await service.search(
        "Geoffrey Hinton Jr", field=SearchField.AUTHOR
    )
    bin_result = await service.search("Osama bin Laden", field=SearchField.AUTHOR)
    dos_result = await service.search("João dos Santos", field=SearchField.AUTHOR)
    hyphenated_given_result = await service.search(
        "Jean-Pierre Serre", field=SearchField.AUTHOR
    )
    st_result = await service.search("Ian St. John", field=SearchField.AUTHOR)
    af_result = await service.search("Lars af Hällström", field=SearchField.AUTHOR)
    ap_result = await service.search("Alicia ap Rhys", field=SearchField.AUTHOR)

    assert [paper.title for paper in particle_result.papers] == ["Particle accepted"]
    assert [paper.title for paper in hyphen_result.papers] == ["Hyphen accepted"]
    assert {paper.title for paper in suffix_result.papers} == {
        "Suffix comma accepted",
        "Suffix family comma accepted",
    }
    assert [paper.title for paper in bin_result.papers] == ["Bin particle accepted"]
    assert [paper.title for paper in dos_result.papers] == ["Dos particle accepted"]
    assert {paper.title for paper in hyphenated_given_result.papers} == {
        "Hyphenated given accepted",
        "Compact initials accepted",
        "Spaced initials accepted",
        "Comma compact initials accepted",
    }
    assert [paper.title for paper in st_result.papers] == ["St particle accepted"]
    assert af_result.papers == []
    assert ap_result.papers == []


@pytest.mark.asyncio
async def test_arxiv_groups_structured_multiword_query_as_phrase(
    settings, monkeypatch
) -> None:
    captured = {}

    class AtomResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured.update({"url": request.full_url, "timeout": timeout})
        return AtomResponse(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    await ArxivSource(settings).search(
        "Geoffrey Hinton", 5, field=SearchField.AUTHOR
    )

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(captured["url"]).query)
    assert query["search_query"] == ['au:"Geoffrey Hinton"']


@pytest.mark.asyncio
async def test_title_and_venue_search_use_normalized_exact_matching() -> None:
    papers = [
        Paper(
            title="Attention Is All You Need",
            authors=[],
            year=2017,
            abstract="",
            sources=["memory"],
            external_id="exact-title",
            venue="NeurIPS",
        ),
        Paper(
            title="Attention Is All You Need: A Retrospective",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="title-extension",
            venue="NeurIPS Workshops",
        ),
    ]
    service = PaperSearchService([MemorySource(papers)])

    title_result = await service.search(
        "attention is all you need", field=SearchField.TITLE
    )
    venue_result = await service.search("NEURIPS", field=SearchField.VENUE)

    assert [paper.external_id for paper in title_result.papers] == ["exact-title"]
    assert [paper.external_id for paper in venue_result.papers] == ["exact-title"]
    assert title_result.filtered_out == 1
    assert venue_result.filtered_out == 1


@pytest.mark.asyncio
async def test_author_search_combines_same_author_affiliation_topic_and_venue() -> None:
    papers = [
        Paper(
            title="Reliable Vision Agents",
            authors=["Wei Wang", "Alice Chen"],
            year=2026,
            abstract="A reliable computer vision agent for visual reasoning.",
            sources=["memory"],
            external_id="target",
            venue="Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition",
            author_affiliations={
                "Wei Wang": ["Shenzhen University"],
                "Alice Chen": ["Other University"],
            },
        ),
        Paper(
            title="Reliable Vision Agents",
            authors=["Wei Wang"],
            year=2026,
            abstract="A reliable computer vision agent for visual reasoning.",
            sources=["memory"],
            external_id="wrong-person",
            venue="CVPR",
            author_affiliations={"Wei Wang": ["Tsinghua University"]},
        ),
        Paper(
            title="Reliable Vision Agents",
            authors=["Wei Wang", "Alice Chen"],
            year=2026,
            abstract="A reliable computer vision agent for visual reasoning.",
            sources=["memory"],
            external_id="coauthor-school",
            venue="CVPR",
            author_affiliations={
                "Wei Wang": ["Other University"],
                "Alice Chen": ["Shenzhen University"],
            },
        ),
        Paper(
            title="Database Indexing",
            authors=["Wei Wang"],
            year=2026,
            abstract="Database systems.",
            sources=["memory"],
            external_id="wrong-topic",
            venue="CVPR",
            author_affiliations={"Wei Wang": ["Shenzhen University"]},
        ),
    ]

    result = await PaperSearchService([MemorySource(papers)]).search(
        "Wei Wang",
        field=SearchField.AUTHOR,
        author_affiliation="Shenzhen University",
        author_topic="vision agents",
        author_venue="CVPR",
    )

    assert [paper.external_id for paper in result.papers] == ["target"]
    assert result.filtered_out == 3


@pytest.mark.asyncio
async def test_author_affiliation_preserves_institution_word_order() -> None:
    source = MemorySource(
        [
            Paper(
                title="Ambiguous institution name",
                authors=["Wei Wang"],
                year=2026,
                abstract="vision",
                sources=["memory"],
                external_id="wrong-university",
                author_affiliations={
                    "Wei Wang": ["Washington University in St. Louis"]
                },
            )
        ]
    )

    result = await PaperSearchService([source]).search(
        "Wei Wang",
        field=SearchField.AUTHOR,
        author_affiliation="University of Washington",
    )

    assert result.papers == []
    assert result.filtered_out == 1


@pytest.mark.asyncio
async def test_author_affiliation_accepts_cjk_institution_department_suffix() -> None:
    source = MemorySource(
        [
            Paper(
                title="纯中文机构元数据",
                authors=["张伟"],
                year=2026,
                abstract="机器学习",
                sources=["memory"],
                external_id="pku-cn",
                author_affiliations={"张伟": ["北京大学计算机学院"]},
            ),
            Paper(
                title="双语机构元数据",
                authors=["张伟"],
                year=2026,
                abstract="机器学习",
                sources=["memory"],
                external_id="pku-bilingual",
                author_affiliations={
                    "张伟": ["北京大学计算机学院（Peking University）"]
                },
            ),
        ]
    )

    result = await PaperSearchService([source]).search(
        "张伟",
        field=SearchField.AUTHOR,
        author_affiliation="北京大学",
    )

    assert {paper.external_id for paper in result.papers} == {
        "pku-cn",
        "pku-bilingual",
    }


@pytest.mark.asyncio
async def test_author_affiliation_prefers_full_name_over_abbreviated_coauthor() -> None:
    source = MemorySource(
        [
            Paper(
                title="Two similarly named authors",
                authors=["Wei Wang", "W. Wang"],
                year=2026,
                abstract="vision",
                sources=["memory"],
                external_id="coauthor-initial",
                author_affiliations={
                    "Wei Wang": ["Other University"],
                    "W. Wang": ["Shenzhen University"],
                },
            )
        ]
    )

    result = await PaperSearchService([source]).search(
        "Wei Wang",
        field=SearchField.AUTHOR,
        author_affiliation="Shenzhen University",
    )

    assert result.papers == []
    assert result.filtered_out == 1


@pytest.mark.asyncio
async def test_author_affiliation_skips_sources_without_verifiable_relationships(
    settings,
) -> None:
    result = await PaperSearchService(
        [ArxivSource(settings), DblpSource(settings)]
    ).search(
        "Wei Wang",
        field=SearchField.AUTHOR,
        author_affiliation="Shenzhen University",
    )

    assert result.papers == []
    assert {failure.source for failure in result.failures} == {"arxiv", "dblp"}
    assert all("作者—机构" in failure.reason for failure in result.failures)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "venue"),
    [
        ("CVPR", "Proceedings of the Computer Vision and Pattern Recognition Conference (CVPR)"),
        ("AAAI", "Proceedings of the AAAI Conference on Artificial Intelligence"),
        ("NIPS", "Advances in Neural Information Processing Systems 30"),
        ("NeurIPS", "Conference on Neural Information Processing Systems"),
        ("ICML", "Proceedings of the 40th International Conference on Machine Learning"),
        ("ICLR", "International Conference on Learning Representations"),
        ("ACL", "Annual Meeting of the Association for Computational Linguistics"),
        ("EMNLP", "Conference on Empirical Methods in Natural Language Processing"),
    ],
)
async def test_top_conference_aliases_match_official_venue_variants(
    query: str, venue: str
) -> None:
    source = MemorySource(
        [
            Paper(
                title=f"Paper at {query}",
                authors=["Ada Smith"],
                year=2025,
                abstract="",
                sources=["memory"],
                external_id=query,
                venue=venue,
            ),
            Paper(
                title=f"Workshop paper near {query}",
                authors=["Ada Smith"],
                year=2025,
                abstract="",
                sources=["memory"],
                external_id=f"{query}-workshop",
                venue=f"{venue} Workshops",
            ),
        ]
    )

    result = await PaperSearchService([source]).search(
        query, field=SearchField.VENUE
    )

    assert [paper.external_id for paper in result.papers] == [query]
    assert result.filtered_out == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "nearby_venue"),
    [
        ("AAAI", "International AAAI Conference on Web and Social Media"),
        ("ACL", "Findings of ACL"),
    ],
)
async def test_top_conference_acronym_does_not_match_neighboring_venues(
    query: str, nearby_venue: str
) -> None:
    source = MemorySource(
        [
            Paper(
                title="Paper from another venue",
                authors=["Ada Smith"],
                year=2026,
                abstract="",
                sources=["memory"],
                external_id="neighbor",
                venue=nearby_venue,
            )
        ]
    )

    result = await PaperSearchService([source]).search(
        query, field=SearchField.VENUE
    )

    assert result.papers == []
    assert result.filtered_out == 1

    exact_result = await PaperSearchService([source]).search(
        nearby_venue, field=SearchField.VENUE
    )
    assert [paper.external_id for paper in exact_result.papers] == ["neighbor"]


@pytest.mark.asyncio
async def test_topic_search_filters_partial_token_collisions() -> None:
    papers = [
        Paper(
            title="FMM-Agent for feature meta-model evolution",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="exact-topic",
        ),
        Paper(
            title="Fast marching method using FMM",
            authors=[],
            year=2025,
            abstract="",
            sources=["memory"],
            external_id="fmm-only",
        ),
        Paper(
            title="An unrelated multi-agent classifier",
            authors=[],
            year=2024,
            abstract="",
            sources=["memory"],
            external_id="agent-only",
        ),
    ]

    result = await PaperSearchService([MemorySource(papers)]).search("FMM-Agent")

    assert [paper.external_id for paper in result.papers] == ["exact-topic"]
    assert result.filtered_out == 2


@pytest.mark.asyncio
async def test_topic_search_preserves_programming_language_symbols() -> None:
    papers = [
        Paper(
            title="Modern C++ programming",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="cpp",
        ),
        Paper(
            title="Modern C# programming",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="csharp",
        ),
        Paper(
            title="Modern F# programming",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="fsharp",
        ),
        Paper(
            title="Modern F programming",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="f",
        ),
    ]

    result = await PaperSearchService([MemorySource(papers)]).search("C++")
    fsharp_result = await PaperSearchService([MemorySource(papers)]).search("F#")
    exact_fsharp_title = await PaperSearchService([MemorySource(papers)]).search(
        "Modern F# programming", field=SearchField.TITLE
    )
    broad_result = await PaperSearchService([MemorySource(papers)]).search("programming")

    assert [paper.external_id for paper in result.papers] == ["cpp"]
    assert [paper.external_id for paper in fsharp_result.papers] == ["fsharp"]
    assert [paper.external_id for paper in exact_fsharp_title.papers] == ["fsharp"]
    assert {paper.external_id for paper in broad_result.papers} == {
        "cpp",
        "csharp",
        "fsharp",
        "f",
    }


@pytest.mark.asyncio
async def test_topic_search_matches_cjk_terms_inside_longer_titles() -> None:
    papers = [
        Paper(
            title="科研智能体系统",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="research-agent",
        ),
        Paper(
            title="面向研究的智能体",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="agent-for-research",
        ),
        Paper(
            title="科研数据管理平台",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="unrelated",
        ),
        Paper(
            title="面向科研智能体的引用可靠性评估",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id="reliable-agent",
        ),
    ]

    result = await PaperSearchService([MemorySource(papers)]).search("智能体")
    multi_term_result = await PaperSearchService([MemorySource(papers)]).search(
        "智能体 可靠性"
    )
    reversed_terms_result = await PaperSearchService([MemorySource(papers)]).search(
        "可靠性 智能体"
    )
    missing_term_result = await PaperSearchService([MemorySource(papers)]).search(
        "智能体 不存在"
    )

    assert {paper.external_id for paper in result.papers} == {
        "research-agent",
        "agent-for-research",
        "reliable-agent",
    }
    assert [paper.external_id for paper in multi_term_result.papers] == [
        "reliable-agent"
    ]
    assert [paper.external_id for paper in reversed_terms_result.papers] == [
        "reliable-agent"
    ]
    assert missing_term_result.papers == []


def test_crossref_uses_field_specific_query_parameters(settings) -> None:
    source = CrossrefSource(settings)

    assert source._params("Geoffrey Hinton", 5, SearchField.AUTHOR)["query.author"] == (
        "Geoffrey Hinton"
    )
    assert source._params("Attention", 5, SearchField.TITLE)["query.title"] == "Attention"
    assert source._params("NeurIPS", 5, SearchField.VENUE)[
        "query.container-title"
    ] == "Neural Information Processing Systems"
    assert "filter" not in source._params("NeurIPS", 5, SearchField.VENUE)
    assert source._params("Nature", 5, SearchField.VENUE)["filter"] == (
        "container-title:Nature"
    )
    author_params = source._author_filter_params(
        "Wei Wang",
        100,
        affiliation="Shenzhen University",
        topic="computer vision",
        venue="CVPR",
    )
    assert author_params["query.author"] == "Wei Wang"
    assert author_params["query.affiliation"] == "Shenzhen University"
    assert author_params["query.bibliographic"] == "computer vision"
    assert author_params["query.container-title"] == (
        "Computer Vision and Pattern Recognition"
    )


def test_crossref_keeps_affiliations_bound_to_each_author(settings) -> None:
    papers = CrossrefSource(settings)._parse_items(
        {
            "message": {
                "items": [
                    {
                        "DOI": "10.1000/affiliation",
                        "title": ["Affiliated work"],
                        "published": {"date-parts": [[2026]]},
                        "author": [
                            {
                                "given": "Wei",
                                "family": "Wang",
                                "affiliation": [{"name": "Shenzhen University"}],
                            },
                            {
                                "given": "Alice",
                                "family": "Chen",
                                "affiliation": [{"name": "Other University"}],
                            },
                        ],
                    }
                ]
            }
        }
    )

    assert papers[0].author_affiliations == {
        "Wei Wang": ["Shenzhen University"],
        "Alice Chen": ["Other University"],
    }


def test_crossref_accumulates_affiliations_for_duplicate_display_names(settings) -> None:
    papers = CrossrefSource(settings)._parse_items(
        {
            "message": {
                "items": [
                    {
                        "DOI": "10.1000/duplicate-name",
                        "title": ["Two authors with the same display name"],
                        "published": {"date-parts": [[2026]]},
                        "author": [
                            {
                                "given": "Wei",
                                "family": "Wang",
                                "affiliation": [{"name": "Shenzhen University"}],
                            },
                            {
                                "given": "Wei",
                                "family": "Wang",
                                "affiliation": [{"name": "Tsinghua University"}],
                            },
                        ],
                    }
                ]
            }
        }
    )

    assert papers[0].author_affiliations == {
        "Wei Wang": ["Shenzhen University", "Tsinghua University"]
    }


@pytest.mark.asyncio
async def test_crossref_and_semantic_scholar_use_exact_doi_endpoints(
    settings, monkeypatch
) -> None:
    crossref = CrossrefSource(settings)
    crossref_capture = {}

    async def fake_crossref(url, params, headers=None):
        crossref_capture.update({"url": url, "params": params, "headers": headers})
        return {
            "message": {
                "DOI": "10.1145/1234567",
                "title": ["Exact DOI"],
                "published": {"date-parts": [[2026]]},
            }
        }

    monkeypatch.setattr(crossref, "get_json", fake_crossref)
    papers = await crossref.search(
        "HTTPS://DOI.ORG/10.1145/1234567", 5, SearchField.DOI
    )

    assert crossref_capture["url"].endswith("/10.1145%2F1234567")
    assert papers[0].doi == "10.1145/1234567"

    semantic = SemanticScholarSource(settings)
    semantic_capture = {}

    async def fake_semantic(url, params, headers=None):
        semantic_capture.update({"url": url, "params": params, "headers": headers})
        return {
            "paperId": "paper-id",
            "title": "Exact DOI",
            "authors": [],
            "externalIds": {"DOI": "10.1145/1234567"},
        }

    monkeypatch.setattr(semantic, "get_json", fake_semantic)
    await semantic.search("10.1145/1234567", 5, SearchField.DOI)

    assert semantic_capture["url"].endswith("/DOI:10.1145%2F1234567")


@pytest.mark.asyncio
async def test_openalex_resolves_author_id_before_querying_works(settings, monkeypatch) -> None:
    source = OpenAlexSource(settings)
    calls = []

    async def fake_get_json(url, params, headers=None):
        calls.append((url, params, headers))
        if url.endswith("/authors"):
            return {
                "results": [
                    {
                        "id": "https://openalex.org/A123",
                        "display_name": "Geoffrey Hinton",
                    },
                    {
                        "id": "https://openalex.org/A999",
                        "display_name": "Geoff Hinton-Smith",
                    },
                ]
            }
        return {"results": []}

    monkeypatch.setattr(source, "get_json", fake_get_json)

    await source.search("Geoffrey Hinton", 5, SearchField.AUTHOR)

    assert calls[0][0].endswith("/authors")
    assert calls[1][1]["filter"] == "authorships.author.id:A123"


@pytest.mark.asyncio
async def test_openalex_parses_per_author_institutions(settings, monkeypatch) -> None:
    source = OpenAlexSource(settings)

    async def fake_get_json(url, params, headers=None):
        if url.endswith("/authors"):
            return {"results": [{"id": "https://openalex.org/A1", "display_name": "Wei Wang"}]}
        return {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "title": "Affiliated work",
                    "authorships": [
                        {
                            "author": {"display_name": "Wei Wang"},
                            "institutions": [{"display_name": "Shenzhen University"}],
                        }
                    ],
                    "primary_location": {"source": {"display_name": "CVPR"}},
                }
            ]
        }

    monkeypatch.setattr(source, "get_json", fake_get_json)
    papers = await source.search("Wei Wang", 5, SearchField.AUTHOR)

    assert papers[0].author_affiliations == {"Wei Wang": ["Shenzhen University"]}


@pytest.mark.asyncio
async def test_openalex_pushes_author_filters_into_works_recall(
    settings, monkeypatch
) -> None:
    source = OpenAlexSource(settings)
    calls = []

    async def fake_get_json(url, params, headers=None):
        calls.append((url, params, headers))
        if url.endswith("/authors"):
            return {"results": [{"id": "https://openalex.org/A1", "display_name": "Wei Wang"}]}
        if url.endswith("/institutions"):
            return {
                "results": [
                    {"id": "https://openalex.org/I1", "display_name": "Shenzhen University"}
                ]
            }
        if url.endswith("/sources"):
            return {
                "results": [
                    {
                        "id": "https://openalex.org/S1",
                        "display_name": "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
                    }
                ]
            }
        return {"results": []}

    monkeypatch.setattr(source, "get_json", fake_get_json)

    await source.search_author(
        "Wei Wang",
        100,
        affiliation="Shenzhen University",
        topic="vision agents",
        venue="CVPR",
    )

    works_params = calls[-1][1]
    assert works_params["search"] == "vision agents"
    assert works_params["filter"] == (
        "authorships.author.id:A1,authorships.institutions.id:I1,"
        "primary_location.source.id:S1"
    )


def test_openalex_accumulates_duplicate_author_affiliations(settings) -> None:
    papers = OpenAlexSource(settings)._parse_works(
        {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "title": "Duplicate names",
                    "authorships": [
                        {
                            "author": {"display_name": "Wei Wang"},
                            "institutions": [{"display_name": "Shenzhen University"}],
                        },
                        {
                            "author": {"display_name": "Wei Wang"},
                            "institutions": [{"display_name": "Tsinghua University"}],
                        },
                    ],
                }
            ]
        }
    )

    assert papers[0].author_affiliations == {
        "Wei Wang": ["Shenzhen University", "Tsinghua University"]
    }


@pytest.mark.asyncio
async def test_dblp_uses_author_token(settings, monkeypatch) -> None:
    source = DblpSource(settings)
    captured = {}

    async def fake_get_json(url, params, headers=None):
        captured.update({"url": url, "params": params, "headers": headers})
        return {"result": {"hits": {"hit": []}}}

    monkeypatch.setattr(source, "get_json", fake_get_json)

    await source.search("Geoffrey Hinton", 5, SearchField.AUTHOR)

    assert captured["params"]["q"] == "author:Geoffrey_Hinton:"

    with pytest.raises(ValueError, match="期刊/会议名称"):
        await source.search("NeurIPS", 5, SearchField.VENUE)


@pytest.mark.asyncio
async def test_unsupported_structured_fields_degrade_explicitly(settings) -> None:
    with pytest.raises(ValueError, match="不支持 DOI"):
        await ArxivSource(settings).search("10.1145/example", 5, SearchField.DOI)
    with pytest.raises(ValueError, match="不支持严格期刊/会议"):
        await ArxivSource(settings).search("Nature", 5, SearchField.VENUE)
    with pytest.raises(ValueError, match="不支持该结构化字段"):
        await SemanticScholarSource(settings).search("Attention", 5, SearchField.TITLE)


@pytest.mark.asyncio
async def test_semantic_scholar_uses_official_venue_filter(settings, monkeypatch) -> None:
    source = SemanticScholarSource(settings)
    captured = {}

    async def fake_get_json(url, params, headers=None):
        captured.update({"url": url, "params": params, "headers": headers})
        return {
            "data": [
                {
                    "paperId": "cvpr-paper",
                    "title": "Vision paper",
                    "authors": [],
                    "venue": "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
                }
            ]
        }

    monkeypatch.setattr(source, "get_json", fake_get_json)
    papers = await source.search("CVPR", 5, SearchField.VENUE)

    assert captured["url"].endswith("/paper/search/bulk")
    assert captured["params"]["venue"] == "CVPR"
    assert papers[0].external_id == "cvpr-paper"


@pytest.mark.asyncio
async def test_semantic_scholar_resolves_author_entity_and_affiliation(
    settings, monkeypatch
) -> None:
    source = SemanticScholarSource(settings)
    calls = []

    async def fake_get_json(url, params, headers=None):
        calls.append((url, params, headers))
        if url.endswith("/author/search"):
            return {
                "data": [
                    {
                        "authorId": "A1",
                        "name": "Wei Wang",
                        "affiliations": ["Shenzhen University"],
                    },
                    {
                        "authorId": "A2",
                        "name": "Wei Zhang",
                        "affiliations": ["Shenzhen University"],
                    },
                ]
            }
        return {
            "data": [
                {
                    "paperId": "paper-1",
                    "title": "Vision Agents",
                    "authors": [{"authorId": "A1", "name": "Wei Wang"}],
                    "venue": "CVPR",
                }
            ]
        }

    monkeypatch.setattr(source, "get_json", fake_get_json)
    papers = await source.search("Wei Wang", 10, SearchField.AUTHOR)

    assert len(calls) == 2
    assert calls[1][0].endswith("/author/A1/papers")
    assert papers[0].author_affiliations == {
        "Wei Wang": ["Shenzhen University"]
    }


@pytest.mark.asyncio
async def test_semantic_scholar_paginates_author_papers_before_topic_filtering(
    settings, monkeypatch
) -> None:
    source = SemanticScholarSource(settings)
    calls = []

    async def fake_get_json(url, params, headers=None):
        calls.append((url, params, headers))
        if url.endswith("/author/search"):
            return {"data": [{"authorId": "A1", "name": "Wei Wang", "affiliations": []}]}
        if params["offset"] == 0:
            return {
                "data": [
                    {
                        "paperId": "unrelated",
                        "title": "Database indexing",
                        "authors": [{"name": "Wei Wang"}],
                    }
                ],
                "next": 1,
            }
        return {
            "data": [
                {
                    "paperId": "target",
                    "title": "Reliable Vision Agents",
                    "authors": [{"name": "Wei Wang"}],
                    "venue": "CVPR",
                }
            ]
        }

    monkeypatch.setattr(source, "get_json", fake_get_json)

    papers = await source.search_author(
        "Wei Wang", 10, affiliation=None, topic="vision agents", venue="CVPR"
    )

    assert [paper.external_id for paper in papers] == ["target"]
    assert [call[1]["offset"] for call in calls[1:]] == [0, 1]


@pytest.mark.asyncio
async def test_semantic_scholar_shares_scan_budget_and_limits_concurrency(
    settings, monkeypatch
) -> None:
    source = SemanticScholarSource(settings)
    paper_limits = []
    active = 0
    max_active = 0

    async def fake_get_json(url, params, headers=None):
        nonlocal active, max_active
        if url.endswith("/author/search"):
            return {
                "data": [
                    {"authorId": f"A{index}", "name": "Wei Wang", "affiliations": []}
                    for index in range(20)
                ]
            }
        active += 1
        max_active = max(max_active, active)
        paper_limits.append(params["limit"])
        await asyncio.sleep(0)
        active -= 1
        return {"data": []}

    monkeypatch.setattr(source, "get_json", fake_get_json)

    papers = await source.search_author(
        "Wei Wang", 100, affiliation=None, topic="vision", venue=None
    )

    assert papers == []
    assert sum(paper_limits) == 1000
    assert max_active <= 5


@pytest.mark.asyncio
async def test_semantic_scholar_cancels_queued_author_requests_after_failure(
    settings, monkeypatch
) -> None:
    source = SemanticScholarSource(settings)
    started_author_requests = []

    async def fake_get_json(url, params, headers=None):
        if url.endswith("/author/search"):
            return {
                "data": [
                    {"authorId": f"A{index}", "name": "Wei Wang", "affiliations": []}
                    for index in range(20)
                ]
            }
        author_id = url.split("/author/", 1)[1].split("/", 1)[0]
        started_author_requests.append(author_id)
        if author_id == "A0":
            raise SourceHttpError(429, "rate limited", retry_after_seconds=60)
        await asyncio.sleep(0.05)
        return {"data": []}

    monkeypatch.setattr(source, "get_json", fake_get_json)

    with pytest.raises(SourceHttpError) as captured:
        await source.search_author(
            "Wei Wang", 100, affiliation=None, topic="vision", venue=None
        )

    assert captured.value.status_code == 429
    assert len(started_author_requests) <= 6


@pytest.mark.asyncio
async def test_doi_search_drops_non_matching_results() -> None:
    source = MemorySource(
        [
            Paper(
                title="Wrong DOI",
                authors=[],
                year=2026,
                abstract="",
                sources=["memory"],
                external_id="wrong-doi",
                doi="10.9999/wrong",
            )
        ]
    )

    result = await PaperSearchService([source]).search(
        "10.1145/exact", selected=["memory"], field=SearchField.DOI
    )

    assert result.papers == []


@pytest.mark.asyncio
async def test_doi_search_rejects_empty_identifier_and_deduplicates_forms() -> None:
    missing = Paper(
        title="Missing DOI",
        authors=[],
        year=2026,
        abstract="",
        sources=["memory"],
        external_id="missing",
    )
    first = replace(missing, title="First DOI form", doi="10.1000/example")
    second = replace(
        missing,
        title="Second DOI form",
        doi="https://doi.org/10.1000/example",
        sources=["second"],
    )

    with pytest.raises(ValueError, match="DOI 检索值不能为空"):
        await PaperSearchService([MemorySource([missing])]).search(
            "doi:", field=SearchField.DOI
        )
    with pytest.raises(ValueError, match="有效 DOI"):
        await PaperSearchService([MemorySource([missing])]).search(
            "not-a-doi", field=SearchField.DOI
        )
    duplicate_result = await PaperSearchService(
        [MemorySource([first, second])]
    ).search("10.1000/example", field=SearchField.DOI)

    assert len(duplicate_result.papers) == 1
    assert duplicate_result.papers[0].sources == ["memory", "second"]


@pytest.mark.asyncio
async def test_single_source_can_fill_requested_limit_above_thirty() -> None:
    papers = [
        Paper(
            title=f"Research agents study {index}",
            authors=[],
            year=2026,
            abstract="",
            sources=["memory"],
            external_id=str(index),
        )
        for index in range(100)
    ]

    result = await PaperSearchService([MemorySource(papers)]).search(
        "research agents", limit=100
    )

    assert len(result.papers) == 100


@pytest.mark.asyncio
async def test_ieee_field_parameters_and_key_status(settings, monkeypatch) -> None:
    missing_catalog = PaperSearchService.default(settings).catalog()
    missing_ieee = next(item for item in missing_catalog if item["name"] == "ieee")
    assert "未配置" in missing_ieee["status"]
    skipped = await PaperSearchService.default(settings).search(
        "agents", selected=["ieee"]
    )
    assert skipped.failures[0].reason == "IEEE API Key 未配置，已自动跳过"

    configured_settings = replace(settings, ieee_xplore_api_key="test-key")
    source = IeeeSource(configured_settings)
    captured = {}

    async def fake_get_json(url, params, headers=None):
        captured.update({"url": url, "params": params, "headers": headers})
        return {"articles": []}

    monkeypatch.setattr(source, "get_json", fake_get_json)
    await source.search("Geoffrey Hinton", 5, SearchField.AUTHOR)

    assert captured["params"]["author"] == "Geoffrey Hinton"
    configured_ieee = PaperSearchService([source]).catalog()[0]
    assert "已配置" in configured_ieee["status"]
    assert "首次检索" in configured_ieee["status"]


@pytest.mark.asyncio
async def test_ieee_pushes_combined_author_filters_and_accumulates_affiliations(
    settings, monkeypatch
) -> None:
    source = IeeeSource(replace(settings, ieee_xplore_api_key="test-key"))
    captured = {}

    async def fake_get_json(url, params, headers=None):
        captured.update({"url": url, "params": params, "headers": headers})
        return {
            "articles": [
                {
                    "article_number": "1",
                    "title": "Vision Agents",
                    "publication_title": "CVPR",
                    "authors": {
                        "authors": [
                            {
                                "full_name": "Wei Wang",
                                "affiliation": "Shenzhen University",
                            },
                            {
                                "full_name": "Wei Wang",
                                "affiliation": "Tsinghua University",
                            },
                        ]
                    },
                }
            ]
        }

    monkeypatch.setattr(source, "get_json", fake_get_json)
    papers = await source.search_author(
        "Wei Wang",
        100,
        affiliation="Shenzhen University",
        topic="vision agents",
        venue="CVPR",
    )

    assert captured["params"]["author"] == "Wei Wang"
    assert captured["params"]["affiliation"] == "Shenzhen University"
    assert captured["params"]["querytext"] == "vision agents"
    assert captured["params"]["publication_title"] == (
        "Computer Vision and Pattern Recognition"
    )
    assert papers[0].author_affiliations == {
        "Wei Wang": ["Shenzhen University", "Tsinghua University"]
    }


def test_external_url_normalization_rejects_terminal_controls() -> None:
    assert normalize_http_url("https://example.org/a b?q=科研") == (
        "https://example.org/a%20b?q=%E7%A7%91%E7%A0%94"
    )
    assert normalize_http_url("https://example.org/\x1b]8;;https://evil.example\x07") is None
    assert normalize_http_url("https://-invalid.example/paper") is None


@pytest.mark.asyncio
async def test_acm_source_filters_crossref_prefix(settings, monkeypatch) -> None:
    source = AcmMetadataSource(settings)
    captured = {}

    async def fake_get_json(url, params, headers=None):
        captured.update({"url": url, "params": params, "headers": headers})
        return {
            "message": {
                "items": [
                    {
                        "DOI": "10.1145/1234567",
                        "title": ["Open ACM paper"],
                        "published": {"date-parts": [[2026]]},
                        "URL": "https://doi.org/10.1145/1234567",
                    }
                ]
            }
        }

    monkeypatch.setattr(source, "get_json", fake_get_json)
    papers = await source.search("research agents", 5)
    assert captured["params"]["filter"] == "prefix:10.1145"
    assert papers[0].is_open_access is False

    await source.search("NeurIPS", 5, SearchField.VENUE)
    assert captured["params"]["query.container-title"] == (
        "Neural Information Processing Systems"
    )
    assert captured["params"]["filter"] == "prefix:10.1145"
