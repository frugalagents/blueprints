from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from backend.domain.models import Candidate, LiveAwsEvidence

RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})


class AwsReadOnlyProvider:
    def __init__(self, region: str):
        self.region = region
        self.quotas = boto3.client(
            "service-quotas", region_name=region, config=RETRY_CONFIG
        )
        self.pricing = boto3.client(
            "pricing", region_name="us-east-1", config=RETRY_CONFIG
        )

    @lru_cache(maxsize=8)
    def endpoint_quotas(self) -> dict[str, dict[str, Any]]:
        paginator = self.quotas.get_paginator("list_service_quotas")
        found: dict[str, dict[str, Any]] = {}
        for page in paginator.paginate(
            ServiceCode="sagemaker", PaginationConfig={"PageSize": 100}
        ):
            for quota in page.get("Quotas", []):
                name = quota.get("QuotaName", "")
                match = re.search(
                    r"(ml\.[a-z0-9.]+) for endpoint usage", name, re.IGNORECASE
                )
                if match:
                    found[match.group(1).lower()] = {
                        "value": quota.get("Value"),
                        "code": quota.get("QuotaCode"),
                        "adjustable": quota.get("Adjustable", False),
                        "name": name,
                    }
        return found

    @lru_cache(maxsize=128)
    def endpoint_hourly_price(self, instance_type: str) -> tuple[float | None, str]:
        response = self.pricing.get_products(
            ServiceCode="AmazonSageMaker",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": self.region},
                {
                    "Type": "TERM_MATCH",
                    "Field": "instanceType",
                    "Value": instance_type,
                },
            ],
            MaxResults=100,
        )
        candidates = []
        for raw in response.get("PriceList", []):
            product = json.loads(raw)
            attributes = product.get("product", {}).get("attributes", {})
            component = (attributes.get("component") or "").lower()
            usage = (attributes.get("usagetype") or "").lower()
            platform = (attributes.get("platoinstancetype") or "").lower()
            if "studio" in component or "studio" in usage or "studio" in platform:
                continue
            for term in product.get("terms", {}).get("OnDemand", {}).values():
                for dimension in term.get("priceDimensions", {}).values():
                    if dimension.get("unit") != "Hrs":
                        continue
                    usd = dimension.get("pricePerUnit", {}).get("USD")
                    if usd is not None:
                        candidates.append(
                            (
                                float(usd),
                                dimension.get("description", "AWS Price List API"),
                            )
                        )
        if not candidates:
            return None, (
                "No unambiguous SageMaker endpoint-hosting price was returned; "
                "Studio prices were rejected."
            )
        return min(candidates, key=lambda item: item[0])

    def enrich_candidates(self, candidates: list[Candidate]) -> list[Candidate]:
        try:
            quotas = self.endpoint_quotas()
        except (BotoCoreError, ClientError) as error:
            quotas = {}
            quota_error = str(error)
        else:
            quota_error = ""

        def enrich(candidate: Candidate) -> Candidate:
            quota = quotas.get(candidate.instance.instance_type.lower())
            try:
                price, price_source = self.endpoint_hourly_price(
                    candidate.instance.instance_type
                )
                price_status = "verified" if price is not None else "unavailable"
            except (BotoCoreError, ClientError) as error:
                price = None
                price_source = str(error)
                price_status = "error"

            candidate.live_aws = LiveAwsEvidence(
                hourly_price_usd=price,
                monthly_price_usd=(
                    round(price * 730 * candidate.estimated_replicas, 2)
                    if price is not None
                    else None
                ),
                price_status=price_status,
                price_source=price_source,
                endpoint_quota=quota.get("value") if quota else None,
                quota_code=quota.get("code") if quota else None,
                quota_status=(
                    "verified"
                    if quota
                    else "error"
                    if quota_error
                    else "not-found"
                ),
            )
            if not quota:
                candidate.warnings.append(
                    "No applied endpoint quota was found for this instance type."
                )
            return candidate

        with ThreadPoolExecutor(max_workers=min(4, len(candidates) or 1)) as pool:
            return list(pool.map(enrich, candidates))
