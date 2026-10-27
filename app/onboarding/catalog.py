from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class RecipePart:
    path: str                                  # relative to ZETARIS_REPO_PATH
    include_kinds: tuple[str, ...] | None = None   # e.g. ("LIGHTNING_DATABASE", "REST_TABLE", "CACHE_TABLE")
    stop_after_contains: str | None = None     # stop after statement containing this text (inclusive)
    rate_limited: bool = False


@dataclass(frozen=True)
class Recipe:
    id: str
    description: str
    parts: tuple[RecipePart, ...]
    allowed_objects: tuple[str, ...]           # lowercase names this recipe may create
    needs_user_agent: bool = True
    source_count: dict | None = None           # how to count the source directly
    notes: tuple[str, ...] = ()


CATALOG: dict[str, Recipe] = {
    "ny_taxi_zone_audit": Recipe(
        id="ny_taxi_zone_audit", description="Read-only comparison of the two installed taxi-zone reference samples.",
        parts=(), allowed_objects=("ny_taxi.taxi_zones", "ny_taxi.zones"), needs_user_agent=False,
        notes=("Supplied demonstration data; results describe sample compatibility, not real operational incidents.",),
    ),
    "ny_taxi_payments": Recipe(
        id="ny_taxi_payments",
        description="Installed Zetaris NY Taxi trips and payment sample tables. Read-only reuse.",
        parts=(),
        allowed_objects=("ny_taxi.trips", "ny_taxi.trip_payments"),
        needs_user_agent=False,
        notes=("Sponsor sample data, not production customer records.",),
    ),
    "sec_data_database": Recipe(
        id="sec_data_database",
        description="SEC_DATA namespace required by the EDGAR submissions REST tables.",
        parts=(RecipePart(path="open_data/rest_apis/sql/rate_limited/01_edgar_company_facts_create.sql",
                          include_kinds=("LIGHTNING_DATABASE",),
                          stop_after_contains="CREATE LIGHTNING DATABASE SEC_DATA"),),
        allowed_objects=("sec_data",),
    ),
    # Steps 0-2 of 12_company_dns_create.sql (through CACHE TABLE company_dns.sic_codes_raw)
    "company_dns_v3_raw": Recipe(
        id="company_dns_v3_raw",
        description="SIC reference hierarchy (V3 raw REST table, cached). Expect 1005 SIC codes.",
        parts=(
            RecipePart(
                path="open_data/rest_apis/sql/rate_limited/12_company_dns_create.sql",
                stop_after_contains="CACHE TABLE company_dns.sic_codes_raw",
                rate_limited=True,
            ),
        ),
        allowed_objects=("company_dns", "sic_codes_raw"),
        source_count={"url": "https://company-dns.mediumroast.io/V3.0/na/sic/code/%25", "count_path": "data.sics"},
        notes=("V3 is deprecated; the /V3.0 path still answered on 2026-10-19 and may go away.",),
    ),
    # Step 0 and the seven *_submissions_raw REST tables of 11_edgar_company_profiles_create.sql
    "edgar_submissions_raw": Recipe(
        id="edgar_submissions_raw",
        description="SEC EDGAR submissions for 7 companies (raw REST tables).",
        parts=(
            RecipePart(
                path="open_data/rest_apis/sql/rate_limited/11_edgar_company_profiles_create.sql",
                include_kinds=("LIGHTNING_DATABASE", "REST_TABLE"),
                rate_limited=True,
            ),
        ),
        allowed_objects=(
            "sec_data",
            "apple_submissions_raw",
            "ford_submissions_raw",
            "ibm_submissions_raw",
            "oracle_submissions_raw",
            "target_submissions_raw",
            "tesla_submissions_raw",
            "walmart_submissions_raw",
        ),
        notes=("EDGAR requires a real User-Agent and allows ~10 requests/second.",),
    ),
    # open_data/usl/sql/02_sic_edgar_usl_create.sql (namespace, compile, activate x2, DQ)
    "sic_edgar_usl": Recipe(
        id="sic_edgar_usl",
        description="Two-table USL (company, sic_code) with an FK from company.sic to sic_code.",
        parts=(RecipePart(path="open_data/usl/sql/02_sic_edgar_usl_create.sql",
                          stop_after_contains="ACTIVATE USL TABLE lightning.metastore.usl_demo.sic_edgar_usl.company AS"),),
        allowed_objects=("lightning.metastore", "lightning.metastore.usl_demo", "sic_edgar_usl"),
        notes=("Foreign key data-quality rule checks company SIC against reference table.",),
    ),
}

DEPENDENCIES: dict[str, list[str]] = {
    "sic_edgar_usl": ["company_dns_v3_raw", "sec_data_database", "edgar_submissions_raw", "sic_edgar_usl"],
    "company_dns_v3_raw": ["company_dns_v3_raw"],
    "edgar_submissions_raw": ["sec_data_database", "edgar_submissions_raw"],
}
