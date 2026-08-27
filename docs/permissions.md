# Paper-Source Permissions, Keys, and Compliance

**English** | [简体中文](./zh-CN/permissions.md)

## Principles

ScholarOS treats metadata search, abstract retrieval, open-full-text access, and subscription-full-text access as four separate capabilities. Finding a DOI or PDF link does not automatically grant rights to download, store, train on, or redistribute the content.

ScholarOS only provides research assistance. Search results, evidence summaries, method suggestions, and research drafts require human verification. The system cannot replace ethics approval, data-integrity checks, authorship decisions, or submission responsibility. Disclose AI use according to institutional and publication rules.

## Sources

### arXiv

- Uses the official Atom API by default.
- Returns preprint metadata, abstracts, landing pages, and PDF links.
- `journal_ref` is a complete publication reference rather than a normalized venue field, so strict venue search explicitly skips arXiv. Topic, title, and author search remain available.
- Verify the version, published record, and license before formal citation.

### OpenAlex

- Uses the open API by default; setting `SCHOLAROS_CONTACT_EMAIL` is recommended.
- Provides aggregated metadata, citation counts, and open-access status.
- Works authorships can represent per-author affiliations. Combined author search resolves Author, Institution, and Source entities, pushes supported constraints into Works retrieval, then verifies that the affiliation belongs to the matched person. Metadata may still be absent or stale.
- Venue search resolves a Source and its alternate or abbreviated names before querying Works.
- Aggregator links may change; formal citations should resolve to the DOI or publisher page.

### Crossref

- Enabled without registration; a `mailto` parameter and explicit User-Agent are recommended.
- Provides publisher-submitted DOI metadata. Some abstracts may remain copyrighted.
- Author, affiliation, topic, and container title can retrieve combined candidates. ScholarOS still verifies author–affiliation relationships because Crossref field queries are relevance retrieval, not exact identity matching.
- Production deployments should cache, handle 429/5xx responses, and apply exponential backoff. The MVP degrades after one failure.

### Semantic Scholar

- Author search resolves an Author entity and may use its affiliation for disambiguation. Topic and venue constraints paginate through the author's papers, scanning at most 1,000 candidates per search. Venue search uses the official venue filter.
- Anonymous shared capacity frequently returns 429. Apply for and configure an API key for production use.
- Requests may be attempted without a key; `SEMANTIC_SCHOLAR_API_KEY` is recommended for production.
- Follow current official quotas and terms.
- Treat `openAccessPdf` as a lead and verify the license before use.

### DBLP

- Uses the official publication-search JSON API.
- Primarily returns computer-science bibliography metadata and does not provide full-text entitlement.

### ACM

- The current `acm` source queries Crossref for ACM's `10.1145` DOI prefix.
- This is ACM publication metadata support, not an official public ACM Digital Library search API, and it does not scrape ACM pages.
- Follow ACM's current open-access policy and each paper's license. A DOI page or open-access marker does not grant ScholarOS permission for bulk storage, training, or redistribution.
- A future official or institutional connector should be a separate adapter; subscription cookies must not be placed in source code.
- University access to full text, premium features, or third-party publisher content depends on the institution's subscription and the current ACM page.
- ScholarOS does not read browser cookies or bulk-download subscription content. Confirm automation rights with the university library before integrating an official institutional or TDM interface.

### IEEE Xplore

- Register at the [IEEE Xplore API Portal](https://developer.ieee.org/), accurately describe the non-commercial research use and institutional affiliation, then wait for IEEE to issue and activate the application key.
- Put the key in the project-root `.env` as `IEEE_XPLORE_API_KEY=your_key`, then restart `scholaros` or `scholaros serve`. Without a key, IEEE remains visible as a source but is skipped with an explanation.
- `ieee.available=true` in the Web status or `/health` only proves that ScholarOS read a key; it does not prove activation. Rely on the activation email and the first real response. A 401/403 message identifies a pending, invalid, or unauthorized key. Never commit `.env` or the key.
- The Metadata Search API and open/subscription full-text APIs are separate permission layers.
- A university Web subscription does not automatically authorize API bulk full-text retrieval. Use the browser for individual articles covered by the subscription; automated retrieval requires separate confirmation from the library or IEEE.
- IEEE's current [API Terms of Use](https://developer.ieee.org/API_Terms_of_Use2) place restrictions on AI/LLM and data-mining use of Content. To avoid sending IEEE metadata or abstracts to a model without authorization, the current `ieee` adapter is available only in `scholaros search` and the Web cross-source search, not the idea-to-draft workflow.
- Add an explicit opt-in AI connector only after IEEE or the university library grants written permission for the specific research use. A university browser login alone is not sufficient.

### Google Scholar

- Google Scholar overlaps substantially with the current sources but is not identical. Its [official description](https://scholar.google.com/intl/engb/scholar/about.html) also includes theses, books, abstracts, repositories, and other scholarly Web pages.
- ScholarOS does not use Google Scholar as an automated source. [Google Scholar help](https://scholar.google.com/intl/us/scholar/help.html) does not provide bulk access and asks automated software to respect `robots.txt`; ScholarOS therefore provides only a manual supplemental search link.
- A manual search can use an existing university browser session to show subscription-access options, but it does not grant full-text automation or AI/TDM rights to ScholarOS.

## Source-failure guidance

Terminal, CLI, Web, and API responses report each failed source and place a suggested action next to the error. Other sources continue, so these messages mean that coverage may be incomplete—not that the entire search failed.

| Source | Common condition | Suggested action |
|---|---|---|
| arXiv | 429, 5xx, timeout | Reduce request frequency and retry later; temporarily disabling arXiv does not affect other sources. |
| OpenAlex | 429, 5xx, network error | Set `SCHOLAROS_CONTACT_EMAIL`, restart, and reduce frequency; retry after upstream recovery. |
| Crossref | 429, 403, 5xx | Set `SCHOLAROS_CONTACT_EMAIL` for polite usage; slow down on 429, follow the response/contact Crossref on 403, retry later on 5xx. |
| Semantic Scholar | 429, 401/403, 5xx | Apply for and set `SEMANTIC_SCHOLAR_API_KEY`; 429 is rate limiting, 401/403 may mean an invalid key, and 5xx should be retried later. |
| DBLP | 429, 503, timeout | Respect `Retry-After` and reduce frequency; 503 is temporary server unavailability, so retry later or disable DBLP temporarily. |
| ACM | Crossref 429/403/5xx | The ACM adapter currently uses Crossref; set `SCHOLAROS_CONTACT_EMAIL`, follow Crossref guidance, or disable ACM temporarily. |
| IEEE | Missing key, 401/403, 429, 5xx | Set `IEEE_XPLORE_API_KEY`; wait if the activation email still says `waiting`; wait for quota recovery on 429; retry 5xx later. |

Semantic Scholar documents that anonymous requests share public capacity and may be further limited during load; an API key provides dedicated capacity. See the [official API page](https://www.semanticscholar.org/product/api). Crossref recommends `mailto`, caching, and backoff after 429 in its [access and rate-limit guide](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/). DBLP documents protective online-API limits and recommends slower requests or datasets for bulk use in its [official FAQ](https://dblp.org/faq/1474706.html).

ScholarOS immediately degrades a failed source and does not repeatedly retry it within one request, avoiding extra load after 429 or upstream failure. A `retryable` flag means that a later retry is usually reasonable; it does not mean ScholarOS has already retried.

## University accounts and automated search

- A university login is not required to find papers. ScholarOS searches arXiv, OpenAlex, Crossref, DBLP, ACM metadata, and optional IEEE APIs.
- A university login can help read subscription full text. Sign in through the browser, then open the publisher page from a ScholarOS result so the browser can use the existing institutional session.
- Do not copy university passwords or browser cookies into the backend. It is unstable and may exceed the subscription's automation rights; ScholarOS intentionally does not implement it.

## Follow-up checklist

1. Apply for an IEEE Xplore API key and confirm `ieee.available=true` through `/health` after configuration.
2. Configure a Crossref contact email and Semantic Scholar key for production use.
3. Ask the university library about IEEE/ACM TDM, remote access, and storage terms.
4. If an institutional API exists, document connector permissions and audit logging before retrieving full text.
5. Add license fields and full-text cache-expiration policies; project and local-artifact deletion already exists.
6. Add ethics-approval gates for research involving participants, health, privacy, or unpublished material.
7. Keep AI disclosure, author confirmation, and final human sign-off as an external pre-submission process; software quality scores are not compliance evidence.

ScholarOS does not provide—and must not add—implementations that bypass paywalls, CAPTCHAs, robots restrictions, or institutional licenses.
