from src.utils.robots import RobotsRules

GUIDECOM = """User-agent: *
Allow: /jobportal/*/viewAusschreibungen.html
Allow: /jobportal/*/viewAusschreibung/*.html
Allow: /jobportal/*/rss
Allow: /jobportal/*/sitemap.xml
Disallow: /
"""


def test_wildcard_allow_beats_disallow_all():
    r = RobotsRules(GUIDECOM)
    base = "https://jobs.guidecom.de"
    assert r.allowed(base + "/jobportal/hal-administration/viewAusschreibungen.html")
    assert r.allowed(base + "/jobportal/hal-administration/rss")
    assert r.allowed(base + "/jobportal/hal-administration/viewAusschreibung/2023-022.html")
    assert not r.allowed(base + "/jobportal/hal-administration/admin")
    assert not r.allowed(base + "/other")


def test_longest_match_and_ties():
    r = RobotsRules("User-agent: *\nDisallow: /private/\nAllow: /private/public/\nDisallow: /tie\nAllow: /tie\n")
    assert r.allowed("https://x.test/private/public/a")
    assert not r.allowed("https://x.test/private/a")
    assert r.allowed("https://x.test/tie")            # equal length: Allow wins


def test_end_anchor_and_empty_disallow():
    r = RobotsRules("User-agent: *\nDisallow: /*.pdf$\n")
    assert not r.allowed("https://x.test/a/b.pdf")
    assert r.allowed("https://x.test/a/b.pdf.html")
    assert RobotsRules("User-agent: *\nDisallow:\n").allowed("https://x.test/anything")


def test_specific_agent_group_wins_and_errors_are_conservative():
    txt = "User-agent: bank-job-monitor\nDisallow: /jobs\n\nUser-agent: *\nDisallow:\n"
    assert not RobotsRules(txt, "bank-job-monitor/1.0 (x)").allowed("https://x.test/jobs/1")
    assert RobotsRules(txt, "otherbot/2.0").allowed("https://x.test/jobs/1")
    assert not RobotsRules(disallow_all=True).allowed("https://x.test/")


def test_existing_real_world_files():
    commerzbank_like = "User-agent: *\nDisallow: /kunde\nDisallow: /service/search/?*\nSitemap: https://x/s.xml\n"
    r = RobotsRules(commerzbank_like)
    assert r.allowed("https://x.test/konzern/karriere/")
    assert not r.allowed("https://x.test/service/search/?q=1")
