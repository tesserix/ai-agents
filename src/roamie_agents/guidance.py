from datetime import UTC, datetime

from roamie_agents.contracts import EntryDetails, Evidence, RecommendationRequest, Specialist
from roamie_agents.evidence import EvidenceBatch

# Navigation references only, never a cached assertion of visa rules or current prices.
IMMIGRATION = {
    "AU": "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-finder",
    "NZ": "https://www.immigration.govt.nz/",
    "GB": "https://www.gov.uk/check-uk-visa",
    "US": "https://travel.state.gov/content/travel/en/us-visas.html",
    "CA": "https://www.canada.ca/en/immigration-refugees-citizenship/services/visit-canada.html",
    "IN": "https://indianvisaonline.gov.in/",
    "JP": "https://www.mofa.go.jp/j_info/visit/visa/index.html",
    "SG": "https://www.ica.gov.sg/enter-transit-depart/entering-singapore",
    "TH": "https://www.thaievisa.go.th/",
}
CHECKLIST = (
    (
        "Check visa or entry authorization eligibility for your passport, "
        "residence, purpose, stay length and every transit country using the "
        "official immigration authority."
    ),
    (
        "Confirm application steps, documents, processing times and fees in the "
        "issuing authority currency before booking. Eligibility and fees have not"
        " been verified by Roamie."
    ),
    (
        "Check passport validity, blank pages, onward travel, accommodation and "
        "proof-of-funds requirements with the destination authority."
    ),
    (
        "Check customs, medication import permits, health and vaccination rules "
        "with the relevant official authorities; do not assume the same rules "
        "apply across destinations."
    ),
    (
        "Review local laws, driving permits, travel advisories, insurance "
        "exclusions and accessibility arrangements."
    ),
    "Check consent and documentation rules for children or other travel companions separately.",
    (
        "Recheck official requirements before applying and before departure; "
        "links alone are not confirmation that entry is permitted."
    ),
)


class EntryGuidanceSource:
    async def search(self, specialist: Specialist, request: RecommendationRequest) -> EvidenceBatch:
        if specialist != Specialist.ENTRY:
            return EvidenceBatch(status="unavailable")
        missing = tuple(
            name
            for name in (
                "destination_country",
                "passport_country",
                "residence_country",
                "start_date",
                "end_date",
            )
            if getattr(request, name) is None
        )
        url = IMMIGRATION.get(request.destination_country or "")
        if url is None:
            missing += ("official_destination_source",)
        return EvidenceBatch(
            status="ok",
            facts=[
                Evidence(
                    id="entry-planning",
                    name="Entry planning checklist — requirements and fees not yet verified",
                    category=Specialist.ENTRY,
                    source_url=url or "https://www.iatatravelcentre.com/",
                    observed_at=datetime.now(UTC),
                    entry=EntryDetails(
                        destination_country=request.destination_country,
                        passport_country=request.passport_country,
                        residence_country=request.residence_country,
                        checklist=CHECKLIST,
                        missing_information=(
                            *missing,
                            "passport-specific eligibility and current official fees",
                        ),
                    ),
                )
            ],
        )
