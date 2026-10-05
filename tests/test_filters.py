import pytest

from src.filters.function import match_functions
from src.filters.location import is_germany
from src.filters.pipeline import evaluate
from src.filters.seniority import classify_seniority
from tests.conftest import make_job


@pytest.mark.parametrize("title,level", [
    ("Head of Regulatory Reporting", "strong"),
    ("Bereichsleiterin Risikocontrolling (m/w/d)", "strong"),
    ("Abteilungsleitung Gesamtbanksteuerung", "strong"),
    ("Teamleiter Treasury", "strong"),
    ("Risikobereichsleiter Kredit", "strong"),          # compound word
    ("Team Lead Finance Data", "strong"),
    ("Executive Director, Capital Management", "strong"),
    ("Senior Regulatory Reporting Specialist", "excluded"),
    ("Senior Expert Risk Models", "excluded"),
    ("Consultant Finance", "excluded"),
    ("Werkstudent Controlling", "excluded"),
    ("Praktikant Risikomanagement", "excluded"),
    ("Junior Head of Something", "excluded"),           # hard exclusion beats leadership word
    ("Head of Expert Network", "strong"),               # soft exclusion overridden by leadership
    ("Senior Manager Regulatory Reporting", "ambiguous"),
    ("Anleitung Kreditprozesse", "none"),               # compound false friend
    ("Projektleiter Regulatory Change", "ambiguous"),
    ("Senior Accountant", "none"),
])
def test_seniority_levels(settings, title, level):
    assert classify_seniority(title, "", settings["filters"]).level == level


def test_senior_alone_is_not_leadership(settings):
    assert classify_seniority("Senior Risk Controller", "", settings["filters"]).level == "none"


def test_ambiguous_management_signal_from_description(settings):
    r = classify_seniority("Senior Manager Regulatory Reporting", "Mit disziplinarischer Führung von 5 Mitarbeitern", settings["filters"])
    assert r.level == "ambiguous" and r.management_signal


@pytest.mark.parametrize("loc,ok", [
    ("Frankfurt am Main", True), ("Köln", True), ("Cologne", True), ("München", True), ("Wiesbaden, Hessen", True),
    ("Germany", True), ("Deutschland", True), ("Frankfurt / London", True),
    ("Amsterdam", False), ("London", False), ("Paris", False), ("Luxembourg", False), ("Zurich", False),
    ("Vienna", False), ("Warsaw", False), ("Brussels", False), ("London, Germany", True), ("", False),
])
def test_location(settings, bank, loc, ok):
    assert is_germany(make_job(bank, "Head of Risk", location=loc), bank, settings["location"])[0] is ok


def test_location_country_field_wins(settings, bank):
    j = make_job(bank, "Head of Risk", location="Frankfurt", country="NL")
    assert not is_germany(j, bank, settings["location"])[0]
    j = make_job(bank, "Head of Risk", location="", country="DE")
    assert is_germany(j, bank, settings["location"])[0]


def test_unknown_location_uses_title_or_description(settings, bank):
    j = make_job(bank, "Head of Risk", location="", description="Position in Deutschland")
    assert is_germany(j, bank, settings["location"])[0]


def test_germany_only_source_skips_location_check(settings, de_bank):
    assert is_germany(make_job(de_bank, "Head of Risk", location="Somewhere odd"), de_bank, settings["location"])[0]


def test_function_matching(settings, bank):
    f = lambda t, **k: match_functions(make_job(bank, t, **k), settings["filters"])
    assert f("Head of Regulatory Reporting")
    assert f("Leiter Risikocontrolling")          # compound via substring
    assert f("Head of FP&A")
    assert f("Director ICAAP")
    assert not f("Head of Sales")
    assert not f("Head of Capital Markets Sales")  # irrelevant phrase stripped
    assert not f("Head of Procurement", description="Risk once")  # one description hit < threshold
    assert f("Head of Operations", department="Risk Management")


def test_organisation_and_it_functions(settings, bank):
    f = lambda t, **k: match_functions(make_job(bank, t, **k), settings["filters"])
    assert f("Abteilungsleiter IT-Betrieb (m/w/d)")
    assert f("Head of IT Security")
    assert f("Bereichsleiter Organisation und Prozessmanagement")
    assert f("Leiter Informationssicherheit")
    assert f("Head of Digital Transformation")
    assert not f("Head of Sales")
    assert evaluate(make_job(bank, "Teamleiter IT-Infrastruktur (m/w/d)"), bank, settings).accepted
    assert not evaluate(make_job(bank, "Werkstudent IT"), bank, settings).accepted


def test_pipeline_accepts_and_rejects(settings, bank):
    ok = evaluate(make_job(bank, "Head of Regulatory Reporting"), bank, settings)
    assert ok.accepted and ok.germany and ok.leadership and ok.relevant
    assert not evaluate(make_job(bank, "Head of Regulatory Reporting", location="London"), bank, settings).accepted
    assert not evaluate(make_job(bank, "Head of Sales"), bank, settings).accepted
    assert not evaluate(make_job(bank, "Senior Specialist Regulatory Reporting"), bank, settings).accepted


def test_pipeline_borderline_policy(settings, bank):
    job = make_job(bank, "Senior Manager Regulatory Reporting")
    d = evaluate(job, bank, settings)
    assert d.accepted and d.borderline            # default policy: include
    s2 = {**settings, "filters": {**settings["filters"], "borderline_policy": "reject"}}
    assert not evaluate(job, bank, s2).accepted
    with_sig = make_job(bank, "Senior Manager Regulatory Reporting", description="Personalverantwortung für 6 Mitarbeiter")
    d = evaluate(with_sig, bank, s2)
    assert d.accepted and not d.borderline


class FakeLlm:
    min_confidence = 0.7

    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    def available(self):
        return True

    def classify(self, job):
        self.calls += 1
        return self.verdict


def test_llm_used_only_for_borderline(settings, bank):
    from src.filters.llm import LlmVerdict
    llm = FakeLlm(LlmVerdict(True, True, True, 0.9, "manages team"))
    evaluate(make_job(bank, "Head of Regulatory Reporting"), bank, settings, llm)   # clear -> no LLM
    evaluate(make_job(bank, "Praktikant Risk"), bank, settings, llm)                  # clear reject -> no LLM
    assert llm.calls == 0
    d = evaluate(make_job(bank, "Senior Manager Regulatory Reporting"), bank, settings, llm)
    assert llm.calls == 1 and d.accepted and d.used_llm
    llm2 = FakeLlm(LlmVerdict(False, True, True, 0.95, "individual contributor"))
    assert not evaluate(make_job(bank, "Senior Manager Regulatory Reporting"), bank, settings, llm2).accepted


def test_llm_cannot_override_foreign_country(settings, bank):
    from src.filters.llm import LlmVerdict
    llm = FakeLlm(LlmVerdict(True, True, True, 0.99, "x"))
    assert not evaluate(make_job(bank, "Head of Risk", location="London"), bank, settings, llm).accepted
    assert llm.calls == 0


def test_llm_json_parsing():
    from src.filters.llm import LlmClassifier
    v = LlmClassifier.parse('Sure: {"leadership_role": true, "relevant_function": false, "germany": true, "confidence": 0.8, "reason": "r"}')
    assert v.leadership_role and not v.relevant_function and not v.accepts(0.5)


def test_plain_manager_needs_management_signal(settings, bank):
    assert classify_seniority("(Senior) Bank Account Manager*in", "", settings["filters"]).level == "weak"
    # function only in department/category must not rescue a plain Manager
    j = make_job(bank, "(Senior) Bank Account Manager*in", department="Finance")
    assert not evaluate(j, bank, settings).accepted
    assert not evaluate(make_job(bank, "Manager Regulatory Reporting"), bank, settings).accepted
    ok = evaluate(make_job(bank, "Manager Regulatory Reporting", description="Mit Personalverantwortung für 4 Mitarbeitende"), bank, settings)
    assert ok.accepted and not ok.borderline


def test_ambiguous_needs_function_in_title(settings, bank):
    assert not evaluate(make_job(bank, "Senior Tax Manager", department="Finance"), bank, settings).accepted   # weak title
    assert not evaluate(make_job(bank, "Senior Manager Tax", department="Finance"), bank, settings).accepted  # function only in department
    d = evaluate(make_job(bank, "Senior Manager Finanzcontrolling (m/w/d)"), bank, settings)
    assert d.accepted and d.borderline
    # configurable: department match allowed when the flag is off
    s2 = {**settings, "filters": {**settings["filters"], "ambiguous_requires_title_function": False}}
    assert evaluate(make_job(bank, "Senior Manager Tax", department="Finance"), bank, s2).accepted


def test_bank_option_accepts_unknown_location(settings, bank):
    j = make_job(bank, "Leiter Fondsbuchhaltung", location="")
    assert not is_germany(j, bank, settings["location"])[0]
    bank.options = {"accept_unknown_location": True}
    assert is_germany(j, bank, settings["location"])[0]
    # but a known foreign location is still rejected
    assert not is_germany(make_job(bank, "Leiter X", location="Luxembourg"), bank, settings["location"])[0]


def test_plain_manager_with_risk_or_it_in_title_is_borderline(settings, bank):
    for title in ("(Senior) Information Risk and Security Manager*in",
                  "Category Project Manager IT Services", "Risk Manager (m/w/d)"):
        d = evaluate(make_job(bank, title), bank, settings)
        assert d.accepted and d.borderline, title
    # other functions and non-Risk/IT words stay out: no flood of fund/investment/account managers
    for title in ("Fund Manager – Renewables and Infrastructure", "(Senior) Investment Manager*in Infrastructure Investments",
                  "(Senior) Bank Account Manager*in", "Manager Regulatory Reporting", "Praktikant IT Manager"):
        assert not evaluate(make_job(bank, title), bank, settings).accepted, title
    # the department alone does not count, and the feature can be switched off
    assert not evaluate(make_job(bank, "Customer Manager", department="IT"), bank, settings).accepted
    off = {**settings, "filters": {**settings["filters"], "weak_borderline_functions": []}}
    assert not evaluate(make_job(bank, "IT-Application Manager*in Compliance"), bank, off).accepted


def test_german_digital_transformation_counts_as_function(settings, bank):
    d = evaluate(make_job(bank, "Gruppenleiter*in Digitale Transformation und Enabling"), bank, settings)
    assert d.accepted and not d.borderline


def test_lead_leiter_digital_titles_are_accepted(settings, bank):
    for title in ("Head of Digital Banking", "Digital Lead (m/w/d)", "Leiter*in Digitalisierung", "Teamleiter Digital Channels",
                  "Lead Digital Product Owner"):
        assert evaluate(make_job(bank, title), bank, settings).accepted, title
    # no leadership word -> still not accepted
    assert not evaluate(make_job(bank, "Digital Marketing Specialist"), bank, settings).accepted


def test_system_and_workflow_management_counts_as_function(settings, bank):
    d = evaluate(make_job(bank, "Abteilungsleiter (m/w/d) System- und Workflowmanagement"), bank, settings)
    assert d.accepted and not d.borderline


@pytest.mark.parametrize("title,ok", [
    ("Teamleiter/-in Anwendungsentwicklung (m/w/d)", False),
    ("Lead Java Backend Engineer (f/m/x)", False),
    ("Gruppenleiter Softwareentwicklung Java / Cloud", False),
    ("Director – Java Backend Engineering", False),
    ("Teamleiter Prozessautomatisierung (m/w/d)", True),
    ("Head of Digitalisierung (m/w/d)", True),
    ("Gruppenleiter Digitale Transformation und Enabling", True),
])
def test_technical_exclusions_keep_soft_it(settings, bank, title, ok):
    from src.filters.pipeline import evaluate
    assert evaluate(make_job(bank, title, location="Frankfurt"), bank, settings).accepted is ok


@pytest.mark.parametrize("title", [
    "IT Application Manager (w/m/d) Schwerpunkt SDM",
    "IT Service Continuity Manager (m/w/d)",
    "IT Customer Relationship Manager*in",
    "Teamleitung IT-Berechtigungen & Service Management (m/w/d)",
    "Teamleitung (m/w/d) IT-Berechtigungsmanagement",
])
def test_it_operations_titles_are_excluded(settings, bank, title):
    from src.filters.pipeline import evaluate
    assert not evaluate(make_job(bank, title, location="Frankfurt"), bank, settings).accepted


@pytest.mark.parametrize("title", ["Vice President Trade Finance Sales (m/w/d)", "Global Solution Sales Manager (Director)",
                                   "Head of Sales Operations"])
def test_sales_titles_are_excluded(settings, bank, title):
    from src.filters.pipeline import evaluate
    assert not evaluate(make_job(bank, title, location="Frankfurt"), bank, settings).accepted


def test_entwicklung_title_is_excluded_but_compounds_are_not(settings, bank):
    from src.filters.pipeline import evaluate
    assert not evaluate(make_job(bank, "Solution Architekt (w/m/d) – Lead Entwicklung JobRouter", location="Frankfurt"), bank, settings).accepted
    assert evaluate(make_job(bank, "Leiter Organisationsentwicklung und Digitalisierung", location="Frankfurt"), bank, settings).accepted


@pytest.mark.parametrize("title", [
    "Leitung Abteilung IT-Systemtechnik (m/w/d)", "Teamleiter/-in IT-Security & BCM (m/w/d)",
    "Gruppenleitung (w/m/d) IT Wertpapierabwicklung", "Manager (w/m/d) IT Strategie", "IT-Service-Manager (w/m/d)",
    "Senior Manager IT Service / Managed Workplace / Cloud m/w/d",
    "Account Manager (m/w/d) IT-Leasing Region Südwest", "Filialleitung (#Mensch) für Crivitz und Plate",
    "Abteilungsleiter*in Kaufmännisches und Infrastrukturelles Gebäudemanagement", "Regionalleiter (m/w/d) für unsere Region",
    "Lead Buyer IT Professional Services (m/w/d)",
])
def test_user_rejected_it_and_offtopic_titles_are_excluded(settings, bank, title):
    from src.filters.pipeline import evaluate
    assert not evaluate(make_job(bank, title, location="Frankfurt"), bank, settings).accepted


def test_soft_it_titles_survive_the_new_exclusions(settings, bank):
    from src.filters.pipeline import evaluate
    for title in ("Gruppenleiter*in Digitale Transformation und Enabling", "Abteilungsleiter Organisation und Digitalisierung (m/w/d)",
                  "Abteilungsleiter (m/w/d) System- und Workflowmanagement", "Leitung Controlling & Betriebssteuerung"):
        assert evaluate(make_job(bank, title, location="Frankfurt"), bank, settings).accepted, title


def test_regions_option_keeps_only_frankfurt_and_koeln_areas():
    from src.config import Bank
    from src.filters.location import is_germany
    from src.models.job import Job
    cfg = {"german_locations": ["Germany", "Frankfurt", "Berlin"], "foreign_locations": ["London"],
           "regions": {"rhein_main": ["Frankfurt", "Eschborn"], "koeln": ["Köln", "Leverkusen"]}}
    bank = Bank(id="x", name="X", germany_only=True, options={"regions": ["rhein_main", "koeln"]})

    def job(loc, title="Head of Risk"):
        return Job(bank_id="x", bank_name="X", title=title, url="https://x.test/1", location=loc)

    assert is_germany(job("Frankfurt am Main"), bank, cfg)[0]
    assert is_germany(job("Eschborn"), bank, cfg)[0]
    assert is_germany(job("Leverkusen"), bank, cfg)[0]
    assert not is_germany(job("Berlin"), bank, cfg)[0]
    assert is_germany(job("", title="Teamleiter Controlling Köln"), bank, cfg)[0]      # place named in the title
    assert not is_germany(job(""), bank, cfg)[0]                                      # unknown place: rejected ...
    bank.options["accept_unknown_location"] = True
    assert is_germany(job(""), bank, cfg)[0]                                          # ... unless the source is known to lack it
    assert not is_germany(job("Berlin"), bank, cfg)[0]                                # a known other city is still rejected
