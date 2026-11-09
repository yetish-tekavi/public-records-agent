"""detect_pii: candidate sensitive-information detection with Microsoft Presidio.

Presidio's built-in recognizers (spaCy NER for names, phonenumbers for phones, pattern
recognizers for licences/SSNs/emails) are extended with three pattern recognizers for
categories this project needs: US street addresses, NJ-format driver licence numbers with
spaces, and dates of birth (only when context words such as "DOB" appear nearby).

Generic DATE_TIME, ORGANIZATION and LOCATION entities are not requested: on the synthetic
records they flag timestamps, work-order numbers and facility names, not personal data.
"""

from __future__ import annotations

import logging
import os

from app.schemas import PiiDetectionResult, SensitiveCategory, SensitiveDataFinding

logger = logging.getLogger(__name__)
# Presidio warns about every non-English recognizer it skips; that is expected here.
logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)

DEFAULT_SPACY_MODEL = os.environ.get("PRA_SPACY_MODEL", "en_core_web_md")
DEFAULT_SCORE_THRESHOLD = 0.4

ENTITY_TO_CATEGORY: dict[str, SensitiveCategory] = {
    "PERSON": SensitiveCategory.PERSON_NAME,
    "PHONE_NUMBER": SensitiveCategory.PHONE_NUMBER,
    "US_DRIVER_LICENSE": SensitiveCategory.DRIVERS_LICENSE_NUMBER,
    "NJ_DRIVER_LICENSE": SensitiveCategory.DRIVERS_LICENSE_NUMBER,
    "US_SSN": SensitiveCategory.SOCIAL_SECURITY_NUMBER,
    "EMAIL_ADDRESS": SensitiveCategory.EMAIL_ADDRESS,
    "IP_ADDRESS": SensitiveCategory.IP_ADDRESS,
    "STREET_ADDRESS": SensitiveCategory.STREET_ADDRESS,
    "DATE_OF_BIRTH": SensitiveCategory.DATE_OF_BIRTH,
}

STREET_SUFFIXES = (
    "Street|St|Avenue|Ave|Road|Rd|Court|Ct|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Place|Pl|"
    "Terrace|Ter|Circle|Cir|Parkway|Pkwy|Highway|Hwy"
)


class PiiDetector:
    """Wraps a configured Presidio AnalyzerEngine. Construct once; loading spaCy takes seconds."""

    def __init__(self, spacy_model: str = DEFAULT_SPACY_MODEL, score_threshold: float = DEFAULT_SCORE_THRESHOLD) -> None:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp_engine = NlpEngineProvider(
            nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": spacy_model}]}
        ).create_engine()
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="STREET_ADDRESS",
                patterns=[Pattern("us_street_address", rf"\b\d{{1,5}}(?: [A-Z][a-z]+){{1,3}} (?:{STREET_SUFFIXES})\b", 0.6)],
                context=["address", "live", "reside", "calling from"],
            )
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="NJ_DRIVER_LICENSE",
                patterns=[Pattern("nj_dl_spaced", r"\b[A-Z]\d{4}[ -]\d{5}[ -]\d{5}\b", 0.6)],
                context=["license", "licence", "driver", "dl"],
            )
        )
        self.analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="DATE_OF_BIRTH",
                patterns=[Pattern("numeric_date", r"\b\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})\b", 0.2)],
                context=["dob", "birth", "born"],
            )
        )
        self.spacy_model = spacy_model
        self.score_threshold = score_threshold
        self.name = f"presidio-analyzer/{spacy_model}"

    def detect(self, text: str, record_id: str | None = None) -> PiiDetectionResult:
        results = self.analyzer.analyze(
            text=text, language="en", entities=list(ENTITY_TO_CATEGORY), score_threshold=self.score_threshold
        )
        # Resolve overlaps: keep the higher-scoring span, then the longer one.
        kept = []
        for r in sorted(results, key=lambda r: (-r.score, -(r.end - r.start), r.start)):
            if all(r.end <= k.start or r.start >= k.end for k in kept):
                kept.append(r)
        kept.sort(key=lambda r: r.start)

        prefix = record_id or "TEXT"
        findings = [
            SensitiveDataFinding(
                finding_id=f"{prefix}-P{i:02d}",
                record_id=record_id,
                category=ENTITY_TO_CATEGORY[r.entity_type],
                text=text[r.start : r.end],
                start=r.start,
                end=r.end,
                detector=f"presidio:{r.entity_type}",
                confidence=round(float(r.score), 3),
            )
            for i, r in enumerate(kept, start=1)
        ]
        logger.info("detect_pii record=%s findings=%s", record_id, [f.category.value for f in findings])
        return PiiDetectionResult(
            record_id=record_id, detector=self.name, score_threshold=self.score_threshold, findings=findings
        )
